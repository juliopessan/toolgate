# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The version tracked here matches `.claude-plugin/plugin.json`.

## [Unreleased]

## [0.4.0] - 2026-07-21

### Added
- `store/decision_log.py` (`zwca_decisions` table, migration `004`): Memory
  bucket — a durable decision log distinct from documentation. Recording a
  decision with `supersedes_decision_id` automatically closes the prior
  decision out.
- `store/change_history.py` (`zwca_artifact_changes` table, migration
  `005`): Change History bucket — artifacts evolve through deltas, not full
  rewrites. Every change declares a `measured`/`estimated`/`counterfactual`
  evidence basis, the same discipline the Waste Ledger applies to savings
  claims.
- `schemas/decision-log.schema.json`, `schemas/change-history.schema.json`.
- `tests/test_decision_log.py`, `tests/test_change_history.py`.
- README: new "Memory and Change History" section; `docs/ZWCA_BLUEPRINT.md`
  Plane 4 and Waste Ledger sections updated to describe both buckets.

## [0.3.0] - 2026-07-20

### Changed
- `config/zwca-dispatch.yaml`: separated domain-agnostic `platform_profiles`
  (`generic_code`, `sql`) from the legacy data-migration example profiles
  (`informatica_xml`, `datastage`, `ssis`), which are now documented as an
  optional worked example rather than defaults every project inherits.
- `README.md`: clarified that the distribution technology is the Claude
  Code plugin, but the enforcement core (`runtime/`, `store/`, `hooks/`) is
  a standalone Python runtime that can be consumed directly, without the
  plugin wrapper; documented `pipx install headroom-ai` as a required
  install step, not optional; added a Versioning section and an "Extending
  to a new project" section.

### Added
- `docs/EXTENDING.md`: extension guide for porting the ZWCA core (runtime +
  store + hooks) into a new project, including the proposed package
  structure split between the reusable core and the domain-specific
  `zwca_score.py` / `platform_profiles` extension points.
- This changelog.

## [0.2.0] - 2026-07-20

### Added
- MIT `LICENSE`.
- `.github/dependabot.yml` for weekly `pip` and `github-actions` update PRs.

### Fixed
- `requirements-pilot.txt` pinned `headroom-ai[all]==0.22.2`, a version no
  longer published on PyPI, which broke installs and the `pilot-runtime.yml`
  CI workflow. Bumped to `0.32.1`. Updated the matching reference in
  `docs/PILOT_RUNTIME_RUNBOOK.md`.

## [0.1.0] - Guardian Enforcement Vertical

### Added
- SQLite migrations for `zwca_sessions` and `waste_ledger_events`
  (`store/migrations/002_waste_ledger.sql`, `003_budget_reservations.sql`).
- `runtime/guardian.py`: mandatory pre-call interception, "no score, no
  call" enforcement, tier input/output hard caps, artifact and session
  budget evaluation, bounded recompression loop, fail-closed behavior.
- `hooks/pre_call_guardian.py`: stdin/stdout provider-call interceptor.
- Admission, compression and audit event emission into the Waste Ledger.
- Anthropic and OpenAI provider adapters (`runtime/anthropic_provider.py`,
  `runtime/openai_provider.py`) with measured cost persistence.
- Automated tests for the main allow/block paths.

## [0.0.1] - Initial architecture

### Added
- ZWCA four-plane architecture contract (`docs/ZWCA_BLUEPRINT.md`).
- Thermal Gradient × RTK tier policy (`config/zwca-dispatch.yaml`).
- Waste Ledger event schema (`schemas/waste-ledger.schema.json`).
- `agent-finops` Claude Code plugin skeleton (`agents/`, `skills/`,
  `.claude-plugin/`).
- Cost reporting, rightsizing and dashboard scripts.
