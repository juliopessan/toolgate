from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ReservationRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    session_id: str
    artifact_id: str
    estimated_cost_usd: float
    expires_at: str


class BudgetReservations:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    def reserve(
        self,
        *,
        session_id: str,
        artifact_id: str,
        estimated_cost_usd: float,
        session_budget_usd: float | None,
        artifact_budget_usd: float | None,
        ttl_seconds: int = 300,
    ) -> Reservation:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl_seconds)
        reservation_id = str(uuid.uuid4())
        with sqlite3.connect(self.db_path, isolation_level=None) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE zwca_budget_reservations SET status='expired' "
                "WHERE status='active' AND expires_at <= ?",
                (now.isoformat(),),
            )
            session_spend = self._scalar(
                conn,
                "SELECT COALESCE(SUM(actual_cost_usd),0) FROM waste_ledger_events "
                "WHERE session_id=? AND actual_cost_usd IS NOT NULL",
                (session_id,),
            )
            session_reserved = self._scalar(
                conn,
                "SELECT COALESCE(SUM(estimated_cost_usd),0) FROM zwca_budget_reservations "
                "WHERE session_id=? AND status='active'",
                (session_id,),
            )
            artifact_spend = self._scalar(
                conn,
                "SELECT COALESCE(SUM(actual_cost_usd),0) FROM waste_ledger_events "
                "WHERE artifact_id=? AND actual_cost_usd IS NOT NULL",
                (artifact_id,),
            )
            artifact_reserved = self._scalar(
                conn,
                "SELECT COALESCE(SUM(estimated_cost_usd),0) FROM zwca_budget_reservations "
                "WHERE artifact_id=? AND status='active'",
                (artifact_id,),
            )
            if session_budget_usd is not None and session_spend + session_reserved + estimated_cost_usd > session_budget_usd:
                conn.execute("ROLLBACK")
                raise ReservationRejected("session budget unavailable")
            if artifact_budget_usd is not None and artifact_spend + artifact_reserved + estimated_cost_usd > artifact_budget_usd:
                conn.execute("ROLLBACK")
                raise ReservationRejected("artifact budget unavailable")
            conn.execute(
                "INSERT INTO zwca_budget_reservations "
                "(reservation_id,session_id,artifact_id,estimated_cost_usd,status,expires_at) "
                "VALUES (?,?,?,?, 'active', ?)",
                (reservation_id, session_id, artifact_id, estimated_cost_usd, expires.isoformat()),
            )
            conn.execute("COMMIT")
        return Reservation(reservation_id, session_id, artifact_id, estimated_cost_usd, expires.isoformat())

    def commit(self, reservation_id: str, actual_cost_usd: float) -> None:
        self._transition(reservation_id, "committed", actual_cost_usd)

    def release(self, reservation_id: str) -> None:
        self._transition(reservation_id, "released", None)

    def _transition(self, reservation_id: str, status: str, cost: float | None) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE zwca_budget_reservations SET status=?, committed_cost_usd=? "
                "WHERE reservation_id=? AND status='active'",
                (status, cost, reservation_id),
            )
            if cur.rowcount != 1:
                raise ReservationRejected("reservation is not active")

    @staticmethod
    def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> float:
        row = conn.execute(sql, params).fetchone()
        return float(row[0] if row else 0.0)
