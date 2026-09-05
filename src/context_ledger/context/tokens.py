"""Layer 1 - token accounting.

A tokenizer-free estimator. A real BPE tokenizer costs a heavy dependency and a
model-specific vocabulary; for *budgeting* decisions what matters is a stable,
slightly conservative number, not an exact one. So we calibrate chars-per-token
per content profile, because the ratio that is right for prose is ~25% wrong for
minified JSON.

Every estimate here rounds up. Under-counting is the only failure mode that
actually hurts: it overflows the window.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, asdict
from typing import Dict, Literal, Optional

Profile = Literal["prose", "markdown", "mixed", "code", "json", "base64"]

#: Chars-per-token ratios calibrated against cl100k/o200k-style vocabularies.
#: Denser syntax means more tokens per character, hence a smaller divisor.
TOKEN_PROFILES: Dict[str, float] = {
    "prose": 4.0,
    "markdown": 3.8,
    "mixed": 3.5,
    "code": 3.2,
    "json": 2.9,
    "base64": 1.4,
}

# ANSI SGR/CSI escape sequences. Never billed, always stripped before counting.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

_CODE_SIGNALS_RE = re.compile(r"[{}();=<>\[\]|&$#@]|=>|::|->")
_JSON_STRUCTURAL_RE = re.compile(r"[\"{}\[\]:,]")
_HEADING_RE = re.compile(r"^#{1,6} ", re.MULTILINE)
_FENCE_RE = re.compile(r"^```", re.MULTILINE)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")
_WS_RE = re.compile(r"\s")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences. Colour bytes cost the caller nothing."""
    return _ANSI_RE.sub("", text or "")


def detect_profile(text: str) -> Profile:
    """Guess which token profile a blob of text belongs to.

    Cheap structural signals only, no parsing. Order matters: base64 and JSON are
    tested first because both read as "code" to a punctuation-density test.
    """
    s = strip_ansi(text)
    if not s.strip():
        return "prose"
    sample = s[:8192]
    trimmed = sample.strip()

    if _BASE64_RE.fullmatch(trimmed) and len(_WS_RE.sub("", trimmed)) > 128:
        # Long, alphabet-only and essentially whitespace-free: an encoded blob.
        if len(_WS_RE.findall(trimmed)) / len(trimmed) < 0.02:
            return "base64"

    if trimmed[:1] in "[{" and trimmed[-1:] in "]}":
        if len(_JSON_STRUCTURAL_RE.findall(sample)) / len(sample) > 0.08:
            return "json"

    code_density = len(_CODE_SIGNALS_RE.findall(sample)) / len(sample)
    if code_density > 0.045:
        return "code"
    if _HEADING_RE.search(sample) or _FENCE_RE.search(sample):
        return "markdown"
    if code_density > 0.02:
        return "mixed"
    return "prose"


def estimate_tokens(text: str, profile: Optional[str] = None) -> int:
    """Estimate the token cost of ``text``.

    ``profile`` defaults to auto-detection. Newlines are charged separately
    rather than diluted into the character average, because a newline is its own
    token far more often than the ratio implies.
    """
    s = strip_ansi(text)
    if not s:
        return 0
    resolved = detect_profile(s) if profile in (None, "auto") else profile
    divisor = TOKEN_PROFILES.get(resolved, TOKEN_PROFILES["mixed"])
    newlines = s.count("\n")
    body = math.ceil((len(s) - newlines) / divisor)
    return max(1, body + newlines)


@dataclass(frozen=True)
class Measurement:
    """A token estimate plus the evidence behind it."""

    tokens: int
    chars: int
    lines: int
    bytes: int
    profile: str
    chars_per_token: float

    def to_dict(self) -> dict:
        return asdict(self)


def measure(text: str, profile: Optional[str] = None) -> Measurement:
    """Estimate tokens and return the reasoning alongside the number.

    Used by ``--explain`` output and by callers who want to log *why* a budget
    decision went the way it did.
    """
    s = strip_ansi(text)
    resolved = detect_profile(s) if profile in (None, "auto") else profile
    return Measurement(
        tokens=estimate_tokens(s, resolved),
        chars=len(s),
        lines=len(s.splitlines()) if s else 0,
        bytes=len(s.encode("utf-8")),
        profile=resolved,
        chars_per_token=TOKEN_PROFILES.get(resolved, TOKEN_PROFILES["mixed"]),
    )


def estimate_cost(
    tokens: int,
    input_per_mtok: float = 0.0,
    output_per_mtok: float = 0.0,
    output_tokens: int = 0,
) -> float:
    """Cost in USD for ``tokens`` of input plus ``output_tokens`` of output."""
    return round((tokens / 1e6) * input_per_mtok + (output_tokens / 1e6) * output_per_mtok, 6)


@dataclass(frozen=True)
class TrimResult:
    text: str
    truncated: bool
    kept_lines: int
    total_lines: int
    tokens: int

    def to_dict(self) -> dict:
        return asdict(self)


def _take(lines, allowance: int, profile: Optional[str]):
    out = []
    used = 0
    for line in lines:
        cost = estimate_tokens(line, profile) + 1
        if used + cost > allowance:
            break
        out.append(line)
        used += cost
    return out


def trim_to_budget(
    text: str,
    budget_tokens: int,
    keep: Literal["head", "tail", "both"] = "head",
    hint: str = "",
    profile: Optional[str] = None,
) -> TrimResult:
    """Trim ``text`` to ``budget_tokens`` along whole-line boundaries.

    ``keep='head'`` preserves the opening (source files, documents), ``'tail'``
    the ending (logs, where the failure is last) and ``'both'`` keeps a head and
    a tail with the middle elided - the shape that survives stack traces, where
    the command and the error sit at opposite ends.

    The marker is always emitted and always states how much was dropped: a
    truncation the caller cannot see is a truncation the caller will not fix.
    """
    s = strip_ansi(text)
    total = estimate_tokens(s, profile)
    lines = s.split("\n")

    if total <= budget_tokens:
        return TrimResult(s, False, len(lines), len(lines), total)

    # Reserve room for the marker itself, so the result *including* its marker
    # still fits the budget the caller asked for.
    body = max(1, budget_tokens - 48)

    if keep == "tail":
        kept = list(reversed(_take(reversed(lines), body, profile)))
        kept_lines = len(kept)
    elif keep == "both":
        half = max(1, body // 2)
        head = _take(lines, half, profile)
        tail = list(reversed(_take(reversed(lines), body - half, profile)))
        # A short input can make head and tail overlap; the tail wins shared lines.
        tail_start = len(lines) - len(tail)
        head_safe = head[: max(0, min(len(head), tail_start))]
        elided = max(0, tail_start - len(head_safe))
        kept = head_safe + [f"... {elided} lines elided ..."] + tail
        kept_lines = len(kept) - 1
    else:
        kept = _take(lines, body, profile)
        kept_lines = len(kept)

    suffix = f" {hint}" if hint else ""
    marker = (
        f"[tools-tokens] truncated to fit {budget_tokens} tokens "
        f"({total} estimated, {len(lines)} lines, keeping {keep}).{suffix}"
    )
    assembled = f"{marker}\n" + "\n".join(kept) if keep == "tail" else "\n".join(kept) + f"\n{marker}"

    return TrimResult(assembled, True, kept_lines, len(lines), estimate_tokens(assembled, profile))
