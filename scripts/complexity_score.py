#!/usr/bin/env python3
"""Deterministic complexity scoring and tier selection for ZWCA.

This module intentionally performs no LLM calls. It converts structural features
extracted by platform adapters into a stable 0–100 score used by the dispatch
policy. Weights are initial defaults and must be calibrated against Phase 0 data.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ComplexityFeatures:
    ast_node_count: int
    dependency_depth: int
    transform_density: float
    branch_density: float
    external_system_count: int
    unsupported_construct_count: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ComplexityFeatures":
        required = {field.name for field in cls.__dataclass_fields__.values()}
        missing = sorted(required - value.keys())
        if missing:
            raise ValueError(f"Missing required complexity features: {', '.join(missing)}")

        features = cls(
            ast_node_count=int(value["ast_node_count"]),
            dependency_depth=int(value["dependency_depth"]),
            transform_density=float(value["transform_density"]),
            branch_density=float(value["branch_density"]),
            external_system_count=int(value["external_system_count"]),
            unsupported_construct_count=int(value["unsupported_construct_count"]),
        )
        features.validate()
        return features

    def validate(self) -> None:
        integer_values = {
            "ast_node_count": self.ast_node_count,
            "dependency_depth": self.dependency_depth,
            "external_system_count": self.external_system_count,
            "unsupported_construct_count": self.unsupported_construct_count,
        }
        for name, value in integer_values.items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")

        for name, value in {
            "transform_density": self.transform_density,
            "branch_density": self.branch_density,
        }.items():
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class ScoreResult:
    score: float
    tier: str
    components: Mapping[str, float]
    features: ComplexityFeatures


TIERS = (
    (0, 15, "solar"),
    (16, 30, "daylight"),
    (31, 45, "horizon"),
    (46, 60, "twilight"),
    (61, 80, "starlight"),
    (81, 100, "aurora"),
)


def _saturating_log(value: int, reference: int) -> float:
    """Return a bounded 0–1 score with diminishing growth."""
    if value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(reference))


def calculate_score(features: ComplexityFeatures) -> ScoreResult:
    """Calculate a deterministic complexity score from structural evidence."""
    components = {
        "ast_size": 25.0 * _saturating_log(features.ast_node_count, 5000),
        "dependency_depth": 20.0 * min(features.dependency_depth / 12.0, 1.0),
        "transform_density": 20.0 * features.transform_density,
        "branch_density": 15.0 * features.branch_density,
        "external_systems": 10.0 * min(features.external_system_count / 5.0, 1.0),
        "unsupported_constructs": 10.0 * min(features.unsupported_construct_count / 8.0, 1.0),
    }
    score = round(min(100.0, sum(components.values())), 2)
    return ScoreResult(
        score=score,
        tier=tier_for_score(score),
        components={key: round(value, 2) for key, value in components.items()},
        features=features,
    )


def tier_for_score(score: float) -> str:
    if not 0 <= score <= 100:
        raise ValueError("score must be between 0 and 100")
    integer_score = math.ceil(score)
    for minimum, maximum, tier in TIERS:
        if minimum <= integer_score <= maximum:
            return tier
    return "aurora"


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate a ZWCA complexity score")
    parser.add_argument("input", type=Path, help="JSON file containing structural features")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = calculate_score(ComplexityFeatures.from_mapping(payload))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
        return 2

    output = {
        "score": result.score,
        "tier": result.tier,
        "components": result.components,
        "features": asdict(result.features),
    }
    print(json.dumps(output, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
