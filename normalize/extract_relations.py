"""Извлечение связей (мини-граф): parent, pr_commit, closes/references из timeline и regex."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import PG_DSN
from ingest.storage import RAW_DIR

import psycopg
from psycopg.types.json import Json

KW_RE = re.compile(r"(close[sd]?|fix(e[sd])?|resolve[sd]?)\s*:?\s*#(\d+)", re.IGNORECASE)
REF_RE = re.compile(r"#(\d+)")
MAX_REFS_PER_BODY = 40


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def insert_relations(conn: psycopg.Connection, rels: set) -> int:
    rows = [(s, d, k, sr, Json({})) for (s, d, k, sr) in rels]
    total = 0
    with conn.cursor() as cur:
        for i in range(0, len(rows), 5000):
            chunk = rows[i:i + 5000]
            args = ",".join(cur.mogrify("(%s,%s,%s,%s,%s)", r) for r in chunk)
            cur.execute(
                "INSERT INTO relations (src_id,dst_id,kind,source,meta) VALUES "
                + args
                + " ON CONFLICT (src_id,dst_id,kind) DO NOTHING"
            )
            total += cur.rowcount
    conn.commit()
    return total


def iter_raw(name: str):
    with (RAW_DIR / name).open(encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def main() -> None:
    augs = {(r["kind"], r["number"]): r for r in iter_raw("augmentations.jsonl")}
    issues_raw = {r["number"]: r for r in iter_raw("issues.jsonl")}
    prs_raw = {r["number"]: r for r in iter_raw("prs.jsonl")}

    with psycopg.connect(PG_DSN, cursor_factory=psycopg.ClientCursor) as conn:
        apply = Path("normalize/schema.sql").read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(apply)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT id, native_id FROM entities WHERE kind='issue'")
            issue_ids = {int(nid): eid for eid, nid in cur.fetchall()}
            cur.execute(
                "SELECT id, native_id, merged_at IS NOT NULL, "
                "COALESCE(extra->>'merge_commit','') FROM entities WHERE kind='pr'"
            )
            pr_ids, pr_merged, pr_merge_sha = {}, {}, {}
            for eid, nid, merged, mc in cur.fetchall():
                pr_ids[int(nid)] = eid
                pr_merged[int(nid)] = merged
                pr_merge_sha[int(nid)] = mc
            cur.execute("SELECT id, native_id FROM entities WHERE kind='commit'")
            commit_ids = {nid: eid for eid, nid in cur.fetchall()}
            cur.execute(
                "SELECT id, extra->>'parent_kind', extra->>'parent_number' "
                "FROM entities WHERE kind='comment'"
            )
            comments = cur.fetchall()

        num_map = {}
        for n, eid in issue_ids.items():
            num_map[n] = ("issue", eid)
        for n, eid in pr_ids.items():
            num_map.setdefault(n, ("pr", eid))

        rels: set[tuple[int, int, str, str]] = set()

        for cid, pk, pn in comments:
            pid = (issue_ids if pk == "issue" else pr_ids).get(int(pn))
            if pid:
                rels.add((cid, pid, "parent", "api"))

        closing_map = {}
        cref_path = RAW_DIR / "closing_refs.jsonl"
        if cref_path.exists():
            for line in cref_path.open(encoding="utf-8"):
                r = json.loads(line)
                closing_map[r["number"]] = r["closing"]

        n_pc = 0
        for (kind, num), det in augs.items():
            if kind != "pr":
                continue
            pid = pr_ids.get(num)
            if pid is None:
                continue
            for c in det.get("commits", []):
                cid = commit_ids.get(c["sha"])
                if cid:
                    rels.add((pid, cid, "pr_commit", "api"))
                    n_pc += 1
            mc = pr_merge_sha.get(num)
            if mc:
                cid = commit_ids.get(mc)
                if cid:
                    rels.add((pid, cid, "pr_commit", "api"))
            for inum in closing_map.get(num, []):
                iid = issue_ids.get(inum)
                if iid:
                    rels.add((pid, iid, "closes", "api"))

        miss_ref_commits = 0
        for (kind, num), det in augs.items():
            self_id = (issue_ids if kind == "issue" else pr_ids).get(num)
            if self_id is None:
                continue
            merged = pr_merged.get(num, False)
            for t in det["timeline"]:
                ev = t["event"]
                tn = t.get("target_number")
                tgt = num_map.get(tn)
                if ev == "CrossReferencedEvent":
                    if not tgt or tgt[1] == self_id:
                        continue
                    src_id, dst_id = tgt[1], self_id
                    is_close = (
                        tgt[0] == "pr" and pr_merged.get(tn, False) and kind == "issue"
                    )
                    rels.add((src_id, dst_id, "closes" if is_close else "references", "timeline"))
                elif ev == "ReferencedEvent":
                    csha = t.get("commit")
                    cid = commit_ids.get(csha) if csha else None
                    if cid is None:
                        if csha:
                            miss_ref_commits += 1
                    elif kind == "issue":
                        rels.add((cid, self_id, "references", "timeline"))
                elif ev == "ConnectedEvent":
                    if tgt and tgt[1] != self_id:
                        rels.add((self_id, tgt[1], "references", "timeline"))

        dangling = 0
        bodies = []
        bodies += [("issue", n, r.get("title"), r.get("body")) for n, r in issues_raw.items()]
        bodies += [("pr", n, r.get("title"), r.get("body")) for n, r in prs_raw.items()]
        for (kind, num), det in augs.items():
            for i, c in enumerate(det["comments"]):
                bodies.append(("comment", f"{kind}:{num}:c{i}", None, c["body"]))

        for kind, key, title, body in bodies:
            if kind == "comment":
                pkind, pnum, _ = key.split(":")
                sid = (issue_ids if pkind == "issue" else pr_ids).get(int(pnum))
                src_is_pr = pkind == "pr"
                src_num = int(pnum)
            elif kind == "issue":
                sid = issue_ids.get(int(key))
                src_is_pr = False
                src_num = int(key)
            else:
                sid = pr_ids.get(int(key))
                src_is_pr = True
                src_num = int(key)
            if sid is None or not body:
                continue
            text = f"{title or ''}\n{body}"
            kw_nums = {int(m.group(3)) for m in KW_RE.finditer(text)}
            seen = set()
            for rn in REF_RE.findall(text)[:MAX_REFS_PER_BODY]:
                rn = int(rn)
                if rn in seen or rn == src_num:
                    continue
                seen.add(rn)
                tgt = num_map.get(rn)
                if not tgt:
                    dangling += 1
                    continue
                tid, tid_kind = tgt[1], tgt[0]
                if src_is_pr:
                    rkind = (
                        "closes"
                        if rn in kw_nums and pr_merged.get(src_num, False) and tid_kind == "issue"
                        else "references"
                    )
                    rels.add((sid, tid, rkind, "regex"))
                else:
                    rels.add((sid, tid, "references", "regex"))

        log(f"связей собрано: {len(rels)} (вне корпуса ссылок: {dangling}, коммитов не из main: {miss_ref_commits})")
        inserted = insert_relations(conn, rels)
        log(f"вставлено новых: {inserted}")

        with conn.cursor() as cur:
            cur.execute("SELECT kind, source, count(*) FROM relations GROUP BY kind, source ORDER BY kind, source")
            log("разбивка relations:")
            for k, sr, cnt in cur.fetchall():
                log(f"  {k}/{sr}: {cnt}")
            cur.execute(
                "SELECT s.url, d.url FROM relations r "
                "JOIN entities s ON s.id=r.src_id JOIN entities d ON d.id=r.dst_id "
                "WHERE r.kind='closes' ORDER BY random() LIMIT 5"
            )
            log("выборка для ручной проверки closes:")
            for su, du in cur.fetchall():
                log(f"  {su}\n    -> {du}")


if __name__ == "__main__":
    main()

