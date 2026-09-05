# Tollgate

> Every candidate token must pass three gates: **Admission** — does it deserve
> to enter? **Compression** — can it be smaller? **Audit** — did it generate
> accepted value?

Agents burn tokens on context nobody asked for — whole files instead of the
one function that matters, unscored payloads sent straight to a frontier
model, no record of what was actually worth paying for. Tollgate is a
deterministic gate that sits in front of every provider call: it scores what
an agent wants to send, decides how much of it earns entry, and keeps an
auditable ledger of what was admitted, rejected and why.

Not zero token usage. **Zero unjustified or unaccounted token consumption.**

```bash
pip install -e .
```

## Two layers, one gate

```text
┌──────────────────────────────────────────────────────────────┐
│  tollgate.context      "what goes in the window?"            │
│  tokens · headroom · astx · chunking · semantic · pack · store│
├──────────────────────────────────────────────────────────────┤
│  tollgate.governance    "what earns the call?"                │
│  Guardian · Waste Ledger · thermal-tier dispatch · budgets    │
└──────────────────────────────────────────────────────────────┘
```

### `tollgate.context` — the seven layers

Zero runtime dependencies. Measured on real repositories, reading a
**skeleton instead of a whole file** costs ~87% fewer tokens.

| Layer | Module | What it answers |
|---|---|---|
| Tokens | `tollgate.context.tokens` | What does this cost? |
| Headroom | `tollgate.context.headroom` | What can I still afford? |
| Structure | `tollgate.context.astx` | What is worth quoting? |
| Chunking | `tollgate.context.chunking` | How do I cut it without breaking it? |
| Semantic | `tollgate.context.semantic` | What is relevant? |
| Pack | `tollgate.context.pack` | What actually gets sent — and can I cite it? |
| Persistence | `tollgate.context.store` | What can I avoid recomputing, and what did it actually save? |

```python
from tollgate.context import Headroom, index_path, pack_query

headroom = Headroom(window=200_000, reserve_output=8_000)
headroom.spend("conversation", 120_000)

index = index_path("./src")
context = pack_query("where is the retry backoff configured?", index,
                      budget=6_000, headroom=headroom)
print(context.text)
```

CLI: `tollgate count|skeleton|symbol|outline|search|pack|headroom ...`
(every subcommand takes `--json`).

### `tollgate.governance` — the four planes

A deterministic runtime contract enforced before any provider call:

```text
PLANE 4 — Governance & Observability   Budget · Waste Ledger · Decision Log · Change History
PLANE 3 — Decision                     Complexity score · thermal tier · model routing
PLANE 2 — Context                      Admission backed directly by tollgate.context
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

Thermal Gradient × RTK tiers (`config/tollgate-dispatch.yaml`):

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
export TOLLGATE_DB=~/.tollgate/telemetry.db
cat request.json | python3 hooks/pre_call_guardian.py
```

## Repository layout

```text
tollgate/
├── src/tollgate/
│   ├── context/        # tokens, headroom, astx, chunking, semantic, pack, store
│   └── governance/
│       ├── runtime/     # Guardian, provider gateway, provider adapters
│       └── store/       # waste ledger, decision log, change history, budget reservations
├── hooks/               # pre-call interceptor (stdin/stdout contract)
├── config/              # thermal-tier dispatch policy
├── schemas/             # JSON Schemas for the ledger, decision log, change history
├── scripts/             # cost_report, rightsizing, gate, complexity scoring, sync_registry
├── dashboard/           # self-contained HTML dashboard generator
├── docs/                # architecture, extension and operationalization notes
├── benchmarks/          # context-layer benchmark harness
└── tests/
    ├── context/
    └── governance/
```

## Quick start

```bash
python3 -m pytest tests/
python3 scripts/complexity_score.py --ast-nodes 320 --dependency-depth 8 \
  --transform-density 0.72 --branch-density 0.25 \
  --external-systems 3 --unsupported-constructs 1
python3 dashboard/generate_dashboard.py
```

## Status

Plane 2 (Context) is wired directly to `tollgate.context`'s pack/headroom
primitives — an oversized candidate payload is compressed by packing it
through a query-aware budget instead of a naive truncation. See
[`docs/EXTENDING.md`](docs/EXTENDING.md) for what's still domain-specific
versus reusable as-is.

Target metrics below are acceptance criteria from an original pilot,
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

MIT — see [LICENSE](LICENSE).
