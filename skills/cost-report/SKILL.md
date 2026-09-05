---
name: cost-report
description: FinOps report of AI cost by project, model and period, from local telemetry (transcripts + hooks). Use when the user asks for cost, spend, token consumption, or a FinOps report.
---

# Cost Report

1. Ingest the latest data from Claude Code transcripts:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/src/tollgate/governance/store/ingest_transcripts.py
   ```
2. Generate the report (adjust `--days`, `--project`, `--by project|model|day` as requested):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cost_report.py --days 30 --by project
   ```
3. Present to the user: top projects by cost, model distribution, cache hit rate (high cache_read = good), and recorded savings (headroom/ast/rightsizing).
4. If a project has a budget set in the `budgets` table and spend crosses `alert_pct`, call out the alert.
