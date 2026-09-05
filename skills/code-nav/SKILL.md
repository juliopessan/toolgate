---
name: code-nav
description: Structural code navigation via AST (ast-grep/tree-sitter) to find symbols, functions and patterns while reading the minimum number of tokens. Use before scanning whole files in large codebases, or when the user asks for structural search.
---

# Code Nav (AST)

AST = Abstract Syntax Tree: a tree representation of source code's logical structure, used here via ast-grep/tree-sitter to navigate with syntactic precision instead of treating code as plain text.

FinOps goal: replace "read whole files" with structural queries — fewer tokens even before compression.

## Setup (once per machine)
```bash
brew install ast-grep   # or: npm i -g @ast-grep/cli
```

## Usage patterns

Search for a function/symbol instead of opening files:
```bash
ast-grep run -p 'function $NAME($$$) { $$$ }' --lang ts src/   # TS definitions
ast-grep run -p 'def $NAME($$$):' --lang py .                  # Python definitions
ast-grep run -p 'useEffect($$$)' --lang tsx src/               # hook usages
```

Extract just the body of a specific symbol:
```bash
ast-grep run -p 'def process_invoice($$$): $$$' --lang py --json | head -50
```

## Rules
1. In codebases > ~50 files, try `ast-grep` before `Read`ing whole files.
2. Read only the returned span (match start/end line) via Read with offset/limit.
3. Record relevant savings in the `savings` table (source='ast'), estimating avoided tokens (file size − span read, /4).
