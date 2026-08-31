"""Конвейер поиска: гибрид -> RRF -> свёртка комментариев в родителей -> graph expansion."""

import json
import sys
import time

sys.path.insert(0, ".")

from config import PG_DSN, FTS_WEIGHT

from retrieve.expand import expand
from retrieve.hybrid import hybrid_search


def native_num(native_id: str) -> int:
    parts = str(native_id).split(":")
    try:
        return int(parts[1] if len(parts) > 1 else native_id)
    except ValueError:
        return -1


def _collapse_comments(conn, fused):
    ids = [eid for eid, _s, _c in fused]
    if not ids:
        return fused
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, extra->>'parent_number' FROM entities "
            "WHERE kind='comment' AND id = ANY(%s)", (ids,)
        )
        cmap = {cid: int(pn) for cid, pn in cur.fetchall()}
        pnums = sorted(set(cmap.values()))
        pmap = {}
        if pnums:
            cur.execute(
                "SELECT id, native_id::int FROM entities "
                "WHERE kind IN ('issue','pr') AND native_id::int = ANY(%s)", (pnums,)
            )
            pmap = {num: pid for pid, num in cur.fetchall()}

    acc = {}
    order = []
    for eid, score, chans in fused:
        key = pmap.get(cmap.get(eid, -1), eid)
        slot = acc.setdefault(key, [0.0, set(), 0])
        slot[0] += score
        slot[1] |= chans
        if eid in cmap:
            slot[2] += 1
        if key not in order:
            order.append(key)

    out = []
    for key in order:
        sc, chans, ncom = acc[key]
        tag = f"+{ncom}c" if ncom else ""
        out.append((key, sc, chans | ({tag} if tag else set())))
    out.sort(key=lambda x: -x[1])
    return out


def _fetch_info(conn, ids):
    info = {}
    if not ids:
        return info
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, kind, native_id, title, url, created_at, coalesce(author,'') "
            "FROM entities WHERE id = ANY(%s)", (ids,)
        )
        for eid, kind, nid, title, url, created_at, author in cur.fetchall():
            info[eid] = {
                "entity_id": eid, "kind": kind, "native_id": nid,
                "number": native_num(nid), "title": title, "url": url,
                "created_at": str(created_at) if created_at else None,
                "author": author,
            }
    return info


def search(query: str, no_expand: bool = False, k: int = 25, n_anchors: int = 10,
           max_expand: int = 40, rewrite_query: bool = True, mode: str = "hybrid",
           w_fts: float = FTS_WEIGHT):
    from pgvector.psycopg import register_vector

    import psycopg

    t0 = time.time()
    conn = None
    try:
        conn = psycopg.connect(PG_DSN)
        register_vector(conn)

        search_text = query
        if rewrite_query:
            from retrieve.query_rewrite import rewrite

            rw = rewrite(query)
            search_text = rw["en"] + (" " + " ".join(rw["kw"]) if rw["kw"] else "")

        fused, vec_r, fts_r = hybrid_search(conn, search_text, k=k, mode=mode, w_fts=w_fts)

        # файловый канал: поиск по путям для вопросов про структуру
        with conn.cursor() as cur:
            from retrieve.file_search import file_path_search
            from synthesize.focus import focus_of

            foc = focus_of(query)
            file_weight = 0.45 if foc["primary"] == "structure" else 0.3
            file_r = file_path_search(cur, search_text, k=k)
        if file_r:
            fused_d = {eid: [sc, ch] for eid, sc, ch in fused}
            for eid, sc in file_r:
                if eid in fused_d:
                    fused_d[eid][0] += sc * file_weight
                    fused_d[eid][1].add("file")
                else:
                    fused_d[eid] = [sc * file_weight, {"file"}]
            fused = sorted(
                [(eid, sc, ch) for eid, (sc, ch) in fused_d.items()],
                key=lambda x: -x[1],
            )

        fused = _collapse_comments(conn, fused)
        info = _fetch_info(conn, [eid for eid, _s, _c in fused])

        anchors = []
        for eid, _sc, _ch in fused:
            ent = info.get(eid, {})
            if ent.get("kind") in ("issue", "pr") and ent.get("author") == "dependabot[bot]":
                continue
            anchors.append(eid)
            if len(anchors) >= n_anchors:
                break

        expanded = []
        if not no_expand and anchors:
            with conn.cursor() as cur:
                expanded = expand(cur, anchors, max_nodes=max_expand)

        all_ids = list(dict.fromkeys(
            [eid for eid, _s, _c in fused] + [eid for eid, _d, _w in expanded]
        ))
        extra_info = _fetch_info(conn, [eid for eid in all_ids if eid not in info])
        info.update(extra_info)

        rows = []
        seen = set()
        for eid, score, channels in fused:
            r = dict(info[eid])
            r["hop"] = 0
            r["score"] = round(score, 4)
            r["weight"] = None
            r["channels"] = sorted(channels)
            rows.append(r)
            seen.add(eid)
        for eid, depth, weight in expanded:
            if eid in seen or eid not in info:
                continue
            seen.add(eid)
            r = dict(info[eid])
            r["hop"] = depth
            r["score"] = None
            r["weight"] = round(weight, 3)
            r["channels"] = ["graph"]
            rows.append(r)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT extra->>'parent_kind', extra->>'parent_number', count(*) "
                "FROM entities WHERE kind='comment' GROUP BY 1, 2"
            )
            comment_counts = {(k or "", int(n) if n else 0): c for k, n, c in cur.fetchall()}
        for r in rows:
            pk = "issue" if r["kind"] == "issue" else "pr"
            r["comments"] = comment_counts.get((pk, r["number"]), 0)

        elapsed = time.time() - t0
        return {"query": query, "rows": rows, "elapsed_s": round(elapsed, 2),
                "fusion_size": len(fused), "expanded_size": len(expanded),
                "anchors": anchors}
    finally:
        if conn is not None:
            conn.close()


def print_table(res: dict) -> None:
    print(f"\nзапрос: «{res['query']}»  ({res['elapsed_s']}с, fusion={res['fusion_size']}, "
          f"expanded={res['expanded_size']})")
    print(f"{'тип':<8} {'источник':<12} {'хоп':>3} {'score':>8} {'#':>7}  заголовок")
    print("-" * 110)
    for r in res["rows"]:
        src = "/".join(r["channels"])[:12]
        title = (r["title"] or "")[:55]
        score = f"{r['score']:.4f}" if r["score"] is not None else f"w={r['weight']}"
        num = r["native_id"][:8] if r["kind"] == "commit" else f"#{r['number']}"
        print(f"{r['kind']:<8} {src:<12} {r['hop']:>3} {score:>8} {num:>7}  {title}")
    urls = [(r["url"], r["hop"], r["kind"], r["number"]) for r in res["rows"][:15] if r["url"]]
    if urls:
        print("\nссылки:")
        for u, h, kind, num in urls:
            tag = "" if h == 0 and kind != "comment" else (
                f" [хоп {h}]" if h else f" [комментарий треда #{num}]")
            print(f"  {u}{tag}")


def main_cli():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--no-expand", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = search(a.query, no_expand=a.no_expand)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    else:
        print_table(res)


if __name__ == "__main__":
    main_cli()
