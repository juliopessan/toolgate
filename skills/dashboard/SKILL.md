---
name: dashboard
description: Gera o dashboard HTML de FinOps/Agent OPS (self-contained, design Avanade) a partir da telemetria local. Use quando o usuário pedir dashboard, visualização, relatório visual ou "quero ver isso num HTML".
---

# Dashboard

1. Ingerir dados mais recentes (garante que o dashboard reflete o uso real até agora):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/store/ingest_transcripts.py
   ```
2. Gerar o dashboard (ajuste `--days` conforme o período pedido):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/dashboard/generate_dashboard.py --days 30
   ```
3. O arquivo sai em `dashboard/dashboard.html` — abra no navegador (`open dashboard/dashboard.html` no macOS) e ofereça ao usuário.

## O que o dashboard mostra

- **Hero + arc de KPIs**: custo total, economia registrada, projetos ativos, agentes no registry.
- **Economia por camada**: code-nav/AST → compress/Headroom → rightsizing, na sequência térmica oficial da marca.
- **Custo por modelo** (waterfall) e **custo por dia** (linha).
- **Tabelas** de custo por projeto e Agent Registry com status colorido (draft/validated/production/deprecated).
- **Roadmap de rightsizing**: top oportunidades de otimização detectadas na telemetria.

Design system: paleta, tipografia e componentes seguem a skill `avanade-style-guide` (hero com glow, arc de estatísticas, stack de camadas térmicas, stat block dark com waterfall, roadmap numerado, footer corporativo).
