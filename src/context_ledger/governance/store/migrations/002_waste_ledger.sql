PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS zwca_sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    budget_usd REAL,
    spent_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS waste_ledger_events (
    event_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    session_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    gate TEXT NOT NULL,
    decision TEXT NOT NULL,
    complexity_score REAL,
    tier TEXT,
    provider TEXT,
    model TEXT,
    tokens_candidate INTEGER NOT NULL DEFAULT 0,
    tokens_admitted INTEGER NOT NULL DEFAULT 0,
    tokens_transmitted INTEGER NOT NULL DEFAULT 0,
    tokens_rejected INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    actual_cost_usd REAL,
    artifact_budget_usd REAL,
    session_budget_usd REAL,
    recompress_attempt INTEGER NOT NULL DEFAULT 0,
    quality_status TEXT,
    evidence_basis TEXT NOT NULL DEFAULT 'estimated',
    reason_code TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES zwca_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_waste_ledger_session_time
    ON waste_ledger_events(session_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_waste_ledger_artifact_time
    ON waste_ledger_events(artifact_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_waste_ledger_gate_decision
    ON waste_ledger_events(gate, decision);
