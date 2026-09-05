# Pilot Runtime Runbook

This runbook validates the two external dependencies required to promote the ZWCA pilot beyond contract-only status:

1. Headroom inline context compression;
2. OpenAI Responses API provider execution.

## Prerequisites

- Python 3.10 or newer;
- a virtual environment;
- an OpenAI API key only for the optional live provider smoke test.

## Install

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements-pilot.txt
```

Verify the installed tools:

```bash
python -c "import headroom, openai, yaml; print('imports: ok')"
headroom --help
```

The pilot pins `headroom-ai[all]==0.32.1` and supports OpenAI SDK versions from `1.54` up to, but not including, `3.0`.

## Offline verification

The offline smoke test executes real Headroom compression without calling an LLM provider:

```bash
python scripts/verify_pilot_runtime.py
```

Expected characteristics:

- Headroom imports successfully;
- a repetitive mixed INFO/ERROR payload is compressed;
- output is non-empty and smaller than input;
- the process exits with code `0`.

Run the complete test suite:

```bash
python -m pytest tests
```

## Live OpenAI verification

Set credentials and the pilot model:

```bash
export OPENAI_API_KEY="..."
export OPENAI_SMOKE_MODEL="gpt-4o-mini"
```

Optional pricing variables allow the adapter to convert measured token usage into cost:

```bash
export OPENAI_INPUT_PRICE_PER_MILLION="0"
export OPENAI_OUTPUT_PRICE_PER_MILLION="0"
```

Use current approved prices for the deployment environment rather than hard-coding prices in source control.

Run:

```bash
python scripts/verify_pilot_runtime.py --live-openai
```

The response reports measured input/output tokens, calculated cost and returned text. The smoke request caps output and asks for a deterministic phrase.

## Headroom integration mode

The Guardian uses the Python SDK directly:

```python
from runtime.compressors import HeadroomCompressor

compressor = HeadroomCompressor(model="gpt-4o-mini")
compressed = compressor.compress(payload, target_tokens=4000)
```

Internally, the adapter calls Headroom's supported public API:

```python
from headroom import compress

result = compress(
    [{"role": "user", "content": payload}],
    model=model,
    compress_user_messages=True,
    target_ratio=target_ratio,
    protect_recent=0,
)
```

This path does not require a Headroom proxy. A proxy remains an optional deployment mode through `OPENAI_BASE_URL`.

## OpenAI provider integration

The concrete adapter is `runtime.openai_provider.OpenAIResponsesProvider`. It:

- constructs the official `OpenAI` client;
- calls `client.responses.create()`;
- extracts `response.output_text`;
- reads `usage.input_tokens` and `usage.output_tokens`;
- calculates cost from deployment-supplied per-million-token prices;
- preserves the raw SDK response for diagnostics;
- wraps provider failures and includes the request ID when available.

## GitHub Actions

`.github/workflows/pilot-runtime.yml` performs a real dependency installation on Python 3.12, runs all tests and executes the offline Headroom smoke test.

The optional `live-openai` job runs only through `workflow_dispatch` when:

- `run_live_openai` is enabled;
- repository secret `OPENAI_API_KEY` exists.

Recommended repository variables:

- `OPENAI_SMOKE_MODEL`;
- `OPENAI_INPUT_PRICE_PER_MILLION`;
- `OPENAI_OUTPUT_PRICE_PER_MILLION`.

## Pilot acceptance gate

The external integration slice is acceptable when:

- dependency installation succeeds in CI;
- `headroom --help` succeeds;
- real inline compression reduces the smoke payload;
- all unit tests pass;
- the optional live OpenAI smoke test returns measured usage;
- no API keys or prices are committed to the repository.
