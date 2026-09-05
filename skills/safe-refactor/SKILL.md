---
name: safe-refactor
description: Refatoração estrutural multi-arquivo via AST (ast-grep rewrite) — renomear símbolos, migrar APIs e padrões de forma determinística. Use quando o usuário pedir rename/refactor que toca vários arquivos.
---

# Safe Refactor (AST)

Em vez de N edits do LLM (caros e sujeitos a erro), use rewrite estrutural determinístico.

## Fluxo

1. **Preview** — sempre rode sem `-U` primeiro e mostre o diff ao usuário:
   ```bash
   ast-grep run -p 'oldFunc($$$ARGS)' -r 'newFunc($$$ARGS)' --lang ts src/
   ```
2. **Aplicar** após confirmação:
   ```bash
   ast-grep run -p 'oldFunc($$$ARGS)' -r 'newFunc($$$ARGS)' --lang ts src/ -U
   ```
3. **Validar** — rode a skill `agent-gate` (parse sintático) + testes do projeto (`npm test` / `pytest`).
4. Casos com semântica de escopo (rename de variável local, shadowing): prefira o LSP/tsc do projeto ou revise manualmente os matches — ast-grep casa padrões, não resolve escopo.

## Exemplos de regras YAML (para refactors recorrentes)
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
