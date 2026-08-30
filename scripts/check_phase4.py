"""Приёмка Фазы 4: recall@10 якорей после fusion, эффект expansion, тайминги."""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml

from retrieve import pipeline
from retrieve.hybrid import get_model

gs = yaml.safe_load(open("eval/golden_set.yaml", encoding="utf-8"))

get_model()
print("модель прогрета, гоняю золотой набор...\n")

hits_fusion = 0
rescued = []
rows_out = []
times = []

for item in gs:
    anchors = {a["number"] for a in item["expected_anchors"]}
    r_noex = pipeline.search(item["question"], no_expand=True)
    times.append(r_noex["elapsed_s"])
    top10 = {r["number"] for r in r_noex["rows"][:10] if r["kind"] in ("issue", "pr")}
    hit = bool(top10 & anchors)
    hits_fusion += hit

    r_full = pipeline.search(item["question"])
    times.append(r_full["elapsed_s"])
    all_nums = {r["number"] for r in r_full["rows"]}
    late = sorted(anchors - top10)
    rescued_now = [n for n in late if n in all_nums]
    if rescued_now:
        rescued.append((item["id"], rescued_now))

    mark = "HIT " if hit else "MISS"
    rows_out.append(f"[{mark}] {item['id']}: fusion_top10={sorted(top10 & anchors) or '-'} "
                    f"rescued_by_graph={rescued_now or '-'} ({r_noex['elapsed_s']}с)")

print("\n".join(rows_out))
n = len(gs)
rate = hits_fusion / n * 100
avg_t = sum(times) / len(times)
print(f"\nrecall@10 (fusion): {hits_fusion}/{n} = {rate:.0f}%")
print(f"вытянуто графом после fusion: {len(rescued)} вопросов: {[x[0] for x in rescued]}")
print(f"среднее время поиска: {avg_t:.2f}с")
verdict = rate >= 80
print("ИТОГ ФАЗЫ 4:", "ПРИНЯТА" if verdict else f"НЕ ДОТЯНУТ порог 80% (сейчас {rate:.0f}%): расширить калибровку в Фазе 6")
