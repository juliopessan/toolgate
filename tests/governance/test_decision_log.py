from __future__ import annotations

from pathlib import Path

import pytest

from context_ledger.governance.store.decision_log import Decision, DecisionLog


@pytest.fixture()
def log(tmp_path: Path) -> DecisionLog:
    result = DecisionLog(tmp_path / "telemetry.db")
    result.migrate()
    return result


def test_record_and_history(log: DecisionLog) -> None:
    log.record(
        Decision(
            project_id="allianz",
            decision="Use Fabric Warehouse",
            reason="Lower operating cost",
            decided_by="architecture-board",
        )
    )
    rows = log.history(project_id="allianz")
    assert len(rows) == 1
    assert rows[0]["decision"] == "Use Fabric Warehouse"
    assert rows[0]["status"] == "approved"


def test_invalid_status_rejected() -> None:
    with pytest.raises(ValueError, match="invalid decision status"):
        Decision(project_id="allianz", decision="x", reason="y", status="final")


def test_superseding_closes_prior_decision(log: DecisionLog) -> None:
    log.record(Decision(project_id="allianz", decision="Use Synapse", reason="initial choice"))
    first_id = log.current(project_id="allianz")[0]["decision_id"]
    log.record(
        Decision(
            project_id="allianz",
            decision="Use Fabric Warehouse",
            reason="Synapse deprecated for new workloads",
            supersedes_decision_id=first_id,
        )
    )
    current = log.current(project_id="allianz")
    assert len(current) == 1
    assert current[0]["decision"] == "Use Fabric Warehouse"

    full_history = log.history(project_id="allianz")
    statuses = {row["decision_id"]: row["status"] for row in full_history}
    assert statuses[first_id] == "superseded"


def test_history_filters_by_artifact(log: DecisionLog) -> None:
    log.record(Decision(project_id="allianz", decision="a", reason="r", artifact_id="assessment-1"))
    log.record(Decision(project_id="allianz", decision="b", reason="r", artifact_id="rom-1"))
    rows = log.history(artifact_id="assessment-1")
    assert len(rows) == 1
    assert rows[0]["decision"] == "a"
