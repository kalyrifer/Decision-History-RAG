CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS entities (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('issue', 'pr', 'comment', 'commit', 'file')),
    native_id TEXT NOT NULL,
    title TEXT,
    body TEXT,
    author TEXT,
    email TEXT,
    created_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    merged_at TIMESTAMPTZ,
    state TEXT,
    url TEXT,
    extra JSONB NOT NULL DEFAULT '{}',
    UNIQUE (kind, native_id)
);

-- миграция: разрешить kind='file' на уже созданной таблице
ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_kind_check;
ALTER TABLE entities ADD CONSTRAINT entities_kind_check
    CHECK (kind IN ('issue', 'pr', 'comment', 'commit', 'file'));

CREATE INDEX IF NOT EXISTS idx_entities_kind_native ON entities (kind, native_id);
CREATE INDEX IF NOT EXISTS idx_entities_created ON entities (created_at);

CREATE TABLE IF NOT EXISTS files (
    entity_id BIGINT NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    status TEXT,
    add_count INTEGER,
    del_count INTEGER,
    old_path TEXT,
    PRIMARY KEY (entity_id, path)
);

ALTER TABLE files ADD COLUMN IF NOT EXISTS old_path TEXT;

CREATE INDEX IF NOT EXISTS idx_files_path ON files (path);

CREATE TABLE IF NOT EXISTS relations (
    src_id BIGINT NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    dst_id BIGINT NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('closes', 'references', 'mentions', 'pr_commit', 'touches_file', 'parent')),
    source TEXT NOT NULL CHECK (source IN ('timeline', 'api', 'regex')),
    meta JSONB NOT NULL DEFAULT '{}',
    UNIQUE (src_id, dst_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_relations_src ON relations (src_id);
CREATE INDEX IF NOT EXISTS idx_relations_dst ON relations (dst_id);
CREATE INDEX IF NOT EXISTS idx_relations_kind ON relations (kind);
