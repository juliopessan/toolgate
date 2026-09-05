from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

VALID_STATUSES = {"proposed", "approved", "rejected", "superseded"}


@dataclass(frozen=True)
class Decision:
    project_id: str
    decision: str
    reason: str
    decided_by: str | None = None
    session_id: str | None = None
    artifact_id: str | None = None
    status: str = "approved"
    supersedes_decision_id: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    decision_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid decision status: {self.status}")


class DecisionLog:
    """Memory bucket: durable record of decisions, not documentation.

    Recording a decision that supersedes an earlier one automatically closes
    the prior decision out (status -> superseded) so `current()` always
    reflects the decisions still in force for a project.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def migrate(self) -> None:
        migration = Path(__file__).parent / "migrations" / "004_decision_log.sql"
        with self.connect() as connection:
            connection.executescript(migration.read_text(encoding="utf-8"))

    def record(self, decision: Decision) -> None:
        values = asdict(decision)
        tags = json.dumps(values.pop("tags"), separators=(",", ":"), sort_keys=True)
        metadata = json.dumps(values.pop("metadata"), separators=(",", ":"), sort_keys=True)
        columns = list(values) + ["tags_json", "metadata_json"]
        placeholders = ",".join("?" for _ in columns)
        params = [values[column] for column in values] + [tags, metadata]
        with self.connect() as connection:
            if decision.supersedes_decision_id:
                connection.execute(
                    "UPDATE zwca_decisions SET status = 'superseded' WHERE decision_id = ?",
                    (decision.supersedes_decision_id,),
                )
            connection.execute(
                f"INSERT INTO zwca_decisions ({','.join(columns)}) VALUES ({placeholders})",
                params,
            )

    def history(
        self, *, project_id: str | None = None, artifact_id: str | None = None
    ) -> list[sqlite3.Row]:
        clauses = []
        params: list[str] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if artifact_id is not None:
            clauses.append("artifact_id = ?")
            params.append(artifact_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            return connection.execute(
                f"SELECT * FROM zwca_decisions {where} ORDER BY occurred_at", params
            ).fetchall()

    def current(self, *, project_id: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM zwca_decisions "
                "WHERE project_id = ? AND status != 'superseded' "
                "ORDER BY occurred_at",
                (project_id,),
            ).fetchall()
