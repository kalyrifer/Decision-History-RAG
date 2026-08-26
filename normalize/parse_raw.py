"""Загрузка сырых данных Фазы 1 в Postgres: entities + files."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import PG_DSN, TARGET_REPO
from ingest.storage import RAW_DIR

import psycopg
from psycopg.types.json import Json


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_jsonl(name: str) -> list[dict]:
    recs = []
    with (RAW_DIR / name).open(encoding="utf-8") as f:
        for line in f:
            recs.append(json.loads(line))
    return recs


def ts(s: str | None):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def apply_schema(conn: psycopg.Connection) -> None:
    schema = (Path("normalize") / "schema.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(schema)
    conn.commit()
    log("схема применена")


def insert_entities(conn: psycopg.Connection, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        for i in range(0, len(rows), 2000):
            chunk = rows[i:i + 2000]
            args = ",".join(cur.mogrify("(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", r) for r in chunk)
            cur.execute(
                "INSERT INTO entities (kind,native_id,title,body,author,email,created_at,closed_at,"
                "merged_at,state,url,extra) VALUES "
                + args
                + " ON CONFLICT (kind,native_id) DO NOTHING"
            )
    conn.commit()


def main() -> None:
    issues = {r["number"]: r for r in load_jsonl("issues.jsonl")}
    prs = {r["number"]: r for r in load_jsonl("prs.jsonl")}
    commits = {r["native_id"]: r for r in load_jsonl("commits.jsonl")}
    augs: dict[tuple[str, int], dict] = {}
    for l in load_jsonl("augmentations.jsonl"):
        augs[(l["kind"], l["number"])] = l

    with psycopg.connect(PG_DSN, cursor_factory=psycopg.ClientCursor) as conn:
        apply_schema(conn)

        rows = []
        for num, r in issues.items():
            rows.append((
                "issue", str(num), r["title"], r["body"], r["author"], None,
                ts(r["created_at"]), ts(r["closed_at"]), None, r["state"], r["url"],
                Json({"labels": r["labels"], "milestone": r["milestone"]}),
            ))
        for num, r in prs.items():
            rows.append((
                "pr", str(num), r["title"], r["body"], r["author"], None,
                ts(r["created_at"]), ts(r["closed_at"]), ts(r["merged_at"]), r["state"], r["url"],
                Json({
                    "labels": r["labels"], "milestone": r["milestone"],
                    "additions": r["additions"], "deletions": r["deletions"],
                    "changed_files": r["changed_files"], "base_ref": r["base_ref"],
                    "head_ref": r["head_ref"], "merge_commit": r["merge_commit"],
                    "merged_by": r["merged_by"],
                }),
            ))
        for sha, r in commits.items():
            rows.append((
                "commit", sha, None, r["message"], r["author"], r["email"],
                ts(r["committed_at"]), None, None, None,
                f"https://github.com/{TARGET_REPO}/commit/{sha}",
                Json({"parents": r["parents"], "merge": r["merge"]}),
            ))
        n_comments = 0
        for (kind, num), det in augs.items():
            for idx, c in enumerate(det["comments"]):
                rows.append((
                    "comment", f"{kind}:{num}:c{idx}", None, c["body"], c["author"], None,
                    ts(c["created_at"]), None, None, None, None,
                    Json({"parent_kind": kind, "parent_number": num, "idx": idx}),
                ))
                n_comments += 1

        log(f"к вставке: issues={len(issues)}, prs={len(prs)}, commits={len(commits)}, comments={n_comments}")
        insert_entities(conn, rows)

        id_map: dict[tuple[str, str], int] = {}
        with conn.cursor() as cur:
            cur.execute("SELECT id, kind, native_id FROM entities")
            for eid, k, nid in cur.fetchall():
                id_map[(k, nid)] = eid
        Path("data").joinpath("id_map.count").write_text(str(len(id_map)), encoding="utf-8")

        file_rows = []
        for sha, r in commits.items():
            cid = id_map[("commit", sha)]
            for f in r["files"]:
                file_rows.append((cid, f["path"], f["status"], f["add"], f["del"]))
        with conn.cursor() as cur:
            for i in range(0, len(file_rows), 5000):
                chunk = file_rows[i:i + 5000]
                args = ",".join(cur.mogrify("(%s,%s,%s,%s,%s)", fr) for fr in chunk)
                cur.execute(
                    "INSERT INTO files (entity_id,path,status,add_count,del_count) VALUES "
                    + args
                    + " ON CONFLICT (entity_id,path) DO NOTHING"
                )
        conn.commit()
        log(f"файлов записано: {len(file_rows)}")

        with conn.cursor() as cur:
            cur.execute("SELECT kind, count(*) FROM entities GROUP BY kind ORDER BY kind")
            for k, cnt in cur.fetchall():
                log(f"entities[{k}] = {cnt}")

    (Path("data") / "comment_count.txt").write_text(str(n_comments), encoding="utf-8")


if __name__ == "__main__":
    main()

