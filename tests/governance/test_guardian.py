from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from context_ledger.governance.runtime.guardian import CallEnvelope, Guardian, GuardianBlocked, TierPolicy
from context_ledger.governance.store.waste_ledger import WasteLedger


def count_tokens(value: str) -> int:
    return len(value.split())


def halve(payload: str, target_tokens: int) -> str:
    words = payload.split()
    return " ".join(words[: max(target_tokens, len(words) // 2)])


@pytest.fixture()
def ledger(tmp_path: Path) -> WasteLedger:
    result = WasteLedger(tmp_path / "telemetry.db")
    result.migrate()
    return result


@pytest.fixture()
def guardian(ledger: WasteLedger) -> Guardian:
    return Guardian(
        ledger,
        {"daylight": TierPolicy("daylight", input_token_cap=4, output_token_cap=2)},
        count_tokens,
        halve,
    )


def envelope(**overrides: object) -> CallEnvelope:
    values = {
        "session_id": "session-1",
        "project_id": "project-1",
        "artifact_id": "artifact-1",
        "payload": "one two three",
        "candidate_tokens": 3,
        "complexity_score": 20.0,
        "tier": "daylight",
        "provider": "test",
        "model": "small",
        "estimated_cost_usd": 0.10,
        "artifact_budget_usd": 1.0,
        "session_budget_usd": 2.0,
    }
    values.update(overrides)
    return CallEnvelope(**values)


def test_no_score_no_call(guardian: Guardian) -> None:
    with pytest.raises(GuardianBlocked, match="no score, no call"):
        guardian.enforce(envelope(complexity_score=None))


def test_recompresses_until_within_cap(guardian: Guardian) -> None:
    result = guardian.enforce(
        envelope(payload="one two three four five six seven eight", candidate_tokens=8)
    )
    assert result.admitted_tokens == 4
    assert result.envelope.recompress_attempt == 1
    assert result.rejected_tokens == 4


def test_blocks_artifact_budget(guardian: Guardian, ledger: WasteLedger) -> None:
    ledger.ensure_session("session-1", "project-1", 2.0)
    admitted = guardian.enforce(envelope(estimated_cost_usd=0.6))
    guardian.record_completion(admitted, actual_cost_usd=0.6, output_tokens=1, quality_status="passed")
    with pytest.raises(GuardianBlocked, match="artifact budget exceeded"):
        guardian.enforce(envelope(estimated_cost_usd=0.5))


def test_completion_updates_spend_and_audit_event(guardian: Guardian, ledger: WasteLedger) -> None:
    admitted = guardian.enforce(envelope())
    guardian.record_completion(admitted, actual_cost_usd=0.25, output_tokens=2, quality_status="passed")
    assert ledger.session_spend("session-1") == pytest.approx(0.25)
    assert ledger.artifact_spend("artifact-1") == pytest.approx(0.25)
    with sqlite3.connect(ledger.db_path) as connection:
        decisions = [row[0] for row in connection.execute(
            "SELECT decision FROM waste_ledger_events ORDER BY occurred_at"
        )]
    assert decisions == ["admitted", "completed"]


def test_output_cap_is_enforced(guardian: Guardian) -> None:
    admitted = guardian.enforce(envelope())
    with pytest.raises(GuardianBlocked, match="output exceeded"):
        guardian.record_completion(admitted, actual_cost_usd=0.2, output_tokens=3, quality_status="failed")
