"""Сверка критериев приёмки Фазы 1."""

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAW = Path("data/raw")


def load_jsonl(name: str) -> tuple[list[dict], int]:
    recs = []
    bad = 0
    p = RAW / name
    if not p.exists():
        return recs, bad
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    return recs, bad


issues, bad_i = load_jsonl("issues.jsonl")
prs, bad_p = load_jsonl("prs.jsonl")
commits, bad_c = load_jsonl("commits.jsonl")
augs, bad_a = load_jsonl("augmentations.jsonl")

print(f"issues={len(issues)} (битых строк: {bad_i})")
print(f"prs={len(prs)} (битых строк: {bad_p})")
print(f"commits={len(commits)} (битых строк: {bad_c})")
print(f"augmentations={len(augs)} (битых строк: {bad_a})")

have = {(r["kind"], r["number"]) for r in augs}
want = {("issue", n) for n in {i["number"] for i in issues}} | {("pr", n) for n in {p["number"] for p in prs}}
missing = want - have
dupes = len(augs) - len(have)
print(f"покрытие деталями: {len(have)}/{len(want)}, отсутствуют: {len(missing)}, дублей записей: {dupes}")

merged = sum(1 for p in prs if p.get("merged_at"))
with_commits = sum(1 for r in augs if "commits" in r)
with_reviews = sum(1 for r in augs if "reviews" in r)
n_comments = sum(len(r["comments"]) for r in augs)
n_timeline = sum(len(r["timeline"]) for r in augs)
xrefs = sum(1 for r in augs for t in r["timeline"] if t.get("target_number"))
sizes = {name: f"{(RAW / name).stat().st_size / 1e6:.1f} МБ" for name in
         ("issues.jsonl", "prs.jsonl", "augmentations.jsonl", "commits.jsonl")}

print(f"merged PR: {merged}, с коммитами: {with_commits}, с ревью: {with_reviews}")
print(f"комментариев: {n_comments}, timeline-событий: {n_timeline}, из них со связями на номер: {xrefs}")
print("размеры:", sizes)

ok = not missing and not bad_i and not bad_p and not bad_a and not bad_c and len(issues) > 5000 and len(prs) > 5000
print("\nИТОГ ФАЗЫ 1:", "ПРИНЯТА" if ok else "ЕСТЬ ПРОБЛЕМЫ")
