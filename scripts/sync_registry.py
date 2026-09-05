#!/usr/bin/env python3
"""Syncs agent_registry with the real agents found across projects.

Walks a root directory looking for agent definitions:
  - Markdown: **/agents/*.md and **/.claude/agents/*.md (Claude Code format)
  - Python:   **/agents/*.py with an agent class/def (heuristic)

New agents enter with status='draft'; existing ones have their
project/model updated without touching status (lifecycle is owned by
agent-gate).

Usage: python3 scripts/sync_registry.py <root> [--owner NAME]
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from tollgate.governance.store import db  # noqa: E402

SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", "worktrees"}
MODEL_RE = re.compile(r"model:\s*([\w.\-]+)")


def project_of(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    return rel.parts[0] if len(rel.parts) > 1 else root.name


def scan(root: Path):
    for p in root.rglob("agents/*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix == ".md" and p.is_file():
            text = p.read_text(errors="replace")[:2000]
            m = MODEL_RE.search(text)
            yield p.stem, project_of(p, root), (m.group(1) if m else "")
        elif p.suffix == ".py" and p.is_file() and p.stem not in ("__init__",):
            text = p.read_text(errors="replace")
            if re.search(r"class\s+\w*Agent|Agent\(|agent", text, re.I):
                yield p.stem, project_of(p, root), ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--owner", default="")
    args = ap.parse_args()

    conn = db.connect()
    n_new = n_upd = 0
    for name, project, model in scan(args.root.resolve()):
        key = f"{project}/{name}"
        cur = conn.execute(
            """INSERT INTO agent_registry (name, project, model, status, owner, notes)
               VALUES (?,?,?,'draft',?, 'auto-registered by sync_registry')
               ON CONFLICT(name) DO UPDATE SET project=excluded.project,
                 model=CASE WHEN excluded.model!='' THEN excluded.model ELSE agent_registry.model END,
                 updated_at=datetime('now')""",
            (key, project, model, args.owner),
        )
        # rowcount is 1 in both cases; distinguishing insert vs. update isn't needed here
        n_new += cur.rowcount
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM agent_registry").fetchone()[0]
    print(f"Sync ok: {n_new} agents processed; registry total = {total}")
    for row in conn.execute("SELECT project, COUNT(*) FROM agent_registry GROUP BY project ORDER BY 2 DESC"):
        print(f"  {row[0]:<28} {row[1]} agents")
    conn.close()


if __name__ == "__main__":
    main()
