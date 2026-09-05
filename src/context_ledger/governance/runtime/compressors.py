from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class CompressionError(RuntimeError):
    pass


class CompressionAdapter(Protocol):
    def compress(self, payload: str, target_tokens: int) -> str: ...


@dataclass
class ConservativeCompressor:
    chars_per_token: int = 4

    def compress(self, payload: str, target_tokens: int) -> str:
        return payload[: max(target_tokens * self.chars_per_token, 0)]


@dataclass
class HeadroomCompressor:
    """Inline adapter for the installed Headroom Python SDK.

    Headroom operates on message collections and returns a result containing
    compressed messages plus measured compression metadata. The adapter keeps
    the Guardian contract string-based while using Headroom's supported public
    `compress()` API instead of an assumed CLI JSON contract.
    """

    model: str = "gpt-4o-mini"
    protect_recent: int = 0
    minimum_target_ratio: float = 0.05

    def compress(self, payload: str, target_tokens: int) -> str:
        try:
            from headroom import compress as headroom_compress
        except ImportError as exc:
            raise CompressionError(
                'Headroom is not installed; run: pip install -r requirements-pilot.txt'
            ) from exc

        approximate_tokens = max(len(payload) // 4, 1)
        target_ratio = min(
            1.0,
            max(self.minimum_target_ratio, target_tokens / approximate_tokens),
        )
        messages = [{"role": "user", "content": payload}]
        try:
            result: Any = headroom_compress(
                messages,
                model=self.model,
                compress_user_messages=True,
                target_ratio=target_ratio,
                protect_recent=self.protect_recent,
            )
        except Exception as exc:
            raise CompressionError(f"Headroom compression failed: {exc}") from exc

        compressed_messages = getattr(result, "messages", None)
        if not compressed_messages:
            raise CompressionError("Headroom returned no compressed messages")

        content = compressed_messages[-1].get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") in {"text", "input_text"}
            ]
            return "\n".join(part for part in text_parts if part)
        raise CompressionError("Headroom returned an unsupported message content type")

    def __call__(self, payload: str, target_tokens: int) -> str:
        return self.compress(payload, target_tokens)


@dataclass
class ASTAwareCompressor:
    """Keeps structural declarations and removes low-value blank/comment lines.

    This adapter is intentionally language-neutral for the pilot. Platform-specific
    tree-sitter adapters can replace it without changing the Guardian contract.
    """

    chars_per_token: int = 4

    def compress(self, payload: str, target_tokens: int) -> str:
        limit = max(target_tokens * self.chars_per_token, 0)
        lines = payload.splitlines()
        structural = []
        secondary = []
        markers = ("class ", "def ", "function ", "procedure ", "CREATE ", "TABLE ", "SELECT ")
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "//", "--")):
                continue
            (structural if any(marker in line for marker in markers) else secondary).append(line)
        result = "\n".join(structural + secondary)
        return result[:limit]


@dataclass
class FallbackCompressor:
    primary: CompressionAdapter
    fallback: CompressionAdapter

    def compress(self, payload: str, target_tokens: int) -> str:
        try:
            return self.primary.compress(payload, target_tokens)
        except CompressionError:
            return self.fallback.compress(payload, target_tokens)

    def __call__(self, payload: str, target_tokens: int) -> str:
        return self.compress(payload, target_tokens)
