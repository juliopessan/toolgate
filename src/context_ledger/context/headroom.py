"""Layer 2 - headroom.

Token counting tells you what something costs. Headroom tells you what you can
still afford, and that is the number every other layer needs.

A context window is not a single pool. Part of it is spoken for before the first
tool call: the system prompt, the conversation so far, and the space the model
needs to *answer*. What is left over is the headroom, and the job of this module
is to (a) track it honestly and (b) divide it across competing lanes - the AST
skeleton, the semantic hits, the diff, the logs - each of which will happily
consume the whole window if you let it.

The allocator is a weighted largest-remainder apportionment with floors and
ceilings, run to a fixed point: lanes clamped at their ``maximum`` return the
surplus to the pool, and the loop redistributes it to lanes that can still grow.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from .tokens import estimate_tokens

#: Fraction of the *effective* budget consumed, mapped to a coarse pressure band.
#: Callers branch on the band rather than on raw ratios so that policy lives here.
PRESSURE_BANDS = (
    (0.50, "cool"),
    (0.75, "warm"),
    (0.90, "hot"),
    (float("inf"), "critical"),
)

#: How aggressive each layer should be at a given pressure band. Consumed by
#: :func:`policy_for` and by ``pack.py`` when it decides how hard to compress.
PRESSURE_POLICY = {
    "cool": {"detail": "full", "slice_mode": "symbol", "semantic_k": 12, "elide_bodies": False},
    "warm": {"detail": "trimmed", "slice_mode": "symbol", "semantic_k": 8, "elide_bodies": False},
    "hot": {"detail": "skeleton", "slice_mode": "signature", "semantic_k": 5, "elide_bodies": True},
    "critical": {"detail": "index-only", "slice_mode": "signature", "semantic_k": 3, "elide_bodies": True},
}


@dataclass
class Lane:
    """One competing consumer of headroom.

    ``weight`` sets the share of the free pool. ``minimum`` is a floor honoured
    even under pressure (a lane worth including at all is worth including
    legibly); ``maximum`` caps a lane that would otherwise swallow the window.
    """

    name: str
    weight: float = 1.0
    minimum: int = 0
    maximum: Optional[int] = None
    priority: int = 0  # Higher wins when the floors alone exceed the budget.


@dataclass
class Allocation:
    lane: str
    tokens: int
    weight: float
    clamped: Optional[str] = None  # "minimum" | "maximum" | "dropped"

    def to_dict(self) -> dict:
        return asdict(self)


class Headroom:
    """Live accounting of one context window.

    >>> h = Headroom(window=200_000, reserve_output=8_000, reserve_system=2_000)
    >>> h.spend("conversation", 40_000)
    40000
    >>> h.available > 0
    True
    """

    def __init__(
        self,
        window: int = 200_000,
        reserve_output: int = 8_000,
        reserve_system: int = 0,
        safety_margin: float = 0.02,
    ) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        if not 0.0 <= safety_margin < 1.0:
            raise ValueError("safety_margin must be in [0, 1)")
        self.window = window
        self.reserve_output = max(0, reserve_output)
        self.reserve_system = max(0, reserve_system)
        # Estimation error is real and one-sided in our favour only if we book it.
        self.safety_margin = safety_margin
        self._spent: Dict[str, int] = {}

    # ---- accounting ------------------------------------------------------

    @property
    def effective(self) -> int:
        """Tokens actually spendable on content, after every reservation."""
        gross = self.window - self.reserve_output - self.reserve_system
        return max(0, int(gross * (1.0 - self.safety_margin)))

    @property
    def used(self) -> int:
        return sum(self._spent.values())

    @property
    def available(self) -> int:
        return max(0, self.effective - self.used)

    @property
    def ratio(self) -> float:
        return 1.0 if self.effective == 0 else min(1.0, self.used / self.effective)

    @property
    def pressure(self) -> str:
        for threshold, band in PRESSURE_BANDS:
            if self.ratio < threshold:
                return band
        return "critical"

    @property
    def policy(self) -> dict:
        """The compression policy implied by current pressure."""
        return dict(PRESSURE_POLICY[self.pressure])

    def spend(self, name: str, tokens: int) -> int:
        """Book ``tokens`` against lane ``name``. Repeat names accumulate."""
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        self._spent[name] = self._spent.get(name, 0) + tokens
        return self._spent[name]

    def spend_text(self, name: str, text: str, profile: Optional[str] = None) -> int:
        """Book the estimated cost of ``text`` against lane ``name``."""
        return self.spend(name, estimate_tokens(text, profile))

    def release(self, name: str) -> int:
        """Give back everything booked under ``name`` (e.g. a dropped lane)."""
        return self._spent.pop(name, 0)

    def fits(self, tokens: int) -> bool:
        return tokens <= self.available

    # ---- allocation ------------------------------------------------------

    def plan(self, lanes: List[Lane], budget: Optional[int] = None) -> List[Allocation]:
        """Divide ``budget`` (default: whatever is available) across ``lanes``.

        Floors are paid first, in priority order. If the floors alone overrun the
        budget, the lowest-priority lanes are dropped rather than shaving every
        lane into uselessness - half a skeleton is worse than no skeleton.
        The remainder is then apportioned by weight, with ceilings respected and
        their surplus redistributed until nothing more can be placed.
        """
        pool = self.available if budget is None else max(0, budget)
        if not lanes:
            return []

        allocations: Dict[str, Allocation] = {
            lane.name: Allocation(lane.name, 0, lane.weight) for lane in lanes
        }

        # 1. Floors, richest-priority first.
        ordered = sorted(lanes, key=lambda ln: (-ln.priority, -ln.weight, ln.name))
        live: List[Lane] = []
        for lane in ordered:
            floor = min(lane.minimum, lane.maximum) if lane.maximum is not None else lane.minimum
            if floor > pool:
                allocations[lane.name].clamped = "dropped"
                continue
            allocations[lane.name].tokens = floor
            pool -= floor
            live.append(lane)

        # 2. Weighted apportionment of the remainder, to a fixed point.
        growable = [ln for ln in live if ln.maximum is None or allocations[ln.name].tokens < ln.maximum]
        while pool > 0 and growable:
            total_weight = sum(ln.weight for ln in growable if ln.weight > 0)
            if total_weight <= 0:
                break
            start_pool = pool
            # Largest-remainder: hand out the integer parts, then the leftovers to
            # the lanes with the biggest fractional claim. No token is lost to floor().
            shares = []
            for lane in growable:
                exact = pool * (lane.weight / total_weight)
                shares.append((lane, int(exact), exact - int(exact)))
            leftover = pool - sum(whole for _, whole, _ in shares)
            for idx, (lane, whole, _frac) in enumerate(
                sorted(shares, key=lambda item: -item[2])
            ):
                bonus = 1 if idx < leftover else 0
                grant = whole + bonus
                if grant <= 0:
                    continue
                alloc = allocations[lane.name]
                ceiling = lane.maximum
                if ceiling is not None and alloc.tokens + grant > ceiling:
                    grant = ceiling - alloc.tokens
                    alloc.clamped = "maximum"
                alloc.tokens += grant
                pool -= grant
            growable = [
                ln for ln in growable if ln.maximum is None or allocations[ln.name].tokens < ln.maximum
            ]
            if pool == start_pool:
                break  # No lane could absorb anything; stop rather than spin.

        for lane in lanes:
            alloc = allocations[lane.name]
            if alloc.clamped is None and lane.minimum and alloc.tokens == lane.minimum:
                alloc.clamped = "minimum"

        return [allocations[lane.name] for lane in lanes]

    # ---- reporting -------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "window": self.window,
            "reserve_output": self.reserve_output,
            "reserve_system": self.reserve_system,
            "safety_margin": self.safety_margin,
            "effective": self.effective,
            "used": self.used,
            "available": self.available,
            "ratio": round(self.ratio, 4),
            "pressure": self.pressure,
            "policy": self.policy,
            "lanes": dict(sorted(self._spent.items(), key=lambda kv: -kv[1])),
        }

    def render(self, width: int = 40) -> str:
        """A one-glance textual gauge, for CLI output and statuslines."""
        filled = int(round(self.ratio * width))
        bar = "#" * filled + "-" * (width - filled)
        lines = [
            f"[{bar}] {self.ratio * 100:5.1f}%  {self.pressure.upper()}",
            f"  window {self.window:,}  effective {self.effective:,}  "
            f"used {self.used:,}  free {self.available:,}",
        ]
        for name, tokens in sorted(self._spent.items(), key=lambda kv: -kv[1]):
            share = 0.0 if self.effective == 0 else tokens / self.effective * 100
            lines.append(f"  {name:<24} {tokens:>9,}  {share:5.1f}%")
        return "\n".join(lines)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Headroom {self.used:,}/{self.effective:,} {self.pressure}>"


def policy_for(pressure: str) -> dict:
    """Compression policy for a pressure band, defaulting to the safest one."""
    return dict(PRESSURE_POLICY.get(pressure, PRESSURE_POLICY["critical"]))
