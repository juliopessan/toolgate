from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class LedgerEvent:
    session_id: str
    artifact_id: str
    event_type: str
    gate: str
    decision: str
    complexity_score: float | None = None
    tier: str | None = None
    provider: str | None = None
    model: str | None = None
    tokens_candidate: int = 0
    tokens_admitted: int = 0
    tokens_transmitted: int = 0
    tokens_rejected: int = 0
    estimated_cost_usd: float = 0.0
    actual_cost_usd: float | None = None
    artifact_budget_usd: float | None = None
    session_budget_usd: float | None = None
    recompress_attempt: int = 0
    quality_status: str | None = None
    evidence_basis: str = "estimated"
    reason_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class WasteLedger:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def migrate(self) -> None:
        migration = Path(__file__).parent / "migrations" / "002_waste_ledger.sql"
        with self.connect() as connection:
            connection.executescript(migration.read_text(encoding="utf-8"))

    def ensure_session(self, session_id: str, project_id: str, budget_usd: float | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO zwca_sessions(session_id, project_id, budget_usd, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    budget_usd = COALESCE(excluded.budget_usd, zwca_sessions.budget_usd),
                    updated_at = excluded.updated_at
                """,
                (session_id, project_id, budget_usd, now, now),
            )

    def append(self, event: LedgerEvent) -> None:
        values = asdict(event)
        metadata = json.dumps(values.pop("metadata"), separators=(",", ":"), sort_keys=True)
        columns = list(values) + ["metadata_json"]
        placeholders = ",".join("?" for _ in columns)
        params = [values[column] for column in values] + [metadata]
        with self.connect() as connection:
            connection.execute(
                f"INSERT INTO waste_ledger_events ({','.join(columns)}) VALUES ({placeholders})",
                params,
            )
            if event.actual_cost_usd is not None:
                connection.execute(
                    """
                    UPDATE zwca_sessions
                    SET spent_usd = spent_usd + ?, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (event.actual_cost_usd, event.occurred_at, event.session_id),
                )

    def session_spend(self, session_id: str) -> float:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT spent_usd FROM zwca_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return float(row["spent_usd"]) if row else 0.0

    def artifact_spend(self, artifact_id: str) -> float:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(actual_cost_usd), 0) AS spent
                FROM waste_ledger_events WHERE artifact_id = ?
                """,
                (artifact_id,),
            ).fetchone()
        return float(row["spent"])
