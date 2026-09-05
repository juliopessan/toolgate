from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = REPO_ROOT / "hooks" / "pre_call_guardian.py"


def _run_hook(request: dict, tmp_path: Path) -> tuple[int, dict]:
    env = {"TOLLGATE_DB": str(tmp_path / "telemetry.db"), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_an_oversized_payload_is_compressed_by_the_real_context_layer(tmp_path: Path) -> None:
    lines = [f"line {i}" for i in range(3000)]
    payload = "\n".join(lines)
    request = {
        "session_id": "s1",
        "project_id": "p1",
        "artifact_id": "a1",
        "payload": payload,
        "complexity_score": 34.2,
        "tier": "daylight",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "estimated_cost_usd": 0.1,
    }

    code, response = _run_hook(request, tmp_path)

    assert code == 0
    assert response["allow"] is True
    assert "[tollgate] truncated" in response["payload"]
    assert "line 0" in response["payload"]
    assert "line 2999" in response["payload"]
    assert response["recompress_attempt"] >= 1


def test_a_missing_score_is_blocked(tmp_path: Path) -> None:
    request = {
        "session_id": "s1",
        "project_id": "p1",
        "artifact_id": "a1",
        "payload": "hello",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "estimated_cost_usd": 0.1,
    }

    code, response = _run_hook(request, tmp_path)

    assert code == 2
    assert response["allow"] is False
    assert response["reason"] == "no score, no call"
