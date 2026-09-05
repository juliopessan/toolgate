from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

VALID_EVIDENCE_BASIS = {"measured", "estimated", "counterfactual"}


@dataclass(frozen=True)
class ArtifactChange:
    """One delta between two versions of an artifact (assessment, ROM, ADR, ...).

    This is deliberately a patch record, not a snapshot: `change_summary` and
    `affected_objects` describe what moved, so a change is never priced or
    audited as if it were the whole artifact.
    """

    project_id: str
    artifact_id: str
    artifact_type: str
    to_version: str
    change_summary: str
    from_version: str | None = None
    affected_objects: list[str] = field(default_factory=list)
    evidence_basis: str = "estimated"
    decision_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    change_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.evidence_basis not in VALID_EVIDENCE_BASIS:
            raise ValueError(f"invalid evidence_basis: {self.evidence_basis}")


class ChangeHistory:
    """Change History bucket: artifacts evolve through patches, not rewrites."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def migrate(self) -> None:
        migration = Path(__file__).parent / "migrations" / "005_change_history.sql"
        with self.connect() as connection:
            connection.executescript(migration.read_text(encoding="utf-8"))

    def record(self, change: ArtifactChange) -> None:
        values = asdict(change)
        affected = json.dumps(values.pop("affected_objects"), separators=(",", ":"), sort_keys=True)
        metadata = json.dumps(values.pop("metadata"), separators=(",", ":"), sort_keys=True)
        columns = list(values) + ["affected_objects_json", "metadata_json"]
        placeholders = ",".join("?" for _ in columns)
        params = [values[column] for column in values] + [affected, metadata]
        with self.connect() as connection:
            connection.execute(
                f"INSERT INTO zwca_artifact_changes ({','.join(columns)}) VALUES ({placeholders})",
                params,
            )

    def history(self, artifact_id: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM zwca_artifact_changes WHERE artifact_id = ? ORDER BY occurred_at",
                (artifact_id,),
            ).fetchall()

    def latest_version(self, artifact_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT to_version FROM zwca_artifact_changes WHERE artifact_id = ? "
                "ORDER BY occurred_at DESC LIMIT 1",
                (artifact_id,),
            ).fetchone()
        return row["to_version"] if row else None

    def between(self, artifact_id: str, from_version: str, to_version: str) -> list[sqlite3.Row]:
        """Changes strictly after `from_version` up to and including `to_version`."""
        rows = self.history(artifact_id)
        start = next((i for i, row in enumerate(rows) if row["to_version"] == from_version), -1) + 1
        end = next((i for i, row in enumerate(rows) if row["to_version"] == to_version), len(rows) - 1)
        return rows[start : end + 1]
