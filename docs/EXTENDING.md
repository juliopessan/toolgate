# Extending Tollgate to a new project

This guide is for porting the Tollgate runtime into a project other than
this one. Read this before copying `config/tollgate-dispatch.yaml` or
`scripts/complexity_score.py` into a new codebase — most of the value is
reusable as-is; a small, well-defined part is not.

## Two technologies, one core

This repository ships two things at once:

1. **A Claude Code plugin** (`.claude-plugin/plugin.json`, `agents/`,
   `skills/`, `hooks/hooks.json`) — the distribution mechanism for Claude
   Code specifically. Load it with `claude --plugin-dir /path/to/tollgate`;
   see the "As a Claude Code plugin" part of the README's integration
   section for what `hooks/claude_code_pretooluse.py` actually gates.
2. **A standalone Python runtime** (`src/tollgate/`, `hooks/`, `scripts/`)
   — the actual context and enforcement engine (context toolkit, Guardian,
   Waste Ledger, provider adapters, cost reporting). It has no dependency
   on Claude Code and can be installed and driven directly, e.g. from
   another agent framework, a CI pipeline, or a plain Python service — see
   the "Integrate Tollgate into your project" section of the main
   [README](../README.md) for all three supported entry points.

## What is domain-agnostic (copy as-is)

| Component | Why it's portable |
|---|---|
| `src/tollgate/context/` (tokens, headroom, astx, chunking, semantic, pack, store) | Operates on source text and queries, not on any specific artifact domain. |
| `src/tollgate/governance/runtime/guardian.py`, `hooks/pre_call_guardian.py` | Pre-call enforcement (score required, budget caps, recompression) has no knowledge of what kind of artifact is being processed. |
| `src/tollgate/governance/runtime/anthropic_provider.py`, `openai_provider.py`, `provider_gateway.py` | Provider adapters are generic. |
| `src/tollgate/governance/store/` (`db.py`, `waste_ledger.py`, `budget_reservations.py`, migrations, `pricing.json`) | The SQLite schema and ledger events describe gate decisions and cost, not artifact content. |
| `scripts/cost_report.py`, `scripts/gate.py`, `dashboard/` | Reporting and AST-syntax gating work on any Python/JS/JSON/TS input. |
| `config/tollgate-dispatch.yaml` — `scoring`, `controls`, `tiers`, and the `generic_code` / `sql` platform profiles | The six Thermal Gradient tiers (Solar → Aurora) and their token caps are a policy choice, not a domain fact. Calibrate the thresholds for your workload, but the shape carries over unchanged. |

## What is project-specific (must be rewritten)

| Component | Why it doesn't carry over | What to do instead |
|---|---|---|
| `scripts/complexity_score.py` | Its `complexity_score` features (`ast_node_count`, `dependency_depth`, `transform_density`, `branch_density`, `external_system_count`, `unsupported_construct_count`) were chosen for legacy ETL/pipeline migration artifacts (Informatica, DataStage, SSIS). A different domain — e.g. React components, Terraform modules, API handlers — needs different structural evidence. | Keep the function signature and score range (`0-100`), replace the feature extraction. |
| `platform_profiles.informatica_xml` / `.datastage` / `.ssis` in `config/tollgate-dispatch.yaml` | These are worked examples for the migration pilot this repo shipped with, not defaults every project needs. | Add one `platform_profiles.<your_artifact_type>` entry per artifact type your project handles; delete the migration-specific ones if irrelevant, or leave them as reference. |
| Success metrics in `docs/ARCHITECTURE_BLUEPRINT.md` (`≥80% context reduction`, `<US$50/artifact`, `545 artifacts` rollout) | Calibrated against the original migration-factory baseline. | Re-run Phase 0 (baseline + calibration) for your own artifact population before trusting these numbers. |

## Proposed package structure

The long-term shape, once packaging is finished:

```text
tollgate/                  # pip-installable, domain-agnostic
├── src/tollgate/
│   ├── context/               # tokens, headroom, astx, chunking, semantic, pack, store
│   └── governance/
│       ├── runtime/           # guardian, provider adapters, compressors
│       └── store/             # waste ledger, budgets, migrations
├── hooks/                     # pre_call_guardian.py
├── scripts/
│   ├── cost_report.py
│   ├── gate.py
│   └── rightsizing.py
└── config/
    └── tollgate-dispatch.default.yaml   # tiers + generic_code/sql profiles only

<your-project>/                # per-project extension point
├── complexity_score.py       # domain-specific complexity scoring
├── tollgate-dispatch.yaml    # extends the default with your platform_profiles
└── .claude-plugin/           # optional: wrap your project as its own plugin
    ├── agents/
    └── skills/
```

Until that packaging is finished, the pragmatic path for a new project is:

1. Vendor, `git subtree`, or `pip install -e` the domain-agnostic
   components listed above.
2. Write a project-specific `complexity_score.py` implementing the same
   six required scoring features (or fewer/different ones, updating
   `scoring.required_features` in your copy of `tollgate-dispatch.yaml` to
   match).
3. Add your own `platform_profiles` entries; keep `generic_code` as the
   fallback.
4. Re-run Phase 0 baseline calibration before enabling budget enforcement
   in production — the tier thresholds and target metrics in this repo are
   *not* pre-validated for your workload.

## Versioning

This repository follows [Semantic Versioning](https://semver.org/) via the
version in `pyproject.toml`. A project that vendors these components should
pin to a specific tag/commit, since changes to
`src/tollgate/governance/runtime/guardian.py`'s enforcement contract or the
Waste Ledger schema are breaking changes for any downstream consumer.
