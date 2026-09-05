from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from context_ledger.governance.runtime.provider_gateway import ProviderResponse


class OpenAIProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAIPricing:
    """Per-million-token prices used to reconcile measured SDK usage."""

    input_per_million_usd: float
    output_per_million_usd: float

    def calculate(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            (input_tokens / 1_000_000) * self.input_per_million_usd
            + (output_tokens / 1_000_000) * self.output_per_million_usd,
            8,
        )


class OpenAIResponsesProvider:
    """Concrete provider adapter using the official OpenAI Python SDK.

    The adapter intentionally accepts an injected client for deterministic tests.
    In production, omit `client` and configure `OPENAI_API_KEY`. Optional
    `OPENAI_BASE_URL` enables an OpenAI-compatible endpoint such as the Headroom
    proxy, while inline Headroom compression remains available independently.
    """

    def __init__(
        self,
        *,
        pricing: OpenAIPricing,
        client: Any | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.pricing = pricing
        if client is not None:
            self.client = client
            return
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise OpenAIProviderError(
                "OpenAI SDK is not installed; run: pip install -r requirements-pilot.txt"
            ) from exc

        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_api_key:
            raise OpenAIProviderError("OPENAI_API_KEY is required")
        kwargs: dict[str, Any] = {"api_key": resolved_api_key}
        resolved_base_url = base_url or os.getenv("OPENAI_BASE_URL")
        if resolved_base_url:
            kwargs["base_url"] = resolved_base_url
        self.client = OpenAI(**kwargs)

    def complete(self, *, model: str, payload: str, **kwargs: Any) -> ProviderResponse:
        instructions = kwargs.pop("instructions", None)
        request: dict[str, Any] = {"model": model, "input": payload, **kwargs}
        if instructions:
            request["instructions"] = instructions

        try:
            response = self.client.responses.create(**request)
        except Exception as exc:
            request_id = getattr(exc, "request_id", None)
            suffix = f" request_id={request_id}" if request_id else ""
            raise OpenAIProviderError(f"OpenAI Responses API call failed:{suffix} {exc}") from exc

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        content = str(getattr(response, "output_text", "") or "")
        if not content:
            raise OpenAIProviderError("OpenAI response contained no output_text")

        return ProviderResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self.pricing.calculate(input_tokens, output_tokens),
            raw=response,
        )
