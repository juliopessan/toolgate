---
name: rightsizing
description: Recomendações FinOps de otimização de modelo, caching e batch com base na telemetria. Use quando o usuário quiser reduzir custo de IA, "otimizar modelo" ou "rightsizing".
---

# Rightsizing

1. Rode a análise:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rightsizing.py --days 30
   ```
2. Interprete e priorize as recomendações geradas:
   - **Downgrade de modelo**: cargas simples/repetitivas rodando em Opus/Fable → sugerir Sonnet 5 ($3/$15) ou Haiku 4.5 ($1/$5). Nunca rebaixar sem validar qualidade (use a skill agent-gate).
   - **Prompt caching**: projetos com `cache_read` baixo relativo ao input → investigar invalidadores silenciosos (timestamps no system prompt, tools variáveis).
   - **Batch API**: cargas não sensíveis a latência → 50% de desconto.
   - **Compressão**: sessões com tool outputs enormes → skill `compress` (Headroom).
3. Ao aplicar uma recomendação, registre a economia estimada na tabela `savings` (source='rightsizing').
