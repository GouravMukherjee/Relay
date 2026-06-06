-- Relay schema — PostgreSQL 15 + pgvector. Mirrors docs/DATA_MODEL.md.
-- Auto-run by the postgres container on first boot (mounted to
-- /docker-entrypoint-initdb.d). For managed Postgres, run this file once by hand.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Org / users ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS organizations (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id uuid REFERENCES organizations(id),
    name            text,
    role            text
);

-- ── Documents / chunks ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id uuid REFERENCES organizations(id),
    title           text,
    source_type     text CHECK (source_type IN ('pdf','docx','txt')),
    status          text NOT NULL DEFAULT 'processing'
                        CHECK (status IN ('processing','ready','failed')),
    tags            text[] DEFAULT '{}',
    chunk_count     int DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id uuid REFERENCES documents(id) ON DELETE CASCADE,
    ordinal     int NOT NULL,
    text        text NOT NULL,
    embedding   vector(1024),     -- pgvector fallback index
    moss_ref    text,             -- handle in the Moss index (primary path)
    metadata    jsonb DEFAULT '{}'
);

-- Fallback ANN index (Moss is the primary <10ms path).
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS chunks_document_idx ON chunks(document_id);

-- ── Customers / memory ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS customers (
    id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id uuid REFERENCES organizations(id),
    name            text,
    company         text,
    email           text
);

CREATE TABLE IF NOT EXISTS memories (
    id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id       uuid REFERENCES customers(id) ON DELETE CASCADE,
    kind              text CHECK (kind IN ('fact','summary','preference')),
    text              text NOT NULL,
    embedding         vector(1024),
    source_session_id uuid,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS memories_embedding_idx
    ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ── Sessions / utterances / cards ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id uuid REFERENCES organizations(id),
    mode            text NOT NULL CHECK (mode IN ('live','desk','intake')),
    customer_id     uuid REFERENCES customers(id),
    livekit_room    text,
    status          text NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','ended')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz
);

CREATE TABLE IF NOT EXISTS utterances (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id  uuid REFERENCES sessions(id) ON DELETE CASCADE,
    speaker     text,
    text        text NOT NULL,
    ts          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS utterances_session_idx ON utterances(session_id);

CREATE TABLE IF NOT EXISTS cards (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id   uuid REFERENCES sessions(id) ON DELETE CASCADE,
    mode         text,
    answer       text NOT NULL,
    trigger_text text,
    latency_ms   int,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cards_session_idx ON cards(session_id);

CREATE TABLE IF NOT EXISTS card_sources (
    card_id  uuid REFERENCES cards(id) ON DELETE CASCADE,
    chunk_id uuid REFERENCES chunks(id) ON DELETE CASCADE,
    score    float,
    PRIMARY KEY (card_id, chunk_id)
);

-- ── Leads (Intake) ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS leads (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id  uuid REFERENCES sessions(id) ON DELETE CASCADE,
    name        text,
    company     text,
    email       text,
    qualifiers  jsonb DEFAULT '{}',
    score       int CHECK (score BETWEEN 0 AND 100),
    status      text CHECK (status IN ('hot','warm','cold')),
    routed_to   text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Default single-org row for the hackathon (forward-compatible org_id).
INSERT INTO organizations (id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'Default Org')
ON CONFLICT (id) DO NOTHING;
