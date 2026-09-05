PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS zwca_decisions (
    decision_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    project_id TEXT NOT NULL,
    session_id TEXT,
    artifact_id TEXT,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    decided_by TEXT,
    status TEXT NOT NULL DEFAULT 'approved'
        CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    supersedes_decision_id TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (supersedes_decision_id) REFERENCES zwca_decisions(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_decisions_project_time
    ON zwca_decisions(project_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_decisions_artifact_time
    ON zwca_decisions(artifact_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_decisions_status
    ON zwca_decisions(status);
