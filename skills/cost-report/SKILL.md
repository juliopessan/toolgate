---
name: cost-report
description: Relatório FinOps de custo de IA por projeto, modelo e período, a partir da telemetria local (transcripts + hooks). Use quando o usuário pedir custo, gasto, consumo de tokens ou relatório FinOps.
---

# Cost Report

1. Ingerir dados mais recentes dos transcripts do Claude Code:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/store/ingest_transcripts.py
   ```
2. Gerar o relatório (ajuste `--days`, `--project`, `--by project|model|day` conforme o pedido):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cost_report.py --days 30 --by project
   ```
3. Apresente ao usuário: top projetos por custo, distribuição por modelo, taxa de cache hit (cache_read alto = bom), e economia registrada (headroom/ast/rightsizing).
4. Se algum projeto tiver budget definido na tabela `budgets` e o gasto passar de `alert_pct`, destaque o alerta.
