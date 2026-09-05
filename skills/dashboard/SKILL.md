---
name: dashboard
description: Generates the self-contained dashboard HTML (Ledger design) from local telemetry — cost, tokens and Guardian decisions. Use when the user asks for a dashboard, a visualization, a visual report, or "I want to see this as HTML".
---

# Dashboard

1. Ingest the latest data (ensures the dashboard reflects real usage up to now):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/src/tollgate/governance/store/ingest_transcripts.py
   ```
2. Generate the dashboard (adjust `--days` to the requested period):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/dashboard/generate_dashboard.py --days 30
   ```
3. The file lands at `dashboard/dashboard.html` — open it in a browser (`open dashboard/dashboard.html` on macOS) and offer it to the user.

If the telemetry store is empty (no `usage`/`waste_ledger_events` yet), run
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/demo_run.py` first to populate the
store with a real Guardian pass, or point the command above's `--db` flag
at the user's actual `~/.tollgate/telemetry.db`.

## What the dashboard shows

- **Waste Ledger** (dark card at the top): candidate vs. transmitted
  tokens, blended reduction %, rejected tokens, active budget reservations
  and measured (audit) cost — from `dashboard/waste_ledger_metrics.py`.
- **Cost summary**: total cost, recorded savings, active projects, agents
  in the registry.
- **Consumption**: cost-per-day chart (canvas, no external dependency),
  table by project and by model.
- **Guardian by tier** and **by reason code** — where blocks and
  recompressions come from (`MISSING_SCORE`, `TIER_INPUT_CAP_EXCEEDED`, etc.).
- **Savings by layer** (`savings.source`: `context_compressor`, `headroom`,
  `ast`, `rightsizing`).
- **Agent Registry** with status (`draft`/`validated`/`production`/`deprecated`).

Design system: the cream/mono ("Ledger") palette is already built into
`dashboard/generate_dashboard.py` — it doesn't depend on any other brand
skill. To reuse the same look in a standalone HTML report, see the pattern
used in `docs/index.html` (the project's landing page).
