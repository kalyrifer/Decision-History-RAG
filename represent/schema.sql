CREATE TABLE IF NOT EXISTS chunks (
    id BIGSERIAL PRIMARY KEY,
    entity_id BIGINT NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    idx INT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    n_chunks INT NOT NULL DEFAULT 1,
    embedding vector(384),
    tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', left(body, 20000)), 'B')
    ) STORED,
    UNIQUE (entity_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_chunks_entity ON chunks (entity_id);
