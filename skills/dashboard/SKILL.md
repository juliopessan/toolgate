---
name: dashboard
description: Gera o dashboard HTML self-contained (design Ledger) a partir da telemetria local — custo, tokens e decisões do Guardian. Use quando o usuário pedir dashboard, visualização, relatório visual ou "quero ver isso num HTML".
---

# Dashboard

1. Ingerir dados mais recentes (garante que o dashboard reflete o uso real até agora):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/src/tollgate/governance/store/ingest_transcripts.py
   ```
2. Gerar o dashboard (ajuste `--days` conforme o período pedido):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/dashboard/generate_dashboard.py --days 30
   ```
3. O arquivo sai em `dashboard/dashboard.html` — abra no navegador (`open dashboard/dashboard.html` no macOS) e ofereça ao usuário.

Se a base de telemetria estiver vazia (nenhum `usage`/`waste_ledger_events` ainda),
rode antes `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/demo_run.py` para popular o
store com uma passada real do Guardian, ou aponte `--db` do próprio comando
acima para um `~/.tollgate/telemetry.db` real do usuário.

## O que o dashboard mostra

- **Waste Ledger** (card escuro no topo): tokens candidatos vs. transmitidos,
  % de redução blended, tokens rejeitados, reservas de budget ativas e custo
  medido (audit) — vem de `dashboard/waste_ledger_metrics.py`.
- **Resumo de custo**: custo total, economia registrada, projetos ativos,
  agentes no registry.
- **Consumo**: gráfico de custo por dia (canvas, sem dependência externa),
  tabela por projeto e por modelo.
- **Guardian por tier** e **por reason code** — de onde vêm os bloqueios e
  recompressões (`MISSING_SCORE`, `TIER_INPUT_CAP_EXCEEDED`, etc.).
- **Economia por camada** (`savings.source`: `context_compressor`, `headroom`,
  `ast`, `rightsizing`).
- **Agent Registry** com status (`draft`/`validated`/`production`/`deprecated`).

Design system: paleta cream/mono ("Ledger") já embutida em
`dashboard/generate_dashboard.py` — não depende de nenhuma outra skill de
brand. Para reaproveitar o mesmo visual num relatório HTML avulso, veja o
padrão usado em `docs/index.html` (a landing page do projeto).
