PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS zwca_budget_reservations (
    reservation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    estimated_cost_usd REAL NOT NULL CHECK (estimated_cost_usd >= 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'committed', 'released', 'expired')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    committed_cost_usd REAL,
    FOREIGN KEY (session_id) REFERENCES zwca_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_zwca_reservations_session_status
    ON zwca_budget_reservations(session_id, status);
CREATE INDEX IF NOT EXISTS idx_zwca_reservations_artifact_status
    ON zwca_budget_reservations(artifact_id, status);
CREATE INDEX IF NOT EXISTS idx_zwca_reservations_expiry
    ON zwca_budget_reservations(expires_at, status);
