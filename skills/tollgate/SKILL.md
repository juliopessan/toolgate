---
name: zwca
description: Enforce zero-waste context architecture across deterministic admission, context compression, model dispatch, token budgets and audit.
---

# ZWCA Runtime

Use this skill when the user asks to optimize, route, govern or audit an AI-agent workload using the unified Zero-Waste Context Architecture.

## Operating contract

Every workload must pass these gates in order:

1. **Admission** — determine whether the operation can be completed deterministically and whether each context unit is relevant.
2. **Compression** — minimize admitted context using the platform profile while preserving required semantics.
3. **Audit** — record token movement, cost, quality and value in the Waste Ledger.

Never invoke a model when no deterministic complexity score is available. Never exceed a tier budget without the configured exception path.

## Workflow

### 1. Identify the artifact

Require or derive:

- `artifact_id`;
- platform;
- operation;
- source location;
- expected output and validation command.

### 2. Extract structural features

Use an AST, parser or deterministic adapter. Produce:

- `ast_node_count`;
- `dependency_depth`;
- `transform_density`;
- `branch_density`;
- `external_system_count`;
- `unsupported_construct_count`.

Do not use an LLM to calculate these features.

### 3. Score and route

Run `scripts/zwca_score.py` or its library function. Resolve the tier from `config/zwca-dispatch.yaml`.

If the tier is `solar`, execute through the deterministic harness and record a zero-token completion.

### 4. Build context

For non-solar tiers:

- retrieve symbols and bounded dependencies rather than full files;
- deduplicate context units;
- use the platform profile;
- create a context manifest with inclusion reasons;
- apply compression;
- calculate transmitted tokens before dispatch.

### 5. Enforce budget

Compare the package with the tier caps.

- Within cap: dispatch.
- Above cap: re-compress up to the configured limit.
- Still above cap: reject or escalate according to policy.

Do not silently truncate semantically required context.

### 6. Validate output

Apply deterministic checks in this order:

1. syntax/parser validation;
2. AST or structural constraints;
3. project tests;
4. policy-specific quality checks.

Use only the bounded repair count configured by policy.

### 7. Audit

Write ledger events conforming to `schemas/waste-ledger.schema.json`. Distinguish measured, estimated and counterfactual values. Report:

- deterministic avoidance;
- context reduction;
- budget compliance;
- provider cost;
- structural pass rate;
- cache reuse;
- accepted/rejected value status.

## Guardrails

- “Zero waste” means no unaccounted or unjustified token usage, not zero total tokens.
- Do not claim avoided-token savings without a reproducible baseline or estimator.
- Keep scoring deterministic, versioned and testable.
- Platform adapters may tune feature extraction and compression profiles, but must emit the same canonical contracts.
- Prefer rejection or human review over an unbounded frontier-model fallback.
