-- ParcelPilot schema (docs/02_DOMAIN_SPEC.md §1 + ADR-001).
-- Single SQLite file: relational tables + FTS5 virtual table over doc chunks.

CREATE TABLE IF NOT EXISTS accounts (
    account_id      TEXT PRIMARY KEY,
    account_name    TEXT NOT NULL,
    plan            TEXT NOT NULL,
    status          TEXT NOT NULL,
    csm             TEXT,
    contract_file   TEXT,
    premium_support INTEGER NOT NULL DEFAULT 0,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    order_id                 TEXT PRIMARY KEY,
    account_id               TEXT NOT NULL REFERENCES accounts(account_id),
    carrier                  TEXT NOT NULL,
    status                   TEXT NOT NULL,
    booked_at                TEXT NOT NULL,
    pickup_window_start      TEXT NOT NULL,
    pickup_window_end        TEXT NOT NULL,
    pickup_actual_at         TEXT,
    shipment_fee_inr         INTEGER NOT NULL,
    carrier_fault            INTEGER,          -- NULL = unknown (INSUFFICIENT_EVIDENCE path)
    customer_fault           INTEGER,          -- NULL = unknown
    cancellation_requested_at TEXT,
    notes                    TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id                 TEXT PRIMARY KEY,
    account_id                TEXT NOT NULL REFERENCES accounts(account_id),
    created_at                TEXT NOT NULL,
    status                    TEXT NOT NULL,
    subject                   TEXT NOT NULL,
    description               TEXT NOT NULL,
    channel                   TEXT,
    assigned_to               TEXT,
    last_customer_message_at  TEXT,
    -- Context only, NEVER a policy source (workbook README + DOMAIN_SPEC §1).
    historical_resolution     TEXT
);

-- Mocked state-changing actions (03_AGENT_SPEC.md §4). Empty until the agent
-- drafts one; confirmation validation lives in the trusted layer (Phase 9).
CREATE TABLE IF NOT EXISTS actions (
    action_id    TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','confirmed','executed','rejected','expired')),
    token        TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    confirmed_at TEXT
);

-- Document/evidence chunks. Metadata preserves status, effective date, scope
-- and authority rank (DOMAIN_SPEC §2). authority_rank: 1 agreement, 2 current
-- policy/SOP, 3 current product docs, 4 deprecated docs, 5 historical
-- resolutions (context only).
CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id       INTEGER PRIMARY KEY,
    source_doc     TEXT NOT NULL,   -- document file stem, or 'tickets' for resolutions
    section        TEXT NOT NULL,
    status         TEXT NOT NULL,   -- CURRENT | ACTIVE | DEPRECATED | HISTORICAL_RESOLUTION
    effective      TEXT,            -- effective/updated date as stated in the document
    scope          TEXT NOT NULL,   -- 'GENERAL' or an account_id
    authority_rank INTEGER NOT NULL,
    text           TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content='document_chunks',
    content_rowid='chunk_id',
    tokenize='porter unicode61'
);
