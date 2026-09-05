from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = REPO_ROOT / "hooks" / "claude_code_pretooluse.py"


def _run_hook(request: dict, tmp_path: Path) -> dict:
    env = {"TOLLGATE_DB": str(tmp_path / "telemetry.db"), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _task_request(prompt: str, tool_use_id: str = "toolu_01") -> dict:
    return {
        "session_id": "session-1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Task",
        "cwd": "/home/user/some-project",
        "tool_use_id": tool_use_id,
        "tool_input": {"description": "test task", "subagent_type": "Explore", "prompt": prompt},
    }


def test_a_small_prompt_is_admitted_unchanged(tmp_path: Path) -> None:
    response = _run_hook(_task_request("What does foo() return?"), tmp_path)

    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "allow"
    assert "updatedInput" not in output


def test_an_oversized_prompt_is_compressed_and_rewritten_via_updated_input(tmp_path: Path) -> None:
    huge_prompt = "\n".join(f"context line {i} with filler text to pad it out" for i in range(4000))
    response = _run_hook(_task_request(huge_prompt), tmp_path)

    output = response["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    updated = output["updatedInput"]
    assert updated["description"] == "test task"
    assert updated["subagent_type"] == "Explore"
    assert len(updated["prompt"]) < len(huge_prompt)
    assert "[tollgate] truncated" in updated["prompt"]
    assert "context line 0" in updated["prompt"]


def test_fields_other_than_prompt_pass_through_untouched(tmp_path: Path) -> None:
    request = _task_request("short prompt")
    request["tool_input"]["extra_flag"] = True

    response = _run_hook(request, tmp_path)

    output = response["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    assert "updatedInput" not in output
