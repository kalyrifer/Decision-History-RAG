"""Построение текстовых представлений сущностей и чанков длинных тел."""

import json
import sys
from datetime import datetime

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import PG_DSN
from ingest.storage import RAW_DIR

import psycopg

MAX_CHARS = 1500
OVERLAP = 150
MAX_FILES = 40
MAX_LABELS = 10


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def iter_raw(name: str):
    with (RAW_DIR / name).open(encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def split_body(body: str) -> list[str]:
    body = (body or "").strip()
    if not body:
        return [""]
    if len(body) <= MAX_CHARS:
        return [body]
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    chunks = []
    cur = ""
    for p in paras:
        while len(p) > MAX_CHARS:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:MAX_CHARS])
            p = p[MAX_CHARS - OVERLAP:]
        if len(cur) + len(p) + 2 <= MAX_CHARS:
            cur = f"{cur}\n\n{p}" if cur else p
        else:
            chunks.append(cur)
            tail = cur[-OVERLAP:] if len(cur) > OVERLAP else cur
            cur = f"{tail}\n\n{p}"
    if cur:
        chunks.append(cur)
    return chunks


def main() -> None:
    issues_raw = {r["number"]: r for r in iter_raw("issues.jsonl")}
    prs_raw = {r["number"]: r for r in iter_raw("prs.jsonl")}

    with psycopg.connect(PG_DSN, cursor_factory=psycopg.ClientCursor) as conn:
        with conn.cursor() as cur:
            cur.execute(open("represent/schema.sql", encoding="utf-8").read())
        conn.commit()
        log("схема chunks применена")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, kind, native_id, title, body, author, created_at, extra "
                "FROM entities WHERE kind IN ('issue','pr')"
            )
            main_ents = cur.fetchall()
            cur.execute(
                "SELECT c.id, c.native_id, c.body, c.author, c.created_at, "
                "c.extra->>'parent_kind', c.extra->>'parent_number' "
                "FROM entities c WHERE kind='comment'"
            )
            comment_rows = cur.fetchall()
            cur.execute(
                "SELECT id, native_id, body, author, created_at FROM entities WHERE kind='commit'"
            )
            commit_rows = cur.fetchall()

        parent_titles = {nid: title for _eid, _k, nid, title, _b, _a, _ca, _extra in main_ents}

        chunk_rows = []

        def fmt_date(dt):
            return dt.strftime("%Y-%m-%d") if dt else ""

        for eid, _kind, nid, title, body, author, created_at, extra in main_ents:
            num = int(nid)
            kind_word = "PR" if num in prs_raw else "ISSUE"
            head = f"[{kind_word} #{num} · {fmt_date(created_at)} · {author or 'ghost'}] {title or ''}"
            parts = [body or ""]
            labels = (extra.get("labels") or [])[:MAX_LABELS]
            if labels:
                parts.append(f"Labels: {', '.join(labels)}")
            if extra.get("merged_at"):
                parts.append(f"Merged by {extra.get('merged_by') or 'unknown'}")
            full_body = "\n".join(p for p in parts if p)
            pieces = split_body(full_body)
            for i, piece in enumerate(pieces):
                chunk_rows.append((eid, i, head, piece, len(pieces)))

        for cid, nid, body, author, created_at in commit_rows:
            sha_short = nid[:8]
            lines = (body or "").split("\n", 1)
            subject = lines[0]
            rest = lines[1].strip() if len(lines) > 1 else ""
            head = f"[COMMIT {sha_short} · {fmt_date(created_at)} · {author or 'unknown'}] {subject}"
            files_q = """
                SELECT path FROM files WHERE entity_id=%s LIMIT %s
            """
            with conn.cursor() as cur:
                cur.execute(files_q, (cid, MAX_FILES))
                paths = [r[0] for r in cur.fetchall()]
            parts = []
            if rest:
                parts.append(rest)
            if paths:
                parts.append("Files: " + ", ".join(paths))
            full_body = "\n".join(parts)
            pieces = split_body(full_body)
            for i, piece in enumerate(pieces):
                chunk_rows.append((cid, i, head, piece, len(pieces)))

        for cid, nid, body, author, created_at, pk, pn in comment_rows:
            pnum = int(pn)
            pkind = "PR" if pk == "pr" else "ISSUE"
            ptitle = parent_titles.get(str(pnum)) or ""
            head = f"[COMMENT · {fmt_date(created_at)} · {author or 'ghost'} on {pkind} #{pnum}] {ptitle}"
            pieces = split_body(body or "")
            for i, piece in enumerate(pieces):
                chunk_rows.append((cid, i, head, piece, len(pieces)))

        log(f"чанков к вставке: {len(chunk_rows)}")
        with conn.cursor() as cur:
            for i in range(0, len(chunk_rows), 4000):
                chunk = chunk_rows[i:i + 4000]
                args = ",".join(cur.mogrify("(%s,%s,%s,%s,%s)", r) for r in chunk)
                cur.execute(
                    "INSERT INTO chunks (entity_id,idx,title,body,n_chunks) VALUES "
                    + args
                    + " ON CONFLICT (entity_id,idx) DO NOTHING"
                )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(embedding) FROM chunks")
            total, embedded = cur.fetchone()
        log(f"итого чанков в БД: {total}, уже с эмбеддингами: {embedded}")


if __name__ == "__main__":
    main()

