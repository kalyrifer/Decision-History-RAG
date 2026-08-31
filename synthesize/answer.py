"""Assemble evidence from search results, call LLM, output answer with citations."""

import json
import re
import sys
import time

sys.path.insert(0, ".")

from config import PG_DSN

from synthesize.llm import generate, stats as llm_stats
from synthesize.prompt import SYSTEM_PROMPT, build_prompt

MAX_CHARS_BUDGET = 30000
COMMENT_TRUNCATE = 1500
ISSUE_TRUNCATE = 2500


def _fetch_chunks(conn, entity_ids: list[int]) -> dict[int, str]:
    if not entity_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT entity_id, body FROM chunks "
            "WHERE entity_id = ANY(%s) ORDER BY entity_id, idx",
            (entity_ids,),
        )
        chunks = {}
        for eid, text in cur.fetchall():
            chunks.setdefault(eid, []).append(text)
    result = {}
    for eid, texts in chunks.items():
        result[eid] = "\n\n".join(texts)
    return result


def _fetch_relations(conn, entity_ids: list[int]) -> dict[int, list[dict]]:
    if not entity_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT src_id, dst_id, kind FROM relations "
            "WHERE src_id = ANY(%s) OR dst_id = ANY(%s)",
            (entity_ids, entity_ids),
        )
        rels = {}
        for src, dst, kind in cur.fetchall():
            rels.setdefault(src, []).append({"dst": dst, "kind": kind, "dir": "out"})
            rels.setdefault(dst, []).append({"src": src, "kind": kind, "dir": "in"})
    return rels


def _fetch_entities(conn, entity_ids: list[int]) -> dict[int, dict]:
    if not entity_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, kind, native_id, title, url, created_at, author "
            "FROM entities WHERE id = ANY(%s)",
            (entity_ids,),
        )
        return {
            eid: {
                "id": eid, "kind": kind, "native_id": nid,
                "title": title, "url": url,
                "created_at": str(created_at) if created_at else None,
                "author": author,
            }
            for eid, kind, nid, title, url, created_at, author in cur.fetchall()
        }


def _build_evidence_block(ent: dict, text: str, hop: int) -> dict:
    kind = ent.get("kind", "")
    max_trunc = COMMENT_TRUNCATE if kind == "comment" else ISSUE_TRUNCATE
    if len(text) > max_trunc:
        text = text[:max_trunc] + "\n... [truncated]"
    return {
        "kind": kind,
        "native_id": ent.get("native_id", ""),
        "url": ent.get("url", ""),
        "title": ent.get("title", ""),
        "author": ent.get("author", ""),
        "created_at": ent.get("created_at"),
        "text": text,
        "hop": hop,
    }


def _extract_urls(text: str) -> list[str]:
    return re.findall(r"https://github\.com/[^\s)>\]\"]+", text)


def answer(search_result: dict, question: str, verbose: bool = False,
           noise_filter: bool | None = None) -> dict:
    import psycopg
    from pgvector.psycopg import register_vector

    from config import RERANK_FILTER_ENABLED, RERANK_KEEP_FRAC, RERANK_MIN_KEEP

    rows = search_result.get("rows", [])
    if not rows:
        return {
            "question": question,
            "answer": "Нет результатов поиска. Попробуйте переформулировать запрос.",
            "sources": [],
            "timeline": [],
            "confidence": "low",
            "llm_stats": llm_stats(),
        }

    entity_ids = [r["entity_id"] for r in rows]

    conn = psycopg.connect(PG_DSN)
    register_vector(conn)
    try:
        entities = _fetch_entities(conn, entity_ids)
        chunks_map = _fetch_chunks(conn, entity_ids)
    finally:
        conn.close()

    evidence_blocks = []
    budget_used = 0
    for r in rows:
        eid = r["entity_id"]
        ent = entities.get(eid, {})
        text = chunks_map.get(eid, "")
        if not text:
            continue
        block = _build_evidence_block(ent, text, r.get("hop", 0))
        block_len = len(block["text"])
        if budget_used + block_len > MAX_CHARS_BUDGET:
            remaining = MAX_CHARS_BUDGET - budget_used
            if remaining > 500:
                block["text"] = block["text"][:remaining] + "\n... [budget exceeded]"
                evidence_blocks.append(block)
            break
        evidence_blocks.append(block)
        budget_used += block_len

    use_filter = RERANK_FILTER_ENABLED if noise_filter is None else noise_filter
    if use_filter and len(evidence_blocks) > RERANK_MIN_KEEP:
        from retrieve.rerank import filter_candidates

        if verbose:
            print(f"[answer] реранкер-фильтр: было {len(evidence_blocks)} блоков")
        cands = [dict(b, message=b.get("title", "")) for b in evidence_blocks]
        kept = filter_candidates(question, cands, keep_frac=RERANK_KEEP_FRAC,
                                 min_keep=RERANK_MIN_KEEP)
        kept_ids = {id(b) for b in kept}
        evidence_blocks = [b for b in evidence_blocks if id(b) in kept_ids]
        if verbose:
            print(f"[answer] реранкер-фильтр: осталось {len(evidence_blocks)}")

    prompt = build_prompt(evidence_blocks, question)

    if verbose:
        from synthesize.focus import focus_of
        print(f"[answer] focus={focus_of(question)['primary']}")
        print(f"\n[answer] evidence blocks: {len(evidence_blocks)}, budget: {budget_used}/{MAX_CHARS_BUDGET}")
        print(f"[answer] prompt length: {len(prompt)} chars")

    try:
        raw_response = generate(prompt, system=SYSTEM_PROMPT)
    except RuntimeError as e:
        return {
            "question": question,
            "answer": f"Ошибка LLM: {e}",
            "sources": [],
            "timeline": [],
            "confidence": "low",
            "llm_stats": llm_stats(),
        }

    cited_urls = _extract_urls(raw_response)
    cited_ids = set()
    for u in cited_urls:
        m = re.search(r"(issues|pull)/(\d+)", u)
        if m:
            cited_ids.add(int(m.group(2)))
        m2 = re.search(r"/commit/([0-9a-f]+)", u)
        if m2:
            cited_ids.add(m2.group(1)[:12])

    sources = []
    seen_src = set()
    cited_only = set()
    for r in rows:
        eid = r["entity_id"]
        ent = entities.get(eid, {})
        url = ent.get("url", "")
        num = ent.get("native_id", "")
        if not url or url in seen_src:
            continue
        role = ""
        if isinstance(num, str) and num.isdigit() and int(num) in cited_ids:
            role = "cited"
            cited_only.add(url)
        elif r.get("hop", 0) == 0:
            role = "retrieved"
        else:
            continue  # skip expanded unless cited
        sources.append({"url": url, "kind": ent.get("kind"), "number": num, "role": role})
        seen_src.add(url)
    # sort: cited first, then retrieved; cap at 15
    sources.sort(key=lambda s: (0 if s["role"] == "cited" else 1, s.get("number", "")))
    sources = sources[:15]

    timeline = []
    for b in evidence_blocks:
        if b.get("created_at") and b.get("kind") in ("issue", "pr", "commit"):
            timeline.append({
                "date": b["created_at"][:10],
                "kind": b["kind"],
                "native_id": b["native_id"],
                "title": b["title"],
            })
    timeline.sort(key=lambda x: x["date"])

    confidence = "high"
    if len(evidence_blocks) < 3:
        confidence = "low"
    elif len(evidence_blocks) < 6:
        confidence = "medium"

    return {
        "question": question,
        "answer": raw_response,
        "sources": sources,
        "timeline": timeline,
        "confidence": confidence,
        "llm_stats": llm_stats(),
    }
