"""Санити-проверка Фазы 3: гибрид из двух миров по 5 запросам (векторы и FTS отдельно)."""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from config import EMBED_MODEL, PG_DSN, QUERY_PREFIX

import psycopg

QUERIES = [
    ("Q1 rust core", "why was rust chosen for pydantic core rewrite", {2276, 4790}),
    ("Q2 int->str v2", "int to str coercion removed in version 2 validation", {6045}),
    ("Q3 discriminated", "discriminated union tag based validation openapi", {619}),
    ("Q4 copy semantics", "copy_on_model_validation removed deep copy breaking change", {4092, 7608}),
    ("Q5 strict bool", "strict bool avoid truthy integer values", {579}),
]


def top_vector(cur, model, q):
    vec = model.encode(QUERY_PREFIX + q, normalize_embeddings=True)
    cur.execute(
        "SELECT e.kind, e.native_id, left(c.title, 70), c.embedding <=> %s AS dist "
        "FROM chunks c JOIN entities e ON e.id = c.entity_id "
        "WHERE c.embedding IS NOT NULL ORDER BY dist LIMIT 5",
        (vec,),
    )
    return cur.fetchall()


def top_fts(cur, q):
    cur.execute(
        "SELECT e.kind, e.native_id, left(c.title, 70), ts_rank(c.tsv, websearch_to_tsquery('english', %s)) AS rank "
        "FROM chunks c JOIN entities e ON e.id = c.entity_id "
        "WHERE c.tsv @@ websearch_to_tsquery('english', %s) "
        "ORDER BY rank DESC LIMIT 5",
        (q, q),
    )
    return cur.fetchall()


def main() -> None:
    with psycopg.connect(PG_DSN) as conn:
        register_vector(conn)
        model = SentenceTransformer(EMBED_MODEL, device="cpu")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chunks WHERE embedding IS NULL")
            left = cur.fetchone()[0]
            if left:
                print(f"ВНИМАНИЕ: без эмбеддингов ещё {left} чанков")

        for label, q, anchors in QUERIES:
            with conn.cursor() as cur:
                vres = top_vector(cur, model, q)
                fres = top_fts(cur, q)
            def fmt(rows):
                return "; ".join(f"{k}#{n}({d:.3f})" if isinstance(d, float) else f"{k}#{n}" for k, n, _t, d in rows)

            def nums(rows):
                out = set()
                for _k, n, _t, _d in rows:
                    parts = str(n).split(":")
                    out.add(int(parts[1]) if len(parts) > 1 else int(n))
                return out

            va = nums(vres) & anchors
            fa = nums(fres) & anchors
            print(f"\n{label}: '{q}'")
            print(f"  вектор: {fmt(vres)}")
            print(f"  fts:    {fmt(fres)}")
            print(f"  якоря: вектор={'да' if va else 'НЕТ'}{sorted(va) if va else ''}, fts={'да' if fa else 'НЕТ'}{sorted(fa) if fa else ''}")


if __name__ == "__main__":
    main()
