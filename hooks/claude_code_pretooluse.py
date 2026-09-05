#!/usr/bin/env python3
"""PreToolUse bridge: adapts Claude Code's hook contract to Tollgate's Guardian.

Claude Code's PreToolUse hook sends a JSON object shaped like
``{"tool_name", "tool_input", "session_id", "cwd", "tool_use_id", ...}`` — not
the ``{"complexity_score", "tier", "payload", ...}`` envelope
``hooks/pre_call_guardian.py`` expects, and it never computes a complexity
score for you (Claude Code doesn't do structural analysis of tool input; that
is this project's job). This script is the missing translation layer.

Two things it deliberately does NOT reuse from the rest of this repository:

1. It does not call ``scripts/complexity_score.py``. That scorer's features
   (``ast_node_count``, ``dependency_depth``, ...) describe a legacy
   migration artifact, not an arbitrary tool call — see docs/EXTENDING.md.
   Wire your own scorer in its place once you have one.
2. It does not use ``config/tollgate-dispatch.yaml``'s six Solar-to-Aurora
   tiers. Those model *how much LLM reasoning an artifact deserves* — Solar's
   zero-token cap means "handle this with no LLM at all", which is correct
   for a trivial migration lookup but wrong for, say, a small Bash command:
   small payload size does not mean "give this zero tokens", it means "this
   compresses to a small budget". Reusing those tier IDs here would silently
   block small, legitimate tool calls. The three buckets below are payload
   size compression budgets only, not a reasoning-tier decision.

Registered in hooks.json against ``Task`` and ``mcp__.*``: a subagent's
prompt, and an MCP tool call's own free-text argument, are the PreToolUse
payloads that actually resemble context handed to another LLM turn or an
external service. Claude Code does not expose a hook on its own
model/provider calls, so these are the closest available interception
points.

MCP tool schemas vary per server, so there is no single field name to gate
the way ``Task`` always has ``prompt``. ``_pick_gated_field`` tries a short
list of common free-text argument names (``prompt``, ``query``, ``content``,
``text``, ``input``, ``message``) and gates whichever one is present as a
non-empty string. If none match, the whole ``tool_input`` is scored and
capped for admission, but — because there is no single field to safely
rewrite — a compression is not written back via ``updatedInput``; only a
hard block (``GuardianBlocked``, e.g. the payload is too large to admit at
all) has any effect in that fallback case. Add the tool's actual field name
to the candidate list, or write a tool-specific extractor, before relying on
this bridge to compress that tool's payloads in place.
"""
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

# Payload-size buckets. Distinct from config/tollgate-dispatch.yaml — see module docstring.
BRIDGE_POLICIES: dict[str, TierPolicy] = {
    "small": TierPolicy("small", 2_000, 500, 2),
    "medium": TierPolicy("medium", 8_000, 2_000, 2),
    "large": TierPolicy("large", 24_000, 6_000, 2),
}

# The score ceiling below is only what turns a token count into the [0, 100]
# figure "no score, no call" requires bookkeeping-wise; it does not drive the
# tier choice (tier_for_tokens does, directly, from the token count itself).
SCORE_CEILING_TOKENS = 24_000


def tier_for_tokens(tokens: int) -> str:
    if tokens <= BRIDGE_POLICIES["small"].input_token_cap:
        return "small"
    if tokens <= BRIDGE_POLICIES["medium"].input_token_cap:
        return "medium"
    return "large"


def payload_size_score(tokens: int) -> float:
    return max(0.0, min(100.0, (tokens / SCORE_CEILING_TOKENS) * 100))


# Common free-text argument names across Task and MCP tool schemas, in
# priority order. The first present non-empty string field is gated; every
# other field on the tool passes through untouched.
GATED_FIELD_CANDIDATES = ("prompt", "query", "content", "text", "input", "message")


def pick_gated_field(tool_input: dict) -> str | None:
    for name in GATED_FIELD_CANDIDATES:
        value = tool_input.get(name)
        if isinstance(value, str) and value:
            return name
    return None


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Tollgate: {reason}",
        }
    }


def _allow(tier: str, score: float, updated_input: dict | None = None) -> dict:
    output = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": f"Tollgate: admitted (tier={tier}, score={score:.1f})",
    }
    if updated_input is not None:
        output["updatedInput"] = updated_input
    return {"hookSpecificOutput": output}


def main() -> int:
    request = json.load(sys.stdin)
    tool_name = str(request.get("tool_name", "unknown"))
    tool_input = dict(request.get("tool_input", {}))
    gated_field = pick_gated_field(tool_input)
    payload = tool_input[gated_field] if gated_field else json.dumps(tool_input, ensure_ascii=False)

    tokens = estimate_tokens(payload)
    score = payload_size_score(tokens)
    tier = tier_for_tokens(tokens)

    db_path = Path(os.environ.get("TOLLGATE_DB", "~/.tollgate/telemetry.db")).expanduser()
    ledger = WasteLedger(db_path)
    ledger.migrate()
    guardian = Guardian(ledger, BRIDGE_POLICIES, estimate_tokens, ContextCompressor())

    project_id = Path(str(request.get("cwd", "."))).name or "unknown"
    envelope = CallEnvelope(
        session_id=str(request.get("session_id", "unknown")),
        project_id=project_id,
        artifact_id=str(request.get("tool_use_id", tool_name)),
        payload=payload,
        candidate_tokens=tokens,
        complexity_score=score,
        tier=tier,
        provider="claude-code",
        model=tool_name,
        estimated_cost_usd=0.0,
    )

    try:
        result = guardian.enforce(envelope)
    except GuardianBlocked as exc:
        json.dump(_deny(str(exc)), sys.stdout)
        return 0

    updated_input = None
    if gated_field and result.envelope.payload != payload:
        updated_input = {**tool_input, gated_field: result.envelope.payload}

    json.dump(_allow(tier, score, updated_input), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
