"""Выгрузка issues/PR pydantic через GitHub GraphQL: инвентаризация + детали по батчам."""

import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

from config import GITHUB_TOKEN, TARGET_REPO
from ingest.storage import append_jsonl, load_ckpt, save_ckpt

GQL_URL = "https://api.github.com/graphql"
OWNER, NAME = TARGET_REPO.split("/")
BODY_LIMIT = 60000
BATCH_ISSUES = 12
BATCH_PRS = 8
POINTS_BUFFER = 300

TIMELINE_TYPES = "[CROSS_REFERENCED_EVENT,REFERENCED_EVENT,CLOSED_EVENT,REOPENED_EVENT,CONNECTED_EVENT,MILESTONED_EVENT]"

ISSUE_SCALARS = "number title state url createdAt closedAt author{login} milestone{title} labels(first:20){nodes{name}} body"
PR_SCALARS = (
    ISSUE_SCALARS + " mergedAt mergedBy{login} additions deletions changedFiles "
    "baseRefName headRefName mergeCommit{oid}"
)


class BatchError(Exception):
    pass


_rate = {"remaining": None, "reset_at": None}
stats = {"points": 0, "items": 0, "batches": 0}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "decision-history-rag",
        },
        timeout=httpx.Timeout(30, read=180),
    )


def _parse_reset(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _update_rate(rl: dict | None) -> None:
    if rl:
        _rate["remaining"] = rl.get("remaining")
        _rate["reset_at"] = _parse_reset(rl["resetAt"]) if rl.get("resetAt") else None
        stats["points"] += rl.get("cost", 0)


def ensure_budget(est: int) -> None:
    if _rate["remaining"] is None or _rate["remaining"] >= est + POINTS_BUFFER:
        return
    wait = (_rate["reset_at"] or time.time()) - time.time() + 15
    if wait <= 0:
        return
    log(f"лимит точек почти исчерпан ({_rate['remaining']}), сплю {int(wait)}с до сброса")
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(min(60, max(1, deadline - time.time())))
        log(f"  ...осталось ждать {int(deadline - time.time())}с")
    _rate["remaining"] = 5000


def gql(client: httpx.Client, query: str, variables: dict | None = None) -> dict:
    delay = 2.0
    last_err = None
    for attempt in range(6):
        try:
            r = client.post(GQL_URL, json={"query": query, "variables": variables or {}})
            if r.status_code == 403 and r.headers.get("Retry-After"):
                ra = int(r.headers["Retry-After"]) + random.uniform(0, 2)
                log(f"secondary rate limit, жду {int(ra)}с")
                time.sleep(ra)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            payload = r.json()
            if "errors" in payload and payload.get("data") is None:
                types = {e.get("type", "?") for e in payload["errors"]}
                if {"SERVICE_UNAVAILABLE", "SERVER_ERROR"} & types:
                    raise RuntimeError(f"gql transient: {types}")
                raise BatchError(f"gql errors: {payload['errors'][:2]}")
            if "data" not in payload:
                raise BatchError(f"нет data в ответе: {str(payload)[:200]}")
            return payload["data"]
        except BatchError:
            raise
        except Exception as e:
            last_err = e
            sleep_s = min(120, delay * (2**attempt)) + random.uniform(0, 1)
            log(f"ошибка ({type(e).__name__}: {str(e)[:100]}), ретрай {attempt + 1}/6 через {int(sleep_s)}с")
            time.sleep(sleep_s)
    raise BatchError(f"исчерпаны ретраи: {last_err}")


def _actor(a: dict | None) -> str | None:
    return a.get("login") if a else None


def _clean_body(body: str | None) -> str:
    body = body or ""
    return body[:BODY_LIMIT] if len(body) > BODY_LIMIT else body


def _base_issue(n: dict) -> dict:
    return {
        "kind": "issue",
        "number": n["number"],
        "title": n["title"],
        "body": _clean_body(n.get("body")),
        "state": n["state"],
        "url": n["url"],
        "created_at": n["createdAt"],
        "closed_at": n["closedAt"],
        "author": _actor(n.get("author")),
        "labels": [x["name"] for x in (n.get("labels") or {}).get("nodes", [])],
        "milestone": (n.get("milestone") or {}).get("title"),
    }


def _base_pr(n: dict) -> dict:
    rec = _base_issue(n)
    rec.update(
        {
            "kind": "pr",
            "merged_at": n.get("mergedAt"),
            "merged_by": _actor(n.get("mergedBy")),
            "additions": n.get("additions"),
            "deletions": n.get("deletions"),
            "changed_files": n.get("changedFiles"),
            "base_ref": n.get("baseRefName"),
            "head_ref": n.get("headRefName"),
            "merge_commit": (n.get("mergeCommit") or {}).get("oid"),
        }
    )
    return rec


def run_inventory(client: httpx.Client) -> None:
    cp = load_ckpt(TARGET_REPO)
    inv = cp["inventory"]
    inv.setdefault("issues_done", False)
    inv.setdefault("prs_done", False)
    inv.setdefault("issue_numbers", [])
    inv.setdefault("pr_numbers", [])
    if inv["done"]:
        log("инвентаризация уже завершена, пропускаю")
        return

    sides = [
        ("issues", "issues.jsonl", "issue_numbers", "issues_done", "issue_cursor", _base_issue, ISSUE_SCALARS),
        ("pullRequests", "prs.jsonl", "pr_numbers", "prs_done", "pr_cursor", _base_pr, PR_SCALARS),
    ]
    for gql_kind, filename, list_key, done_key, cursor_key, base_fn, scalars in sides:
        if inv[done_key]:
            log(f"{gql_kind}: сторона уже готова, пропускаю")
            continue
        seen = set(inv[list_key])
        cursor = inv[cursor_key]
        total = None
        fetched = 0
        while True:
            after = f',after:"{cursor}"' if cursor else ""
            q = (
                "query{rateLimit{cost remaining resetAt} "
                f'repository(owner:"{OWNER}",name:"{NAME}"){{'
                f"{gql_kind}(first:100{after},orderBy:{{field:CREATED_AT,direction:ASC}})"
                f"{{totalCount pageInfo{{hasNextPage endCursor}} nodes{{{scalars}}}}}}}}}"
            )
            ensure_budget(6)
            data = gql(client, q)
            _update_rate(data.get("rateLimit"))
            node = data["repository"][gql_kind]
            total = node["totalCount"]
            page_recs = [base_fn(n) for n in node["nodes"] if n["number"] not in seen]
            for r in page_recs:
                seen.add(r["number"])
            inv[list_key].extend(r["number"] for r in page_recs)
            fetched += len(page_recs)
            append_jsonl(filename, page_recs)
            if node["pageInfo"]["hasNextPage"]:
                cursor = node["pageInfo"]["endCursor"]
                inv[cursor_key] = cursor
            save_ckpt(cp)
            log(
                f"инвентаризация {gql_kind}: {len(inv[list_key])}/{total}"
                f" (+{len(page_recs)} новых), cost={data['rateLimit']['cost']}"
            )
            if not node["pageInfo"]["hasNextPage"]:
                break
        inv[done_key] = True
        inv[cursor_key] = None
        save_ckpt(cp)
    inv["done"] = True
    save_ckpt(cp)
    log(
        f"инвентаризация завершена: issues={len(inv['issue_numbers'])}, prs={len(inv['pr_numbers'])}"
    )


TIMELINE_INNER = (
    "__typename "
    "... on CrossReferencedEvent{createdAt source{__typename ... on Issue{number} ... on PullRequest{number}}} "
    "... on ReferencedEvent{createdAt subject{__typename ... on Issue{number} ... on PullRequest{number}} commit{oid}} "
    "... on ClosedEvent{createdAt} "
    "... on ReopenedEvent{createdAt} "
    "... on ConnectedEvent{createdAt subject{__typename ... on Issue{number} ... on PullRequest{number}}} "
    "... on MilestonedEvent{createdAt milestoneTitle}"
)


def _tl_parse(nodes: list[dict]) -> list[dict]:
    out = []
    for e in nodes:
        rec = {"event": e["__typename"], "created_at": e.get("createdAt")}
        tgt = e.get("source") or e.get("subject")
        if tgt and tgt.get("__typename") in ("Issue", "PullRequest"):
            rec["target_number"] = tgt["number"]
            rec["target_kind"] = "issue" if tgt["__typename"] == "Issue" else "pr"
        if e.get("commit"):
            rec["commit"] = e["commit"]["oid"]
        if e.get("milestoneTitle"):
            rec["milestone"] = e["milestoneTitle"]
        out.append(rec)
    return out


def _conn(name: str, after: str) -> tuple[str, object]:
    tail = f',after:"{after}"' if after else ""

    def tail_parse(node: dict):
        return (
            node["totalCount"],
            node["pageInfo"]["hasNextPage"],
            node["pageInfo"]["endCursor"],
        )

    if name == "comments":
        expr = (
            "comments(first:99" + tail + "){totalCount pageInfo{hasNextPage endCursor}"
            " nodes{author{login} createdAt body}}"
        )
        return expr, lambda nd: (
            [
                {"author": _actor(x.get("author")), "created_at": x["createdAt"], "body": _clean_body(x["body"])}
                for x in nd["nodes"]
            ],
        ) + tail_parse(nd)
    if name == "timeline":
        expr = (
            "timelineItems(first:99" + tail + ",itemTypes:" + TIMELINE_TYPES + ")"
            "{totalCount pageInfo{hasNextPage endCursor} nodes{" + TIMELINE_INNER + "}}"
        )
        return expr, lambda nd: (_tl_parse(nd["nodes"]),) + tail_parse(nd)
    if name == "commits":
        expr = (
            "commits(first:99" + tail + "){totalCount pageInfo{hasNextPage endCursor}"
            " nodes{commit{oid message author{name email} committedDate}}}"
        )
        return expr, lambda nd: (
            [
                {
                    "sha": x["commit"]["oid"],
                    "message": x["commit"]["message"],
                    "author": (x["commit"].get("author") or {}).get("name"),
                    "date": x["commit"]["committedDate"],
                }
                for x in nd["nodes"]
            ],
        ) + tail_parse(nd)
    if name == "reviews":
        expr = (
            "reviews(first:30" + tail + "){totalCount pageInfo{hasNextPage endCursor}"
            " nodes{author{login} state submittedAt body"
            " comments(first:5){totalCount nodes{author{login} createdAt body}}}}"
        )
        return expr, lambda nd: (
            [
                {
                    "author": _actor(x.get("author")),
                    "state": x["state"],
                    "submitted_at": x["submittedAt"],
                    "body": _clean_body(x["body"]),
                    "comments": [
                        {"author": _actor(c.get("author")), "created_at": c["createdAt"], "body": _clean_body(c["body"])}
                        for c in (x.get("comments") or {}).get("nodes", [])
                    ],
                }
                for x in nd["nodes"]
            ],
        ) + tail_parse(nd)
    raise ValueError(name)


def _single_conn_query(gql_kind: str, number: int, expr: str) -> str:
    return (
        "query{rateLimit{cost remaining resetAt} "
        + f'repository(owner:"{OWNER}",name:"{NAME}")'
        + "{"
        + f"{gql_kind}(number:{number})"
        + "{" + expr + "}}}"
    )


def paginate_overflow(client: httpx.Client, gql_kind: str, number: int, name: str,
                      values: list, total: int, has_next: bool, cursor: str | None) -> tuple[list, bool]:
    field = "timelineItems" if name == "timeline" else name
    while has_next and len(values) < total and cursor:
        expr, parse_fn = _conn(name, cursor)
        data = gql(client, _single_conn_query(gql_kind, number, expr))
        _update_rate(data.get("rateLimit"))
        node = data["repository"][gql_kind][field]
        got, total, has_next, cursor = parse_fn(node)
        values.extend(got)
    return values, len(values) >= total


def _detail_fields(kind: str, merged: bool) -> str:
    parts = ["comments", "timeline"]
    if kind == "pr" and merged:
        parts += ["commits", "reviews"]
    return " ".join(_conn(p, "")[0] for p in parts)


def _build_batch_query(entries: list[tuple[str, str, int, bool]]) -> str:
    body = []
    for alias, kind, number, merged in entries:
        gql_kind = "issue" if kind == "issue" else "pullRequest"
        body.append(f"{alias}: {gql_kind}(number:{number})" + "{" + _detail_fields(kind, merged) + "}")
    joined = " ".join(body)
    return (
        "query{rateLimit{cost remaining resetAt} "
        + f'repository(owner:"{OWNER}",name:"{NAME}")'
        + "{" + joined + "}}"
    )


def _parse_detail(kind: str, number: int, merged: bool, node: dict, client: httpx.Client) -> dict:
    gql_kind = "issue" if kind == "issue" else "pullRequest"
    comments, ct, chn, cur = _conn("comments", "")[1](node["comments"])
    if chn:
        comments, _ = paginate_overflow(client, gql_kind, number, "comments", comments, ct, chn, cur)
    timeline, tt, thn, tcur = _conn("timeline", "")[1](node["timelineItems"])
    if thn:
        timeline, _ = paginate_overflow(client, gql_kind, number, "timeline", timeline, tt, thn, tcur)
    rec = {
        "kind": kind,
        "number": number,
        "comments": comments,
        "comments_total": ct,
        "timeline": timeline,
        "timeline_total": tt,
    }
    if kind == "pr" and merged:
        commits_node = node.get("commits")
        reviews_node = node.get("reviews")
        if commits_node is not None:
            commits, kt, khn, kcur = _conn("commits", "")[1](commits_node)
            if khn:
                commits, _ = paginate_overflow(client, gql_kind, number, "commits", commits, kt, khn, kcur)
            rec["commits"] = commits
        if reviews_node is not None:
            reviews, rt, rhn, rcur = _conn("reviews", "")[1](reviews_node)
            if rhn:
                reviews, _ = paginate_overflow(client, gql_kind, number, "reviews", reviews, rt, rhn, rcur)
            rec["reviews"] = reviews
            rec["reviews_total"] = rt
    return rec


def run_details(client: httpx.Client, max_batches: int | None = None) -> None:
    cp = load_ckpt(TARGET_REPO)
    inv = cp["inventory"]
    assert inv["done"], "сначала инвентаризация"

    merged_map = {}
    prs_path = Path("data/raw/prs.jsonl")
    if prs_path.exists():
        with prs_path.open(encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                merged_map[r["number"]] = bool(r.get("merged_at"))

    q_issues = sorted(set(inv.get("issue_numbers", [])))
    q_pm = sorted(n for n in set(inv.get("pr_numbers", [])) if merged_map.get(n))
    q_pu = sorted(n for n in set(inv.get("pr_numbers", [])) if not merged_map.get(n))
    d = cp["detail"]
    total_all = len(q_issues) + len(q_pm) + len(q_pu)
    log(
        f"очереди деталей: issues={len(q_issues)}, pr_merged={len(q_pm)}, pr_other={len(q_pu)}; "
        f"стартовые позиции: {d['idx_issues']}/{d['idx_pr_merged']}/{d['idx_pr_other']}"
    )

    stages = [
        ("issue", "idx_issues", q_issues, BATCH_ISSUES),
        ("pr_merged", "idx_pr_merged", q_pm, BATCH_PRS),
        ("pr_other", "idx_pr_other", q_pu, BATCH_PRS),
    ]
    t0 = time.time()

    for stage_name, idx_key, queue, batch_size in stages:
        gql_kind = "issue" if stage_name == "issue" else "pullRequest"
        kind_for_rec = "issue" if stage_name == "issue" else "pr"
        while d[idx_key] < len(queue):
            if max_batches is not None and stats["batches"] >= max_batches:
                log(f"достигнут лимит --max-batches={max_batches}, стоп (чекпоинт сохранён)")
                return
            chunk = queue[d[idx_key]: d[idx_key] + batch_size]
            entries = [(f"a{i}", kind_for_rec, n, stage_name == "pr_merged") for i, n in enumerate(chunk)]
            est = (2 if stage_name != "pr_merged" else 5) * len(chunk) + 3
            ensure_budget(est)
            query = _build_batch_query(entries)
            try:
                data = gql(client, query)
            except BatchError as e:
                log(f"БАТЧ ПРОПУЩЕН {chunk[:3]}...: {str(e)[:150]}")
                append_jsonl("errors.jsonl", [{"stage": stage_name, "numbers": chunk, "error": str(e)[:300]}])
                d[idx_key] += len(chunk)
                stats["batches"] += 1
                save_ckpt(cp)
                continue
            _update_rate(data.get("rateLimit"))
            repo_node = data["repository"]
            recs = []
            failed = []
            for alias, _k, number, merged in entries:
                node = repo_node.get(alias)
                if node is None:
                    failed.append((number, "null ответ"))
                    continue
                try:
                    recs.append(_parse_detail(kind_for_rec, number, merged, node, client))
                except Exception as e:
                    failed.append((number, f"{type(e).__name__}: {str(e)[:80]}"))
            if recs:
                append_jsonl("augmentations.jsonl", recs)
            if failed:
                append_jsonl("errors.jsonl", [{"stage": stage_name, "numbers": [n for n, _ in failed],
                                               "error": "; ".join(m for _, m in failed)}])
            d[idx_key] += len(chunk)
            d["batches_done"] += 1
            stats["batches"] += 1
            stats["items"] += len(chunk)
            save_ckpt(cp)

            done_now = sum(d[k] for _, k, _, _ in stages)
            eta_h = ""
            elapsed_h = (time.time() - t0) / 3600
            if stats["items"] and stats["points"] and elapsed_h > 0.005:
                pts_per_item = stats["points"] / stats["items"]
                rate_pts_per_h = max(stats["points"] / elapsed_h, 1)
                remain_items = total_all - done_now
                eta_h = f", ETA~{(remain_items * pts_per_item) / rate_pts_per_h:.1f}ч"
            log(
                f"{stage_name}: {d[idx_key]}/{len(queue)} (всего {done_now}/{total_all}) "
                f"cost={data['rateLimit']['cost']} rem={data['rateLimit']['remaining']}{eta_h}"
            )
    log("детали выгружены полностью")


if __name__ == "__main__":
    c = make_client()
    run_inventory(c)
    run_details(c)
