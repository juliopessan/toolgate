---
name: compress
description: Comprime contexto (built-in ou via Headroom) e registra a economia de tokens no store FinOps. Use quando o usuário pedir para comprimir contexto, reduzir tokens de sessão ou "ativar headroom".
---

# Compress

## Opção padrão — zero dependência, já embutida

`tollgate.context` já vem com compressão real, sem instalar nada:

- `tollgate.context.tokens.trim_to_budget(text, budget_tokens, keep="both")` —
  corta por linha inteira, mantém início+fim, sempre diz o que descartou.
  É o que `tollgate.governance.runtime.compressors.ContextCompressor` usa
  por baixo dos panos no Guardian.
- `tollgate.context.pack.pack_query(query, index, budget=N, headroom=h)` —
  quando dá pra usar uma query para escolher o que entra, em vez de só
  cortar o que já foi escolhido.

Prefira isso ao Headroom abaixo quando o projeto já é Python e não precisa
de wrap/proxy fora do processo.

## Headroom — para wrap/proxy fora do processo, ou fora de Python

Headroom (https://github.com/chopratejas/headroom) comprime 60–95% dos tokens que o agente lê. Três modos, em ordem de preferência:

1. **Wrap** (mais simples, por CLI):
   ```bash
   pip install headroom-ai   # ou: npm i -g headroom-ai
   headroom wrap claude
   ```
2. **Proxy** (qualquer app, zero mudança de código):
   ```bash
   headroom proxy --port 8787
   # apontar ANTHROPIC_BASE_URL=http://localhost:8787
   ```
3. **MCP** — expõe `headroom_compress`, `headroom_retrieve`, `headroom_stats`.

## Registro de economia

Após uma sessão comprimida, colete `headroom stats` (ou a saída do wrap) e registre no store:

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/src")
from tollgate.governance.store import db
conn = db.connect()
pricing = db.load_pricing()
tokens_saved = TOKENS   # da saída do headroom
usd = tokens_saved * pricing["models"]["claude-opus-4-8"]["input"] / 1e6
conn.execute("INSERT INTO savings (source, project, tokens_saved, usd_saved, notes) VALUES ('headroom', ?, ?, ?, ?)",
             (PROJECT, tokens_saved, usd, "sessão comprimida"))
conn.commit()
EOF
```

Essas economias aparecem no `cost-report` e no dashboard.
