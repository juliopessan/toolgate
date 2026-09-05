---
name: agent-gate
description: Quality gate de Agent OPS — valida sintaxe (AST) de código gerado, roda testes e controla o lifecycle de agentes no registry (draft→validated→production). Use antes de commitar código gerado ou promover um agente.
---

# Agent Gate

AST = Abstract Syntax Tree (Árvore de Sintaxe Abstrata). O gate 1 abaixo usa AST para validação determinística de sintaxe — sem LLM envolvido.

## 1. Gate sintático (sempre, antes de commit de código gerado)
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py <arquivos alterados>
```
FAIL em qualquer arquivo = corrigir antes de prosseguir.

## 2. Gate funcional
Rode lint + testes do projeto (`npm run lint && npm test`, ou `pytest`). Só prossiga com tudo verde.

## 3. Lifecycle no registry
Registre/promova o agente no store:
```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/store")
import db
conn = db.connect()
conn.execute("""INSERT INTO agent_registry (name, project, model, status, owner, notes)
  VALUES (?,?,?,?,?,?)
  ON CONFLICT(name) DO UPDATE SET status=excluded.status, model=excluded.model,
    updated_at=datetime('now'), notes=excluded.notes""",
  (NAME, PROJECT, MODEL, STATUS, OWNER, NOTES))
conn.commit()
EOF
```
Status válidos: `draft` → `validated` (gates 1–2 ok) → `production` (promovido após validação em uso real) → `deprecated`.

## 4. Sincronizar inventário

Para descobrir e registrar automaticamente agentes novos nos projetos (entram como `draft`):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sync_registry.py <raiz-dos-projetos> --owner <nome>
```
