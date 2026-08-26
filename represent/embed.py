"""Заполнение эмбеддингов чанков на CPU (bge-small) + HNSW/GIN индексы."""

import sys
import time
from datetime import datetime

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from config import EMBED_MODEL, PG_DSN, QUERY_PREFIX

import psycopg

BATCH_TEXTS = 256
FETCH = 2048


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    t0 = time.time()
    with psycopg.connect(PG_DSN) as conn:
        register_vector(conn)
        model = SentenceTransformer(EMBED_MODEL, device="cpu")
        log(f"модель {EMBED_MODEL} загружена за {time.time() - t0:.0f}с")

        done = 0
        last_id = 0
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, body FROM chunks "
                    "WHERE embedding IS NULL AND id > %s ORDER BY id LIMIT %s",
                    (last_id, FETCH),
                )
                rows = cur.fetchall()
            if not rows:
                break

            texts = [f"{t}\n{b}"[:1500] for _i, t, b in rows]
            vecs = model.encode(
                texts, batch_size=BATCH_TEXTS, normalize_embeddings=True,
                convert_to_numpy=True, show_progress_bar=False,
            ).astype(np.float32)

            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE chunks SET embedding=%s WHERE id=%s",
                    [(v, r[0]) for v, r in zip(vecs, rows)],
                )
            conn.commit()

            last_id = rows[-1][0]
            done += len(rows)
            rate = done / (time.time() - t0)
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM chunks WHERE embedding IS NULL")
                left = cur.fetchone()[0]
            log(f"эмбеддено +{done}, осталось {left}, скорость {rate:.0f}/с")

        log("строю индексы HNSW + GIN...")
        with conn.cursor() as cur:
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_hnsw ON chunks "
                "USING hnsw (embedding vector_cosine_ops)"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING gin (tsv)")
        conn.commit()

    total_s = time.time() - t0
    log(f"готово за {total_s / 60:.1f} мин")


if __name__ == "__main__":
    main()
