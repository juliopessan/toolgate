---
name: compress
description: Compresses context (built-in or via Headroom) and records the token savings in the FinOps store. Use when the user asks to compress context, reduce session tokens, or "enable headroom".
---

# Compress

## Default option — zero dependency, already built in

`tollgate.context` already ships real compression, no install required:

- `tollgate.context.tokens.trim_to_budget(text, budget_tokens, keep="both")` —
  trims on whole-line boundaries, keeps head+tail, always says what it
  dropped. This is what
  `tollgate.governance.runtime.compressors.ContextCompressor` uses under
  the hood in the Guardian.
- `tollgate.context.pack.pack_query(query, index, budget=N, headroom=h)` —
  when a query can drive what gets in, instead of just trimming whatever
  was already chosen.

Prefer this over Headroom below when the project is already Python and
doesn't need an out-of-process wrap/proxy.

## Headroom — for an out-of-process wrap/proxy, or outside Python

Headroom (https://github.com/chopratejas/headroom) compresses 60–95% of the tokens an agent reads. Three modes, in order of preference:

1. **Wrap** (simplest, via CLI):
   ```bash
   pip install headroom-ai   # or: npm i -g headroom-ai
   headroom wrap claude
   ```
2. **Proxy** (any app, zero code changes):
   ```bash
   headroom proxy --port 8787
   # point ANTHROPIC_BASE_URL=http://localhost:8787
   ```
3. **MCP** — exposes `headroom_compress`, `headroom_retrieve`, `headroom_stats`.

## Recording savings

After a compressed session, collect `headroom stats` (or the wrap output) and record it in the store:

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/src")
from tollgate.governance.store import db
conn = db.connect()
pricing = db.load_pricing()
tokens_saved = TOKENS   # from headroom's output
usd = tokens_saved * pricing["models"]["claude-opus-4-8"]["input"] / 1e6
conn.execute("INSERT INTO savings (source, project, tokens_saved, usd_saved, notes) VALUES ('headroom', ?, ?, ?, ?)",
             (PROJECT, tokens_saved, usd, "compressed session"))
conn.commit()
EOF
```

These savings show up in `cost-report` and the dashboard.
