from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from tollgate.governance.runtime.anthropic_provider import AnthropicMessagesProvider, AnthropicPricing  # noqa: E402
from tollgate.governance.runtime.compressors import HeadroomCompressor  # noqa: E402
from tollgate.governance.runtime.openai_provider import OpenAIPricing, OpenAIResponsesProvider  # noqa: E402


def verify_headroom() -> dict[str, object]:
    sample = "\n".join(
        [
            "INFO workflow completed successfully id=123 duration=10ms",
            "INFO workflow completed successfully id=124 duration=11ms",
            "INFO workflow completed successfully id=125 duration=12ms",
            "ERROR workflow failed id=126 reason=timeout",
        ]
        * 40
    )
    compressed = HeadroomCompressor(model=os.getenv("TOLLGATE_HEADROOM_MODEL", "gpt-4o-mini")).compress(
        sample, target_tokens=max(len(sample) // 16, 64)
    )
    if not compressed or len(compressed) >= len(sample):
        raise RuntimeError("Headroom smoke test did not reduce the sample payload")
    return {
        "status": "passed",
        "input_chars": len(sample),
        "output_chars": len(compressed),
        "reduction_pct": round((1 - len(compressed) / len(sample)) * 100, 2),
    }


def verify_openai_live(model: str) -> dict[str, object]:
    provider = OpenAIResponsesProvider(
        pricing=OpenAIPricing(
            input_per_million_usd=float(os.getenv("OPENAI_INPUT_PRICE_PER_MILLION", "0")),
            output_per_million_usd=float(os.getenv("OPENAI_OUTPUT_PRICE_PER_MILLION", "0")),
        )
    )
    response = provider.complete(
        model=model,
        payload="Reply with exactly: tollgate provider smoke test passed",
        max_output_tokens=20,
    )
    return {
        "status": "passed",
        "model": model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "measured_cost_usd": response.cost_usd,
        "content": response.content,
    }


def verify_anthropic_live(model: str) -> dict[str, object]:
    provider = AnthropicMessagesProvider(
        pricing=AnthropicPricing(
            input_per_million_usd=float(os.getenv("ANTHROPIC_INPUT_PRICE_PER_MILLION", "0")),
            output_per_million_usd=float(os.getenv("ANTHROPIC_OUTPUT_PRICE_PER_MILLION", "0")),
            cache_write_per_million_usd=float(os.getenv("ANTHROPIC_CACHE_WRITE_PRICE_PER_MILLION", "0")),
            cache_read_per_million_usd=float(os.getenv("ANTHROPIC_CACHE_READ_PRICE_PER_MILLION", "0")),
        )
    )
    response = provider.complete(
        model=model,
        payload="Reply with exactly: tollgate Anthropic smoke test passed",
        max_output_tokens=30,
    )
    return {
        "status": "passed",
        "model": model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "measured_cost_usd": response.cost_usd,
        "content": response.content,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-openai", action="store_true")
    parser.add_argument("--live-anthropic", action="store_true")
    parser.add_argument("--model", default=os.getenv("OPENAI_SMOKE_MODEL", "gpt-4o-mini"))
    parser.add_argument("--anthropic-model", default=os.getenv("ANTHROPIC_SMOKE_MODEL", "claude-sonnet-4-20250514"))
    args = parser.parse_args()

    result: dict[str, object] = {"headroom": verify_headroom()}
    result["openai"] = verify_openai_live(args.model) if args.live_openai else {
        "status": "skipped", "reason": "use --live-openai"
    }
    result["anthropic"] = verify_anthropic_live(args.anthropic_model) if args.live_anthropic else {
        "status": "skipped", "reason": "use --live-anthropic"
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
