#!/usr/bin/env python3
"""PostToolUse hook: registra cada tool call no store central.

Recebe JSON no stdin (payload padrão de hooks do Claude Code) e grava um
evento com estimativa de tokens (chars/4). Nunca bloqueia a sessão: qualquer
erro sai com código 0 silenciosamente.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "store"))

def main():
    try:
        payload = json.load(sys.stdin)
        import db  # noqa: E402
        tool_input = json.dumps(payload.get("tool_input", ""), ensure_ascii=False)
        tool_response = json.dumps(payload.get("tool_response", ""), ensure_ascii=False)
        in_chars, out_chars = len(tool_input), len(tool_response)
        conn = db.connect()
        conn.execute(
            "INSERT INTO tool_events (session_id, project, tool_name, input_chars, output_chars, est_tokens)"
            " VALUES (?,?,?,?,?,?)",
            (
                payload.get("session_id", ""),
                Path(payload.get("cwd", "")).name,
                payload.get("tool_name", ""),
                in_chars,
                out_chars,
                (in_chars + out_chars) // 4,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    sys.exit(0)

if __name__ == "__main__":
    main()
