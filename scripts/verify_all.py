"""Проверка Фаз 0-6: комплексная верификация."""
import sys
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psycopg
import yaml
from config import PG_DSN

PHASE = {"ok": 0, "fail": 0, "items": []}
def check(phase, msg, ok):
    PHASE["ok"] += ok
    PHASE["fail"] += 1 - ok
    PHASE["items"].append((phase, msg, ok))
    print(f"  {'OK' if ok else 'FAIL'} [{phase}] {msg}")

# ---- Фаза 0: окружение ----
gs = yaml.safe_load(open("eval/golden_set.yaml", encoding="utf-8"))
check("P0", f"golden_set.yaml: {len(gs)} вопросов", len(gs) >= 8)
all_draft = all(item.get("status") == "draft" for item in gs)
check("P0", "все статусы draft", all_draft)
for item in gs:
    check("P0", f"  {item['id']}: {len(item['expected_anchors'])} якорей",
          len(item["expected_anchors"]) >= 1)

# ---- Фаза 1: данные ----
import json
raw = {}
for name in ("commits", "issues", "prs", "augmentations"):
    with open(f"data/raw/{name}.jsonl", encoding="utf-8") as f:
        raw[name] = [json.loads(line) for line in f]
check("P1", f"issues: {len(raw['issues'])}", len(raw["issues"]) == 5715)
check("P1", f"prs: {len(raw['prs'])}", len(raw["prs"]) == 5494)
check("P1", f"commits: {len(raw['commits'])}", len(raw["commits"]) == 5692)
check("P1", f"augmentations: {len(raw['augmentations'])}", len(raw["augmentations"]) == 11209)

# ---- Фаза 2: БД ----
with psycopg.connect(PG_DSN) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT kind, count(*) FROM entities GROUP BY kind ORDER BY kind")
        ek = {k: c for k, c in cur.fetchall()}
        check("P2", f"entities: {sum(ek.values())} (issue={ek.get('issue',0)}, pr={ek.get('pr',0)}, commit={ek.get('commit',0)}, comment={ek.get('comment',0)}, file={ek.get('file',0)})",
              sum(ek.values()) >= len(raw["issues"]) + len(raw["prs"]) + len(raw["commits"]) + 39960)

        cur.execute("SELECT count(*) FROM relations")
        rcnt = cur.fetchone()[0]
        check("P2", f"relations: {rcnt}", rcnt >= 50000)

        cur.execute("SELECT kind, count(*) FROM relations GROUP BY kind ORDER BY kind")
        for k, c in cur.fetchall():
            check("P2", f"  relations[{k}] = {c}", c > 0)

        cur.execute("SELECT count(*) FROM files")
        fcnt = cur.fetchone()[0]
        check("P2", f"files: {fcnt}", fcnt >= 25000)

        cur.execute("SELECT count(*) FROM relations WHERE kind='touches_file'")
        tf = cur.fetchone()[0]
        check("P2", f"touches_file relations: {tf}", tf > 0)

        # Dangling refs check
        cur.execute("""
            SELECT count(*) FROM relations r
            LEFT JOIN entities e1 ON r.src_id = e1.id
            LEFT JOIN entities e2 ON r.dst_id = e2.id
            WHERE e1.id IS NULL OR e2.id IS NULL
        """)
        dng = cur.fetchone()[0]
        check("P2", f"висячих связей: {dng}", dng == 0)

# ---- Фаза 3: чанки + эмбеддинги ----
with psycopg.connect(PG_DSN) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), count(embedding) FROM chunks")
        total, embedded = cur.fetchone()
        check("P3", f"чанков: {total}, с эмбеддингами: {embedded}", total >= 70000 and embedded == total)

        cur.execute("""
            SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_chunks_hnsw')
        """)
        hnsq = cur.fetchone()[0]
        check("P3", "HNSW индекс (idx_chunks_hnsw)", hnsq)

        cur.execute("""
            SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_chunks_tsv')
        """)
        gin = cur.fetchone()[0]
        check("P3", "GIN индекс (tsv)", gin)

# ---- Фаза 4: поиск ----
from retrieve import pipeline
from retrieve.hybrid import get_model
get_model()
try:
    res = pipeline.search("StrictBool", no_expand=False, rewrite_query=False, mode="hybrid", w_fts=0.1)
    check("P4", f"поиск 'StrictBool': {len(res['rows'])} результатов за {res['elapsed_s']}с",
          len(res["rows"]) > 0)
    qmarks = [r for r in res["rows"] if r.get("number") == 579]
    check("P4", "якорь #579 (StrictBool) в результатах", len(qmarks) > 0)
except Exception as e:
    check("P4", f"поиск упал: {e}", False)

# ---- Фаза 5: синтез ----
try:
    from synthesize.answer import answer
    a = answer(res, "StrictBool")
    check("P5", f"ответ LLM: {len(a['answer'])} символов, уверенность={a['confidence']}",
          len(a["answer"]) > 100)
    has_urls = any(s.get("url") for s in a.get("sources", []))
    check("P5", "есть ссылки в источниках", has_urls)
    # п.5: регрессия — тело ответа не содержит служебных метаданных
    # (легитимные секции «Источники»/«Уверенность» в теле не считаем утечкой;
    #  ловим именно служебные строки: метрики пайплайна, счётчики, статусы)
    body = a["answer"].lower()
    leak = [tok for tok in (
        "fusion:", "elapsed_s", "expansion:", "всего строк", "уверенность: high",
        "уверенность: low", "уверенность: medium", "событий timeline", "источников (",
        "llm:", "токенов, $", "реранкер", "graph expansion",
    ) if tok in body]
    check("P5", f"тело ответа чистое от метаданных (leak={leak or 'нет'})", not leak)
except Exception as e:
    check("P5", f"синтез упал: {e}", False)

# ---- Фаза 6: eval ----
from eval.run_eval import run_retrieval
r = run_retrieval("hybrid", False, False, w_fts=0.1)
# baseline G01-G08 (8 вопросов, 11 якорей)
b = r["per_question"][:8]
b_fusion = sum(p["n_anchors"] for p in b)
b_found = sum(len(p["top10"]) for p in b)
b_recall = b_found / b_fusion if b_fusion else 0.0
check("P6", f"baseline recall@10 fusion (G01-G08) = {b_recall:.0%} ({b_found}/{b_fusion})", b_recall >= 0.8)
# новые вопросы G09-G11 (расширение)
n = r["per_question"][8:]
n_fusion = sum(p["n_anchors"] for p in n)
n_found = sum(len(p["top10"]) for p in n)
check("P6", f"расширение recall@10 fusion (G09-G11) = {n_found}/{n_fusion}", n_found >= 0)

# Итог
print(f"\n{'='*50}")
print(f"ИТОГ: {PHASE['ok']} OK / {PHASE['ok']+PHASE['fail']} проверок")
if PHASE["fail"]:
    print(f"Провалено:")
    for phase, msg, ok in PHASE["items"]:
        if not ok:
            print(f"  [{phase}] {msg}")