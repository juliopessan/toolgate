#!/usr/bin/env python3
"""Ingere transcripts do Claude Code (~/.claude/projects/*/*.jsonl) no store.

Fonte de verdade dos tokens reais: cada mensagem 'assistant' carrega
message.usage {input_tokens, output_tokens, cache_*}. Idempotente
(message_id é chave primária).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

PROJECTS_DIR = Path.home() / ".claude" / "projects"


def project_name(dirname: str) -> str:
    # dirs são o cwd com '/' vira '-': pega o último segmento útil
    return dirname.rstrip("-").split("-")[-1] or dirname


def ingest() -> int:
    conn = db.connect()
    pricing = db.load_pricing()
    inserted = 0
    for jl in PROJECTS_DIR.glob("*/*.jsonl"):
        proj = project_name(jl.parent.name)
        with open(jl, errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage") or {}
                mid = msg.get("id")
                if not mid or not usage:
                    continue
                inp = usage.get("input_tokens", 0) or 0
                out = usage.get("output_tokens", 0) or 0
                cr = usage.get("cache_read_input_tokens", 0) or 0
                cw = usage.get("cache_creation_input_tokens", 0) or 0
                model = msg.get("model", "")
                cur = conn.execute(
                    "INSERT OR IGNORE INTO usage (message_id, ts, session_id, project, model,"
                    " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        mid,
                        rec.get("timestamp", ""),
                        rec.get("sessionId", ""),
                        proj,
                        model,
                        inp,
                        out,
                        cr,
                        cw,
                        db.cost_usd(pricing, model, inp, out, cr, cw),
                    ),
                )
                inserted += cur.rowcount
    conn.commit()
    conn.close()
    return inserted


if __name__ == "__main__":
    n = ingest()
    print(f"Ingested {n} new usage records into {db.DB_PATH}")
