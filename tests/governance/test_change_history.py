from __future__ import annotations

from pathlib import Path

import pytest

from tollgate.governance.store.change_history import ArtifactChange, ChangeHistory


@pytest.fixture()
def history(tmp_path: Path) -> ChangeHistory:
    result = ChangeHistory(tmp_path / "telemetry.db")
    result.migrate()
    return result


def test_record_and_latest_version(history: ChangeHistory) -> None:
    history.record(
        ArtifactChange(
            project_id="allianz",
            artifact_id="rom-1",
            artifact_type="rom",
            to_version="1.0",
            change_summary="initial ROM",
        )
    )
    history.record(
        ArtifactChange(
            project_id="allianz",
            artifact_id="rom-1",
            artifact_type="rom",
            from_version="1.0",
            to_version="1.1",
            change_summary="added SAP dependency, automation coverage dropped 80% -> 55%",
            affected_objects=["Z_SALES_REPORT"],
            evidence_basis="measured",
        )
    )
    assert history.latest_version("rom-1") == "1.1"
    rows = history.history("rom-1")
    assert [row["to_version"] for row in rows] == ["1.0", "1.1"]
    assert rows[1]["evidence_basis"] == "measured"


def test_invalid_evidence_basis_rejected() -> None:
    with pytest.raises(ValueError, match="invalid evidence_basis"):
        ArtifactChange(
            project_id="allianz",
            artifact_id="rom-1",
            artifact_type="rom",
            to_version="1.0",
            change_summary="x",
            evidence_basis="guessed",
        )


def test_between_returns_only_the_intermediate_changes(history: ChangeHistory) -> None:
    for version, summary in [("1.0", "initial"), ("1.1", "a"), ("1.2", "b"), ("1.3", "c")]:
        history.record(
            ArtifactChange(
                project_id="allianz",
                artifact_id="rom-1",
                artifact_type="rom",
                to_version=version,
                change_summary=summary,
            )
        )
    changes = history.between("rom-1", "1.0", "1.2")
    assert [row["to_version"] for row in changes] == ["1.1", "1.2"]


def test_no_evidence_basis_mixing_by_default(history: ChangeHistory) -> None:
    history.record(
        ArtifactChange(
            project_id="allianz",
            artifact_id="rom-1",
            artifact_type="rom",
            to_version="1.0",
            change_summary="x",
        )
    )
    assert history.history("rom-1")[0]["evidence_basis"] == "estimated"
