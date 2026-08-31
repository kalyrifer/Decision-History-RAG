"""Backfill: проставляет old_path/status=R в files из rename-детекции git.

Выполняется поверх уже загруженных данных (без переингеста). Читает name-status
с --find-renames из data/repo.git и обновляет таблицу files.

Запуск:
    python scripts/backfill_renames.py
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import PG_DSN, REPO_GIT_DIR

SHA_RE = __import__("re").compile(r"^[0-9a-f]{40}$")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    ref = None
    for cand in ("main", "master"):
        r = subprocess.run(["git", "-C", str(REPO_GIT_DIR), "rev-parse", "--verify", cand],
                           capture_output=True, text=True)
        if r.returncode == 0:
            ref = cand
            break
    if not ref:
        raise SystemExit("не найден main/master в клоне")

    out = subprocess.run(
        ["git", "-C", str(REPO_GIT_DIR), "log", ref, "--root",
         "--name-status", "--find-renames", "--pretty=format:%H"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout

    renames = {}  # sha -> list[(old, new)]
    cur_sha = None
    for line in out.splitlines():
        if SHA_RE.match(line):
            cur_sha = line
            renames.setdefault(cur_sha, [])
        elif cur_sha is not None and line.startswith("R"):
            parts = line.split("\t")
            if len(parts) >= 3:
                renames[cur_sha].append((parts[1], parts[2]))

    n_renamed = sum(len(v) for v in renames.values())
    log(f"ренеймов найдено: {n_renamed} (в {len([s for s, v in renames.items() if v])} коммитах)")

    import psycopg

    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, native_id FROM entities WHERE kind='commit'")
            eid_of = {sha: eid for eid, sha in cur.fetchall()}

        updated = 0
        with conn.cursor() as cur:
            for sha, pairs in renames.items():
                eid = eid_of.get(sha)
                if eid is None or not pairs:
                    continue
                for old, new in pairs:
                    # пометить новый путь как R и указать old_path
                    cur.execute(
                        "UPDATE files SET status='R', old_path=%s "
                        "WHERE entity_id=%s AND path=%s",
                        (old, eid, new),
                    )
                    updated += cur.rowcount
                    # старый путь (появившийся как D при --no-renames) — обновить ссылку
                    cur.execute(
                        "UPDATE files SET old_path=%s WHERE entity_id=%s AND path=%s",
                        (new, eid, old),
                    )
        conn.commit()
        log(f"обновлено строк files: {updated}")

        with conn.cursor() as cur:
            cur.execute("SELECT status, count(*) FROM files GROUP BY status ORDER BY status")
            log("разбивка files по статусам:")
            for st, cnt in cur.fetchall():
                log(f"  {st}: {cnt}")


if __name__ == "__main__":
    main()