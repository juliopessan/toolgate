---
name: tollgate
description: Enforce the Tollgate admission gate across deterministic scoring, context compression, thermal-tier dispatch, token budgets and audit. Use when the user asks to optimize, route, govern, audit an AI-agent workload, or wire the pre-call gate into a project or a Claude Code hook.
---

# Tollgate Runtime

Use this skill when the user asks to score, route, govern or audit an AI-agent
workload through Tollgate — or to wire its admission gate into a project.

## Operating contract

Every candidate payload must pass these gates in order:

1. **Admission** — does it deserve to enter? No score, no call.
2. **Compression** — can it be smaller? Compress on whole-line boundaries,
   never a blind truncation.
3. **Audit** — did it generate accepted value? Record token movement, cost
   and quality in the Waste Ledger.

Never invoke a model when no deterministic complexity score is available.
Never exceed a tier's token cap without the configured exception path.

## Two real entry points — use these, not a hypothetical harness

Tollgate ships two working implementations of the contract above. Pick
based on what's calling it:

### A. Generic stdin/stdout gate — `hooks/pre_call_guardian.py`

For any project, any language, in front of any provider call:

```bash
export TOLLGATE_DB=~/.tollgate/telemetry.db
echo '{
  "session_id": "session-001", "project_id": "my-agent", "artifact_id": "turn-042",
  "payload": "...", "complexity_score": 34.2, "tier": "horizon",
  "provider": "anthropic", "model": "claude-sonnet-5", "estimated_cost_usd": 0.42
}' | python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pre_call_guardian.py
```

Exit `0` + admitted `payload` = send it. Exit `2` + `{"allow": false, "reason": ...}`
= do not call the provider. `complexity_score` and `tier` are not computed
for you here — see "Scoring your own artifacts" below.

### B. Claude Code `PreToolUse` bridge — `hooks/claude_code_pretooluse.py`

Registered in `hooks/hooks.json` against `^(Task|mcp__.*)$`. Claude Code
sends `tool_name`/`tool_input`/`session_id`/`cwd`/`tool_use_id` — not the
envelope above — so this bridge translates it, gates the one free-text
field in `tool_input` that looks like context bound for another LLM turn or
external service (`pick_gated_field` tries `prompt`, `query`, `content`,
`text`, `input`, `message` in that order), and — when it compresses
something — rewrites it in place via the hook's `updatedInput` field, so
the tool call actually runs on the smaller payload.

**To extend it to another tool**, same pattern, two options and nothing else
to touch:

1. The tool's free-text field is already in `GATED_FIELD_CANDIDATES` (in
   `hooks/claude_code_pretooluse.py`) → just widen the `hooks.json` matcher
   regex to include it, e.g. `^(Task|mcp__.*|Bash)$` to also gate `Bash`'s
   `command` field once you add `"command"` to the candidate list.
2. The tool's field name isn't a candidate yet → add it to
   `GATED_FIELD_CANDIDATES`, in priority order if more than one tool-specific
   name could collide.

This bridge deliberately does **not** reuse `config/tollgate-dispatch.yaml`'s
six Solar-to-Aurora tiers (those model how much LLM *reasoning* an artifact
deserves — a small payload is not "give this zero tokens"). It uses its own
three payload-size buckets (`BRIDGE_POLICIES` in the same file). Don't
"fix" this by pointing it at the dispatch policy; that would start blocking
small, legitimate tool calls under the Solar tier's zero-token cap.

## Scoring your own artifacts (for path A, or a new domain-specific gate)

1. Extract structural features deterministically (AST, parser, adapter) —
   never with an LLM: `ast_node_count`, `dependency_depth`,
   `transform_density`, `branch_density`, `external_system_count`,
   `unsupported_construct_count`.
2. Run `scripts/complexity_score.py` (or import `calculate_score`/
   `tier_for_score` directly) to get a `[0, 100]` score and a tier id.
   Resolve that tier's caps from `config/tollgate-dispatch.yaml` via
   `tollgate.governance.runtime.policy_loader.load_tier_policies`.
3. If the tier is `solar`, execute through a deterministic path and record a
   zero-token completion — don't call a model at all.
4. For every other tier: retrieve symbols and bounded dependencies instead
   of full files (`tollgate.context.pack.pack_query`), compress with
   `tollgate.governance.runtime.compressors.ContextCompressor` (backed by
   `tollgate.context.tokens.trim_to_budget`), and pass the result through
   `Guardian.enforce()` before dispatch.
5. Audit: `Guardian.record_completion()` writes measured cost, quality and
   token movement to the Waste Ledger, conforming to
   `schemas/waste-ledger.schema.json`.

`scripts/complexity_score.py`'s specific features are calibrated for a
legacy ETL/migration pilot (Informatica/DataStage/SSIS) — a different
domain needs different structural evidence. Keep the function signature and
the `[0, 100]` range; replace the feature extraction. See
[`docs/EXTENDING.md`](../../docs/EXTENDING.md).

## Guardrails

- "Zero waste" means no unaccounted or unjustified token usage, not zero
  total tokens.
- Do not claim avoided-token savings without a reproducible baseline —
  `scripts/demo_run.py` shows what a reproducible one looks like.
- Keep scoring deterministic, versioned and testable.
- Domain adapters may tune feature extraction and compression profiles, but
  must emit the same canonical Guardian contracts.
- Prefer rejection or human review over an unbounded frontier-model fallback.
