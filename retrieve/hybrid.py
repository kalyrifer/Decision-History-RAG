"""Гибридный поиск: pgvector (cosine) + Postgres FTS, слияние Reciprocal Rank Fusion."""

import sys
from datetime import datetime

sys.path.insert(0, ".")

from config import EMBED_MODEL, QUERY_PREFIX

_model = None


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _model


def encode_query(q: str):
    model = get_model()
    return model.encode(QUERY_PREFIX + q, normalize_embeddings=True)


def vector_top(cur, qvec, k=25, fetch=200):
    cur.execute("SET hnsw.ef_search = 200")
    cur.execute(
        "SELECT c.entity_id, c.embedding <=> %s::vector AS dist "
        "FROM chunks c WHERE c.embedding IS NOT NULL "
        "ORDER BY dist LIMIT %s",
        (qvec, fetch),
    )
    best = {}
    for eid, dist in cur.fetchall():
        if eid not in best or dist < best[eid]:
            best[eid] = dist
    ranked = sorted(best.items(), key=lambda x: x[1])[:k]
    return [(eid, i + 1) for i, (eid, _d) in enumerate(ranked)]


def fts_top(cur, query, k=25, fetch=200):
    cur.execute(
        """
        WITH q AS (
            SELECT string_agg(lexeme, ' | ') AS t
            FROM unnest(to_tsvector('english', %(q)s)) AS u(lexeme)
            WHERE length(lexeme) > 2
        )
        SELECT c.entity_id, ts_rank(c.tsv, to_tsquery('english', (SELECT t FROM q))) AS rank
        FROM chunks c, q
        WHERE c.tsv @@ to_tsquery('english', (SELECT t FROM q))
        ORDER BY rank DESC
        LIMIT %(f)s
        """,
        {"q": query, "f": fetch},
    )
    best = {}
    for eid, rank in cur.fetchall():
        if eid not in best or rank > best[eid]:
            best[eid] = rank
    ranked = sorted(best.items(), key=lambda x: -x[1])[:k]
    return [(eid, i + 1) for i, (eid, _r) in enumerate(ranked)]


def rrf_fuse(vec_ranked, fts_ranked, k_rrf=60):
    scores = {}
    channels = {}
    for lst, name in ((vec_ranked, "vec"), (fts_ranked, "fts")):
        for eid, rank in lst:
            scores[eid] = scores.get(eid, 0.0) + 1.0 / (k_rrf + rank)
            channels.setdefault(eid, set()).add(name)
    ordered = sorted(scores.items(), key=lambda x: -x[1])
    return [(eid, sc, channels.get(eid, set())) for eid, sc in ordered]


def hybrid_search(conn, query, k=25):
    with conn.cursor() as cur:
        qvec = encode_query(query)
        vec_r = vector_top(cur, qvec, k=k)
        fts_r = fts_top(cur, query, k=k)
    fused = rrf_fuse(vec_r, fts_r)
    return fused, vec_r, fts_r
