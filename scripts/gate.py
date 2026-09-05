#!/usr/bin/env python3
"""agent-gate: validação sintática pós-geração via AST.

Uso: python3 scripts/gate.py <arquivo> [arquivo...]
Sai com código 1 se qualquer arquivo não parseia.
"""
import ast
import json
import subprocess
import sys
from pathlib import Path


def check(path: Path) -> tuple[bool, str]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".py":
            ast.parse(path.read_text(errors="replace"))
            return True, "python ast ok"
        if suffix == ".json":
            json.loads(path.read_text(errors="replace"))
            return True, "json ok"
        if suffix in (".js", ".mjs", ".cjs"):
            r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            return r.returncode == 0, r.stderr.strip() or "node --check ok"
        if suffix in (".ts", ".tsx", ".jsx"):
            r = subprocess.run(
                ["npx", "--yes", "@ast-grep/cli", "run", "-p", "$A", "--lang",
                 "tsx" if suffix in (".tsx", ".jsx") else "ts", str(path)],
                capture_output=True, text=True,
            )
            return r.returncode == 0, "ast-grep parse ok" if r.returncode == 0 else r.stderr.strip()
        return True, f"sem validador para {suffix} (pulado)"
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    failed = 0
    for arg in sys.argv[1:]:
        ok, msg = check(Path(arg))
        print(f"{'PASS' if ok else 'FAIL'}  {arg}  ({msg})")
        failed += 0 if ok else 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
