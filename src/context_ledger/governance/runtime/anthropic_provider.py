from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from context_ledger.governance.runtime.provider_gateway import ProviderResponse


@dataclass(frozen=True)
class AnthropicPricing:
    input_per_million_usd: float = 0.0
    output_per_million_usd: float = 0.0
    cache_write_per_million_usd: float = 0.0
    cache_read_per_million_usd: float = 0.0


class AnthropicMessagesProvider:
    """Concrete Anthropic SDK adapter using the Messages API."""

    def __init__(self, pricing: AnthropicPricing, client: Any | None = None) -> None:
        if client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise RuntimeError("anthropic SDK is not installed") from exc
            client = Anthropic()
        self.client = client
        self.pricing = pricing

    def complete(self, *, model: str, payload: str, **kwargs: Any) -> ProviderResponse:
        max_tokens = int(kwargs.pop("max_output_tokens", kwargs.pop("max_tokens", 1024)))
        system = kwargs.pop("instructions", kwargs.pop("system", None))
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": payload}],
            **kwargs,
        }
        if system:
            request["system"] = system

        response = self.client.messages.create(**request)
        usage = response.usage
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)

        content = "".join(
            block.text for block in response.content
            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        )
        cost = (
            input_tokens * self.pricing.input_per_million_usd
            + output_tokens * self.pricing.output_per_million_usd
            + cache_write * self.pricing.cache_write_per_million_usd
            + cache_read * self.pricing.cache_read_per_million_usd
        ) / 1_000_000

        return ProviderResponse(
            content=content,
            input_tokens=input_tokens + cache_write + cache_read,
            output_tokens=output_tokens,
            cost_usd=cost,
            raw=response,
        )
