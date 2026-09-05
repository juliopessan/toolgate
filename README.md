# Context Ledger

> **Every candidate token must pass three gates: Admission — does it deserve to
> enter? Compression — can it be smaller? Audit — did it generate accepted
> value?**

`context-ledger` unifies two previously separate projects into one runtime:
a **context toolkit** (what goes in the window — tokens, headroom, AST
skeletons, chunking, semantic retrieval, packing, persistence) and a
**FinOps governance runtime** (what is allowed to leave the window — a
deterministic Guardian that scores, tiers, budgets and audits every call
before a provider ever sees it).

Not zero token usage. **Zero unjustified or unaccounted token consumption.**

```bash
pip install -e .
```

## Two halves, one ledger

```text
┌──────────────────────────────────────────────────────────────┐
│  context_ledger.context      "what goes in the window?"      │
│  tokens · headroom · astx · chunking · semantic · pack · store│
├──────────────────────────────────────────────────────────────┤
│  context_ledger.governance    "what earns the call?"          │
│  Guardian · Waste Ledger · thermal-tier dispatch · budgets    │
└──────────────────────────────────────────────────────────────┘
```

### `context_ledger.context` — the seven layers

Formerly [Tools-Tokens](https://github.com/juliopessan/Tools-Tokens). Zero
runtime dependencies. Measured on real repositories, reading a **skeleton
instead of a whole file** costs ~87% fewer tokens.

| Layer | Module | What it answers |
|---|---|---|
| Tokens | `context_ledger.context.tokens` | What does this cost? |
| Headroom | `context_ledger.context.headroom` | What can I still afford? |
| Structure | `context_ledger.context.astx` | What is worth quoting? |
| Chunking | `context_ledger.context.chunking` | How do I cut it without breaking it? |
| Semantic | `context_ledger.context.semantic` | What is relevant? |
| Pack | `context_ledger.context.pack` | What actually gets sent — and can I cite it? |
| Persistence | `context_ledger.context.store` | What can I avoid recomputing, and what did it actually save? |

```python
from context_ledger.context import Headroom, index_path, pack_query

headroom = Headroom(window=200_000, reserve_output=8_000)
headroom.spend("conversation", 120_000)

index = index_path("./src")
context = pack_query("where is the retry backoff configured?", index,
                      budget=6_000, headroom=headroom)
print(context.text)
```

CLI: `context-ledger count|skeleton|symbol|outline|search|pack|headroom ...`
(every subcommand takes `--json`).

### `context_ledger.governance` — the four planes

Formerly [agent-finops-ZWCA](https://github.com/juliopessan/agent-finops-ZWCA).
A deterministic runtime contract enforced before any provider call:

```text
PLANE 4 — Governance & Observability   Budget · Waste Ledger · Decision Log · Change History
PLANE 3 — Decision                     Complexity score · thermal tier · model routing
PLANE 2 — Context                      (now backed directly by context_ledger.context above)
PLANE 1 — Deterministic floor          Everything that does not require an LLM executes here
```

Runtime contract:

1. Deterministic before probabilistic.
2. No score, no call.
3. Minimum sufficient context.
4. Hard caps before provider calls.
5. No unlimited retries or automatic frontier escalation.
6. Quality and cost are evaluated together.
7. Measured, estimated and counterfactual evidence never mix.
8. Every admitted token has an auditable purpose and outcome.

Thermal Gradient × RTK tiers (`config/zwca-dispatch.yaml`):

| Tier | Score | Execution policy |
|---|---:|---|
| Solar | 0–15 | Deterministic, zero-token execution |
| Daylight | 16–30 | Small model, tightly constrained context |
| Horizon | 31–45 | Small or medium model |
| Twilight | 46–60 | Reasoning-capable model |
| Starlight | 61–80 | Advanced reasoning with approval controls |
| Aurora | 81–100 | Frontier model, maximum budget, mandatory approval |

Pre-call hook contract (`hooks/pre_call_guardian.py`, stdin/stdout JSON,
exit `0` admitted / `2` blocked):

```bash
export AGENT_FINOPS_DB=~/.agent-finops/telemetry.db
cat request.json | python3 hooks/pre_call_guardian.py
```

## Repository layout

```text
context-ledger/
├── src/context_ledger/
│   ├── context/        # CORE — tokens, headroom, astx, chunking, semantic, pack, store
│   └── governance/
│       ├── runtime/     # Guardian, provider gateway, provider adapters
│       └── store/       # waste ledger, decision log, change history, budget reservations
├── hooks/               # pre-call interceptor (stdin/stdout contract)
├── config/              # thermal-tier dispatch policy (zwca-dispatch.yaml)
├── schemas/             # JSON Schemas for the ledger, decision log, change history
├── scripts/             # cost_report, rightsizing, gate, zwca_score, sync_registry
├── dashboard/           # self-contained HTML dashboard generator (Ledger design system)
├── docs/                # architecture, extension and operationalization notes
├── benchmarks/          # tools-tokens harvest benchmark
└── tests/
    ├── context/         # ported from Tools-Tokens
    └── governance/      # ported from agent-finops-ZWCA
```

## Quick start

```bash
python3 -m pytest tests/
python3 scripts/zwca_score.py --ast-nodes 320 --dependency-depth 8 \
  --transform-density 0.72 --branch-density 0.25 \
  --external-systems 3 --unsupported-constructs 1
python3 dashboard/generate_dashboard.py
```

## Status

Both halves ship with passing test suites (210+ tests) migrated verbatim
from their origin repositories, re-pointed at the unified `context_ledger`
package. The next step documented in
[`docs/EXTENDING.md`](docs/EXTENDING.md) — wiring Plane 2 (Context) directly
to the `context_ledger.context` pack/headroom primitives instead of the
placeholder AST admission described there — is the first integration
milestone, not yet done.

Target metrics below are acceptance criteria from the original ZWCA pilot,
**not current production claims** — recalibrate against your own Phase 0
baseline.

| Metric | Target |
|---|---:|
| Deterministic operations | 25–35% |
| Context reduction for LLM cases | ≥80% pilot |
| Blended reduction | 85–90% |
| Structural pass rate | ≥95% |
| Cost per completed artifact | < US$50 |
| Unaccounted token consumption | 0% |

## License

MIT — see [LICENSE](LICENSE). Both origin projects (Tools-Tokens,
agent-finops-ZWCA) were MIT-licensed under the same author.
