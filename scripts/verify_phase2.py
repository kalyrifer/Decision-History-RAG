"""Сверка критериев приёмки Фазы 2."""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

from config import GITHUB_TOKEN, PG_DSN

import psycopg


def log(m):
    print(m, flush=True)


fails = []

with psycopg.connect(PG_DSN, cursor_factory=psycopg.ClientCursor) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT kind, count(*) FROM entities GROUP BY kind")
        ents = dict(cur.fetchall())
        cur.execute("SELECT kind, source, count(*) FROM relations GROUP BY kind, source ORDER BY kind, source")
        rels = {(k, s): c for k, s, c in cur.fetchall()}
        cur.execute("SELECT count(*) FROM files")
        n_files = cur.fetchone()[0]

        cur.execute(
            "SELECT count(*) FROM entities e WHERE e.kind='comment' AND NOT EXISTS "
            "(SELECT 1 FROM relations r WHERE r.src_id=e.id AND r.kind='parent')"
        )
        orphan_comments = cur.fetchone()[0]

        cur.execute(
            "SELECT count(DISTINCT p.native_id) FROM entities p "
            "JOIN entities m ON m.native_id = (p.extra->>'merge_commit') "
            "JOIN relations r ON r.src_id=p.id AND r.dst_id=m.id AND r.kind='pr_commit' "
            "WHERE p.kind='pr' AND p.merged_at IS NOT NULL"
        )
        cur.execute(
            "SELECT count(*) FROM ("
            "SELECT p.id FROM entities p LEFT JOIN relations r ON r.src_id=p.id AND r.kind='pr_commit' "
            "WHERE p.kind='pr' AND p.merged_at IS NOT NULL GROUP BY p.id HAVING count(r.dst_id)=0)"
        )
        merged_no_commit = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM entities WHERE kind='pr' AND merged_at IS NOT NULL")
        merged_total = cur.fetchone()[0]

        cur.execute(
            "SELECT count(*) FROM relations r JOIN entities d ON d.id=r.dst_id "
            "WHERE r.kind IN ('closes') AND upper(d.state) <> 'CLOSED'"
        )
        closes_open_dst = cur.fetchone()[0]

log(f"entities: {ents}")
log(f"relations: { {f'{k}/{s}': c for (k, s), c in sorted(rels.items())} }")
log(f"files: {n_files}")

if orphan_comments:
    fails.append(f"комментариев без parent: {orphan_comments}")
log(f"[{'OK' if orphan_comments == 0 else 'FAIL'}] комментарии без parent: {orphan_comments}")

pct = 100 * (merged_total - merged_no_commit) / max(merged_total, 1)
ok = pct >= 90
if not ok:
    fails.append(f"merged PR без pr_commit: {merged_no_commit}")
log(f"[{'OK' if ok else 'FAIL'}] merged PR со связью pr_commit: {merged_total - merged_no_commit}/{merged_total} ({pct:.1f}%)")

log(f"[{'OK' if closes_open_dst < 0.02 * (rels.get(('closes','api'), 0) + rels.get(('closes','regex'), 0)) else 'WARN'}] "
    f"closes на неоткрытые... точнее: закрытых-получателей не соблюдено: {closes_open_dst}")

h = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
with psycopg.connect(PG_DSN) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.native_id, d.native_id FROM relations r "
            "JOIN entities s ON s.id=r.src_id JOIN entities d ON d.id=r.dst_id "
            "WHERE r.kind='closes' AND r.source='api' ORDER BY random() LIMIT 8"
        )
        sample = cur.fetchall()
bad_live = 0
for pr_num, iss_num in sample:
    r = httpx.get(f"https://api.github.com/repos/pydantic/pydantic/issues/{iss_num}", headers=h, timeout=20)
    state = r.json().get("state") if r.status_code == 200 else "?"
    if state != "closed":
        bad_live += 1
        log(f"  LIVE FAIL: #{iss_num} state={state} (из PR #{pr_num})")
ok_live = bad_live == 0
if not ok_live:
    fails.append("живая проверка closes провалена")
log(f"[{'OK' if ok_live else 'FAIL'}] живая проверка: {len(sample) - bad_live}/{len(sample)} issues действительно закрыты")

print("\nИТОГ ФАЗЫ 2:", "ПРИНЯТА" if not fails else f"ПРОБЛЕМЫ: {fails}")
