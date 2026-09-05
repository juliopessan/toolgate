PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS zwca_artifact_changes (
    change_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    project_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    from_version TEXT,
    to_version TEXT NOT NULL,
    change_summary TEXT NOT NULL,
    affected_objects_json TEXT NOT NULL DEFAULT '[]',
    evidence_basis TEXT NOT NULL DEFAULT 'estimated'
        CHECK (evidence_basis IN ('measured', 'estimated', 'counterfactual')),
    decision_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (decision_id) REFERENCES zwca_decisions(decision_id)
);

CREATE INDEX IF NOT EXISTS idx_artifact_changes_artifact_time
    ON zwca_artifact_changes(artifact_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_artifact_changes_project_time
    ON zwca_artifact_changes(project_id, occurred_at);
