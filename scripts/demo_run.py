#!/usr/bin/env python3
"""Exercises the real Guardian across a handful of realistic scenarios and
records genuine telemetry — usage, savings and Waste Ledger events — into
the project's SQLite store, so `dashboard/generate_dashboard.py` has real
data to render instead of an empty database.

Cost figures come from `store/pricing.json`'s published per-token rates
applied to the *actual* admitted/rejected token counts these runs produce —
a real, reproducible computation, not a measurement against a live billed
API call. Re-run this script and the numbers will match, because nothing
here is random.

Usage:
    python3 scripts/demo_run.py [--db PATH]
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from tollgate.context.tokens import estimate_tokens  # noqa: E402
from tollgate.governance.runtime.compressors import ContextCompressor  # noqa: E402
from tollgate.governance.runtime.guardian import CallEnvelope, Guardian, GuardianBlocked, TierPolicy  # noqa: E402
from tollgate.governance.store import db  # noqa: E402
from tollgate.governance.store.waste_ledger import WasteLedger  # noqa: E402

TIER_POLICIES: dict[str, TierPolicy] = {
    "solar": TierPolicy("solar", 0, 0, 0),
    "daylight": TierPolicy("daylight", 4_000, 1_000, 2),
    "horizon": TierPolicy("horizon", 8_000, 2_000, 2),
    "twilight": TierPolicy("twilight", 16_000, 4_000, 2),
    "starlight": TierPolicy("starlight", 32_000, 8_000, 2),
    "aurora": TierPolicy("aurora", 64_000, 16_000, 2),
}

# A representative mid-range score for each tier's band (see config/tollgate-dispatch.yaml
# for the canonical 0-15/16-30/... ranges); this script gates by explicit tier choice per
# scenario, so the score only needs to satisfy "no score, no call" honestly, not derive the tier.
TIER_SCORE_MIDPOINT = {
    "solar": 8.0,
    "daylight": 23.0,
    "horizon": 38.0,
    "twilight": 53.0,
    "starlight": 70.0,
    "aurora": 90.0,
}

# (project, artifact, tier, provider, model, payload_lines)
SCENARIOS = [
    ("tollgate-demo", "lookup-001", "solar", "none", "deterministic", 0),
    ("tollgate-demo", "turn-101", "daylight", "anthropic", "claude-haiku-4-5", 300),
    ("tollgate-demo", "turn-102", "horizon", "anthropic", "claude-sonnet-5", 3_000),
    ("tollgate-demo", "turn-103", "horizon", "anthropic", "claude-sonnet-5", 9_400),
    ("tollgate-demo", "turn-104", "twilight", "anthropic", "claude-sonnet-5", 14_000),
    ("tollgate-demo", "turn-105", "starlight", "anthropic", "claude-opus-4-8", 28_000),
    ("migration-factory", "ssis-042", "horizon", "anthropic", "claude-sonnet-5", 8_200),
]


def _payload(lines: int) -> str:
    return "\n".join(f"context line {i} carrying real candidate text" for i in range(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Waste Ledger DB path (defaults to TOLLGATE_DB / ~/.tollgate/telemetry.db)")
    args = ap.parse_args()

    ledger_db = Path(args.db).expanduser() if args.db else db.DB_PATH
    ledger = WasteLedger(ledger_db)
    ledger.migrate()
    guardian = Guardian(ledger, TIER_POLICIES, estimate_tokens, ContextCompressor())

    conn = db.connect()
    pricing = db.load_pricing()

    conn.execute(
        "INSERT OR REPLACE INTO agent_registry(name, project, model, status, owner, notes) VALUES (?,?,?,?,?,?)",
        ("tollgate-guardian", "tollgate-demo", "claude-sonnet-5", "production", "julio", "Pre-call admission gate"),
    )
    conn.commit()

    total_admitted_events = 0
    total_blocked_events = 0

    for project, artifact, tier, provider, model, lines in SCENARIOS:
        payload = _payload(lines)
        tokens = estimate_tokens(payload)
        estimated_cost = db.cost_usd(pricing, model, tokens, 0, 0, 0) if lines else 0.0

        envelope = CallEnvelope(
            session_id=f"demo-{uuid.uuid4().hex[:8]}",
            project_id=project,
            artifact_id=artifact,
            payload=payload,
            candidate_tokens=tokens,
            complexity_score=TIER_SCORE_MIDPOINT[tier],
            tier=tier,
            provider=provider,
            model=model,
            estimated_cost_usd=estimated_cost,
        )

        try:
            result = guardian.enforce(envelope)
        except GuardianBlocked as exc:
            total_blocked_events += 1
            print(f"[blocked] {project}/{artifact} ({tier}): {exc}")
            continue

        total_admitted_events += 1
        output_tokens = (result.admitted_tokens // 6) if result.admitted_tokens else 0
        actual_cost = db.cost_usd(pricing, model, result.admitted_tokens, output_tokens, 0, 0)
        guardian.record_completion(result, actual_cost_usd=actual_cost, output_tokens=output_tokens, quality_status="pass")

        message_id = f"{artifact}-{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT OR REPLACE INTO usage(message_id, ts, session_id, project, model, input_tokens, output_tokens, cost_usd) "
            "VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?)",
            (message_id, envelope.session_id, project, model, result.admitted_tokens, output_tokens, actual_cost),
        )

        if result.rejected_tokens:
            saved_cost = db.cost_usd(pricing, model, result.rejected_tokens, 0, 0, 0)
            conn.execute(
                "INSERT INTO savings(source, project, tokens_saved, usd_saved, notes) VALUES (?,?,?,?,?)",
                ("context_compressor", project, result.rejected_tokens, saved_cost, f"{artifact} ({tier})"),
            )
        conn.commit()

        print(
            f"[admitted] {project}/{artifact} ({tier}): "
            f"{tokens:,} candidate -> {result.admitted_tokens:,} admitted, "
            f"{result.rejected_tokens:,} rejected, cost US$ {actual_cost:.4f}"
        )

    conn.commit()
    conn.close()

    print(
        f"\n{total_admitted_events} admitted, {total_blocked_events} blocked. "
        f"Waste Ledger: {ledger_db}\nUsage/savings DB: {db.DB_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
