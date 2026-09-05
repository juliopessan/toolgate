---
name: rightsizing
description: FinOps recommendations for model, caching and batch optimization, based on telemetry. Use when the user wants to reduce AI cost, "optimize the model", or "rightsizing".
---

# Rightsizing

1. Run the analysis:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rightsizing.py --days 30
   ```
2. Interpret and prioritize the generated recommendations:
   - **Model downgrade**: simple/repetitive workloads running on Opus/Fable → suggest Sonnet 5 ($3/$15) or Haiku 4.5 ($1/$5). Never downgrade without validating quality (use the agent-gate skill).
   - **Prompt caching**: projects with `cache_read` low relative to input → investigate silent invalidators (timestamps in the system prompt, variable tools).
   - **Batch API**: latency-insensitive workloads → 50% discount.
   - **Compression**: sessions with huge tool outputs → `compress` skill (Headroom).
3. When applying a recommendation, record the estimated savings in the `savings` table (source='rightsizing').
