---
name: agent-gate
description: Agent OPS quality gate — validates syntax (AST) of generated code, runs tests, and manages agent lifecycle in the registry (draft→validated→production). Use before committing generated code or promoting an agent.
---

# Agent Gate

AST = Abstract Syntax Tree. Gate 1 below uses AST for deterministic syntax validation — no LLM involved.

## 1. Syntax gate (always, before committing generated code)
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py <changed files>
```
FAIL on any file = fix before proceeding.

## 2. Functional gate
Run the project's lint + tests (`npm run lint && npm test`, or `pytest`). Only proceed once everything is green.

## 3. Registry lifecycle
Register/promote the agent in the store:
```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/src")
from tollgate.governance.store import db
conn = db.connect()
conn.execute("""INSERT INTO agent_registry (name, project, model, status, owner, notes)
  VALUES (?,?,?,?,?,?)
  ON CONFLICT(name) DO UPDATE SET status=excluded.status, model=excluded.model,
    updated_at=datetime('now'), notes=excluded.notes""",
  (NAME, PROJECT, MODEL, STATUS, OWNER, NOTES))
conn.commit()
EOF
```
Valid statuses: `draft` → `validated` (gates 1–2 passed) → `production` (promoted after real-usage validation) → `deprecated`.

## 4. Sync the inventory

To discover and auto-register new agents across projects (they enter as `draft`):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sync_registry.py <projects-root> --owner <name>
```
