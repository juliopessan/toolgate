# Extending ZWCA to a new project

This guide is for porting the Zero-Waste Context Architecture runtime into a
project other than the one this repository originated from. Read this before
copying `config/zwca-dispatch.yaml` or `scripts/zwca_score.py` into a new
codebase — most of the value is reusable as-is; a small, well-defined part
is not.

## Two technologies, one core

This repository ships two things at once:

1. **A Claude Code plugin** (`.claude-plugin/`, `agents/`, `skills/`) — the
   distribution mechanism. It gives Claude Code slash-command-style access
   to the runtime via `${CLAUDE_PLUGIN_ROOT}`.
2. **A standalone Python runtime** (`runtime/`, `store/`, `hooks/`,
   `scripts/`) — the actual enforcement engine (Guardian, Waste Ledger,
   provider adapters, cost reporting). It has no dependency on Claude Code
   and can be installed and driven directly, e.g. from another agent
   framework, a CI pipeline, or a plain Python service.

Use the plugin when the consumer is Claude Code. Use the Python core
directly when it isn't — the enforcement contract (admission → compression
→ audit, "no score, no call") is identical either way.

## What is domain-agnostic (copy as-is)

| Component | Why it's portable |
|---|---|
| `runtime/guardian.py`, `hooks/pre_call_guardian.py` | Pre-call enforcement (score required, budget caps, recompression) has no knowledge of what kind of artifact is being processed. |
| `runtime/anthropic_provider.py`, `runtime/openai_provider.py`, `runtime/provider_gateway.py` | Provider adapters are generic. |
| `store/` (`db.py`, `waste_ledger.py`, `budget_reservations.py`, migrations, `pricing.json`) | The SQLite schema and ledger events describe gate decisions and cost, not artifact content. |
| `scripts/cost_report.py`, `scripts/gate.py`, `dashboard/` | Reporting and AST-syntax gating work on any Python/JS/JSON/TS input. |
| `config/zwca-dispatch.yaml` — `scoring`, `controls`, `tiers`, and the `generic_code` / `sql` platform profiles | The six Thermal Gradient tiers (Solar → Aurora) and their token caps are a policy choice, not a domain fact. Calibrate the thresholds for your workload, but the shape carries over unchanged. |

## What is project-specific (must be rewritten)

| Component | Why it doesn't carry over | What to do instead |
|---|---|---|
| `scripts/zwca_score.py` | Its `complexity_score` features (`ast_node_count`, `dependency_depth`, `transform_density`, `branch_density`, `external_system_count`, `unsupported_construct_count`) were chosen for legacy ETL/pipeline migration artifacts (Informatica, DataStage, SSIS). A different domain — e.g. React components, Terraform modules, API handlers — needs different structural evidence. | Keep the function signature and score range (`0-100`), replace the feature extraction. |
| `platform_profiles.informatica_xml` / `.datastage` / `.ssis` in `config/zwca-dispatch.yaml` | These are worked examples for the migration pilot this repo shipped with, not defaults every project needs. | Add one `platform_profiles.<your_artifact_type>` entry per artifact type your project handles; delete the migration-specific ones if irrelevant, or leave them as reference. |
| Success metrics in `docs/ZWCA_BLUEPRINT.md` (`≥80% context reduction`, `<US$50/artifact`, `545 artifacts` rollout) | Calibrated against the original migration-factory baseline. | Re-run Phase 0 (baseline + calibration) for your own artifact population before trusting these numbers. |

## Proposed package structure

The long-term shape, once the core is extracted for reuse across projects:

```text
agent-finops-core/            # pip-installable, domain-agnostic
├── runtime/                  # guardian, provider adapters, compressors
├── store/                    # waste ledger, budgets, migrations
├── hooks/                    # pre_call_guardian.py
├── scripts/
│   ├── cost_report.py
│   ├── gate.py
│   └── rightsizing.py
└── config/
    └── zwca-dispatch.default.yaml   # tiers + generic_code/sql profiles only

<your-project>/                # per-project extension point
├── zwca_score.py             # domain-specific complexity scoring
├── zwca-dispatch.yaml        # extends the default with your platform_profiles
└── .claude-plugin/           # optional: wrap agent-finops-core as a plugin
    ├── agents/
    └── skills/
```

Until that extraction happens, the pragmatic path for a new project is:

1. Vendor or `git subtree` the domain-agnostic components listed above.
2. Write a project-specific `zwca_score.py` implementing the same six
   required scoring features (or fewer/different ones, updating
   `scoring.required_features` in your copy of `zwca-dispatch.yaml` to
   match).
3. Add your own `platform_profiles` entries; keep `generic_code` as the
   fallback.
4. Re-run Phase 0 baseline calibration before enabling budget enforcement
   in production — the tier thresholds and target metrics in this repo are
   *not* pre-validated for your workload.

## Versioning

This repository follows [Semantic Versioning](https://semver.org/). Track
changes in `CHANGELOG.md`. A project that vendors these components should
pin to a specific tag/commit and re-check `CHANGELOG.md` before pulling in
updates, since changes to `runtime/guardian.py`'s enforcement contract or
the Waste Ledger schema are breaking changes for any downstream consumer.
