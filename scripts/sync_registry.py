#!/usr/bin/env python3
"""Sincroniza o agent_registry com os agentes reais encontrados nos projetos.

Varre um diretório raiz procurando definições de agentes:
  - Markdown: **/agents/*.md e **/.claude/agents/*.md (formato Claude Code)
  - Python:   **/agents/*.py com classe/def de agente (heurística)

Agentes novos entram como status='draft'; existentes têm project/model
atualizados sem tocar no status (o lifecycle é gerido pelo agent-gate).

Uso: python3 scripts/sync_registry.py <raiz> [--owner NOME]
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
               VALUES (?,?,?,'draft',?, 'auto-registrado por sync_registry')
               ON CONFLICT(name) DO UPDATE SET project=excluded.project,
                 model=CASE WHEN excluded.model!='' THEN excluded.model ELSE agent_registry.model END,
                 updated_at=datetime('now')""",
            (key, project, model, args.owner),
        )
        # rowcount é 1 em ambos os casos; distinguir por existência prévia é dispensável aqui
        n_new += cur.rowcount
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM agent_registry").fetchone()[0]
    print(f"Sync ok: {n_new} agentes processados; registry total = {total}")
    for row in conn.execute("SELECT project, COUNT(*) FROM agent_registry GROUP BY project ORDER BY 2 DESC"):
        print(f"  {row[0]:<28} {row[1]} agentes")
    conn.close()


if __name__ == "__main__":
    main()
