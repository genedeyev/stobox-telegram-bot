-- Reference schema. The app creates these tables automatically on first run
-- (see knowledge/store.py, memory/store.py, analytics/logger.py). This file is
-- for DBAs, migrations tooling, and review. Replace {DIM} with the configured
-- embedding dimensions (default 1024).

CREATE EXTENSION IF NOT EXISTS vector;

-- Knowledge base chunks (RAG).
CREATE TABLE IF NOT EXISTS kb_chunks (
    chunk_id     TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL,
    content_hash TEXT,
    ordinal      INT,
    section      TEXT,
    text         TEXT NOT NULL,
    summary      TEXT,
    keywords     TEXT[],
    embedding    vector(1024),
    meta         JSONB
);
CREATE INDEX IF NOT EXISTS kb_doc_idx ON kb_chunks(doc_id);
CREATE INDEX IF NOT EXISTS kb_vec_idx ON kb_chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Long-term per-user memory.
CREATE TABLE IF NOT EXISTS user_profiles (
    user_key TEXT PRIMARY KEY,
    data     JSONB NOT NULL,
    updated  TIMESTAMPTZ DEFAULT now()
);

-- Decision / audit log (observability).
CREATE TABLE IF NOT EXISTS decision_log (
    id   BIGSERIAL PRIMARY KEY,
    at   TIMESTAMPTZ,
    data JSONB
);
CREATE INDEX IF NOT EXISTS decision_at_idx ON decision_log(at);
