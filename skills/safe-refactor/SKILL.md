---
name: safe-refactor
description: Multi-file structural refactoring via AST (ast-grep rewrite) — rename symbols, migrate APIs and patterns deterministically. Use when the user asks for a rename/refactor that touches multiple files.
---

# Safe Refactor (AST)

Instead of N LLM edits (expensive and error-prone), use a deterministic structural rewrite.

## Flow

1. **Preview** — always run without `-U` first and show the diff to the user:
   ```bash
   ast-grep run -p 'oldFunc($$$ARGS)' -r 'newFunc($$$ARGS)' --lang ts src/
   ```
2. **Apply** after confirmation:
   ```bash
   ast-grep run -p 'oldFunc($$$ARGS)' -r 'newFunc($$$ARGS)' --lang ts src/ -U
   ```
3. **Validate** — run the `agent-gate` skill (syntax parse) + the project's tests (`npm test` / `pytest`).
4. Cases with scope semantics (local variable rename, shadowing): prefer the project's LSP/tsc, or review matches manually — ast-grep matches patterns, it doesn't resolve scope.

## Example YAML rules (for recurring refactors)
```yaml
# rule.yml
id: migrate-axios-to-fetch
language: typescript
rule: { pattern: axios.get($URL) }
fix: fetch($URL)
```
```bash
ast-grep scan -r rule.yml src/ -U
```
