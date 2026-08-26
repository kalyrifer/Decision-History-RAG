"""Глубокая перепроверка Фазы 1: живой GitHub, якоря golden set, целостность ссылок."""

import json
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
import yaml

from config import GITHUB_TOKEN

RAW = Path("data/raw")


def load(name):
    return [json.loads(l) for l in (RAW / name).open(encoding="utf-8")]


issues = {r["number"]: r for r in load("issues.jsonl")}
prs = {r["number"]: r for r in load("prs.jsonl")}
commits = {r["native_id"]: r for r in load("commits.jsonl")}
augs = {}
for l in load("augmentations.jsonl"):
    augs[(l["kind"], l["number"])] = l

fails = []

# 1. Живые счётчики GitHub
h = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
for typ, mine in (("issue", len(issues)), ("pr", len(prs))):
    r = httpx.get(
        "https://api.github.com/search/issues",
        headers=h,
        params={"q": f"repo:pydantic/pydantic is:{typ}", "per_page": 1},
        timeout=20,
    )
    live = r.json()["total_count"]
    diff = abs(live - mine)
    status = "OK" if diff <= 30 else "FAIL"
    if status == "FAIL":
        fails.append(f"{typ}: расхождение {diff}")
    print(f"[{status}] GitHub is:{typ} = {live}, у нас = {mine} (расхождение {diff})")

# 2. Якоря golden set существуют и с деталями
gs = yaml.safe_load(Path("eval/golden_set.yaml").read_text(encoding="utf-8"))
for item in gs:
    for a in item["expected_anchors"]:
        num = a["number"]
        base = issues.get(num) or prs.get(num)
        det = augs.get(("issue", num)) or augs.get(("pr", num))
        if not base or not det:
            fails.append(f"golden #{num} не найден")
            print(f"[FAIL] якорь {item['id']} #{num}")
print(f"[OK] все якоря golden set ({len(gs)} вопросов) на месте" if not any('golden' in f for f in fails) else "")

# 3. Squash-merge: merge_commit присутствует в истории main
random.seed(42)
sample = random.sample([p for p in prs.values() if p.get("merge_commit")], min(300, len(prs)))
hit = sum(1 for p in sample if p["merge_commit"] in commits)
pct = hit / len(sample) * 100
print(f"[{'OK' if pct > 80 else 'WARN'}] merge_commit из API найден в git-логе: {hit}/{len(sample)} ({pct:.0f}%)")

# 4. Разрешимость timeline-ссылок
known_numbers = set(issues) | set(prs)
xrefs = resolved = 0
bad_targets = set()
for (kind, num), det in augs.items():
    for t in det["timeline"]:
        tn = t.get("target_number")
        if tn is None:
            continue
        tk = t.get("target_kind")
        xrefs += 1
        if (tk == "issue" and tn in issues) or (tk == "pr" and tn in prs):
            resolved += 1
        else:
            bad_targets.add((tk, tn))
pct = resolved / xrefs * 100 if xrefs else 0
print(f"[{'OK' if pct > 95 else 'WARN'}] timeline-ссылки разрешаются внутри корпуса: {resolved}/{xrefs} ({pct:.1f}%)")
if bad_targets:
    print(f"       примеры внешних целей: {sorted(bad_targets)[:5]}")

# 5. Даты и URL
try:
    for i in issues.values():
        datetime.fromisoformat(i["created_at"].replace("Z", "+00:00"))
    bad_urls = sum(1 for i in issues.values() if not i["url"].startswith("https://github.com/pydantic/pydantic/"))
    print(f"[{'OK' if bad_urls == 0 else 'FAIL'}] даты ISO, url корректны (плохих url: {bad_urls})")
except Exception as e:
    fails.append(f"даты: {e}")

# 6. Комментарии крупного треда (#619, 59 комм.) реально выгружены
big = augs.get(("issue", 619))
print(f"[{'OK' if big and len(big['comments']) >= 50 else 'FAIL'}] тред #619: комментариев {len(big['comments']) if big else 0}")

print("\nВЕРДИКТ:", "ФАЗА 1 ПОДТВЕРЖДЕНА" if not fails else f"ПРОБЛЕМЫ: {fails}")
