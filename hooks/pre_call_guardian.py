#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from tollgate.context.tokens import estimate_tokens  # noqa: E402
from tollgate.governance.runtime.compressors import ContextCompressor  # noqa: E402
from tollgate.governance.runtime.guardian import CallEnvelope, Guardian, GuardianBlocked, TierPolicy  # noqa: E402
from tollgate.governance.store.waste_ledger import WasteLedger  # noqa: E402


def default_policies() -> dict[str, TierPolicy]:
    return {
        "solar": TierPolicy("solar", 0, 0, 0),
        "daylight": TierPolicy("daylight", 4_000, 1_000, 2),
        "horizon": TierPolicy("horizon", 8_000, 2_000, 2),
        "twilight": TierPolicy("twilight", 16_000, 4_000, 2),
        "starlight": TierPolicy("starlight", 32_000, 8_000, 2),
        "aurora": TierPolicy("aurora", 64_000, 16_000, 2),
    }


def main() -> int:
    request = json.load(sys.stdin)
    db_path = Path(os.environ.get("TOLLGATE_DB", "~/.tollgate/telemetry.db")).expanduser()
    ledger = WasteLedger(db_path)
    ledger.migrate()
    guardian = Guardian(ledger, default_policies(), estimate_tokens, ContextCompressor())

    envelope = CallEnvelope(
        session_id=str(request["session_id"]),
        project_id=str(request.get("project_id", "unknown")),
        artifact_id=str(request["artifact_id"]),
        payload=str(request.get("payload", "")),
        candidate_tokens=int(request.get("candidate_tokens") or estimate_tokens(str(request.get("payload", "")))),
        complexity_score=request.get("complexity_score"),
        tier=request.get("tier"),
        provider=str(request.get("provider", "unknown")),
        model=str(request.get("model", "unknown")),
        estimated_cost_usd=float(request.get("estimated_cost_usd", 0.0)),
        artifact_budget_usd=request.get("artifact_budget_usd"),
        session_budget_usd=request.get("session_budget_usd"),
    )

    try:
        result = guardian.enforce(envelope)
    except (GuardianBlocked, KeyError, TypeError, ValueError) as exc:
        json.dump({"allow": False, "reason": str(exc)}, sys.stdout)
        return 2

    json.dump(
        {
            "allow": True,
            "payload": result.envelope.payload,
            "tier": result.envelope.tier,
            "admitted_tokens": result.admitted_tokens,
            "rejected_tokens": result.rejected_tokens,
            "recompress_attempt": result.envelope.recompress_attempt,
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
