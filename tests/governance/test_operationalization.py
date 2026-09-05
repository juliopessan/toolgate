from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tollgate.governance.runtime.compressors import (
    ASTAwareCompressor,
    ConservativeCompressor,
    ContextCompressor,
    FallbackCompressor,
)
from tollgate.governance.runtime.policy_loader import load_tier_policies
from tollgate.governance.store.budget_reservations import BudgetReservations, ReservationRejected


def test_policy_loader_reads_canonical_caps() -> None:
    policies = load_tier_policies(Path("config/tollgate-dispatch.yaml"))
    assert policies["horizon"].input_token_cap == 8000
    assert policies["aurora"].output_token_cap == 12000


def test_ast_compressor_reduces_comments() -> None:
    payload = "# comment\n\nclass A:\n    pass\n// note\ndef run():\n    return 1"
    compressed = ASTAwareCompressor().compress(payload, 100)
    assert "comment" not in compressed
    assert "class A" in compressed
    assert "def run" in compressed


def test_context_compressor_keeps_head_and_tail_within_budget() -> None:
    lines = [f"line {i}" for i in range(500)]
    payload = "\n".join(lines)

    compressed = ContextCompressor().compress(payload, target_tokens=300)

    from tollgate.context.tokens import estimate_tokens

    assert estimate_tokens(compressed) <= 300
    assert "line 0" in compressed
    assert "line 499" in compressed
    assert "elided" in compressed


def test_context_compressor_is_a_guardian_compatible_callable() -> None:
    compressor = ContextCompressor()
    assert compressor("short payload", target_tokens=1000) == "short payload"


def _reservation_db(tmp_path: Path) -> Path:
    db = tmp_path / "ledger.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE tollgate_sessions(session_id TEXT PRIMARY KEY);
            CREATE TABLE waste_ledger_events(
              session_id TEXT, artifact_id TEXT, actual_cost_usd REAL
            );
            """
        )
        conn.execute("INSERT INTO tollgate_sessions(session_id) VALUES ('s1')")
        conn.executescript(Path("src/tollgate/governance/store/migrations/003_budget_reservations.sql").read_text())
    return db


def test_active_reservation_prevents_parallel_overspend(tmp_path: Path) -> None:
    reservations = BudgetReservations(_reservation_db(tmp_path))
    reservations.reserve(
        session_id="s1", artifact_id="a1", estimated_cost_usd=6,
        session_budget_usd=10, artifact_budget_usd=10,
    )
    with pytest.raises(ReservationRejected):
        reservations.reserve(
            session_id="s1", artifact_id="a1", estimated_cost_usd=6,
            session_budget_usd=10, artifact_budget_usd=10,
        )


def test_fallback_compressor_is_callable() -> None:
    compressor = FallbackCompressor(
        primary=ConservativeCompressor(), fallback=ConservativeCompressor()
    )
    assert compressor("abcdefgh", 1) == "abcd"
