from __future__ import annotations

from pathlib import Path
from typing import Any

from tollgate.governance.runtime.guardian import TierPolicy


class PolicyError(ValueError):
    pass


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise PolicyError("PyYAML is required to load ZWCA policy files") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PolicyError("policy root must be a mapping")
    return data


def load_tier_policies(path: str | Path) -> dict[str, TierPolicy]:
    raw = _load_yaml(Path(path))
    controls = raw.get("controls") or {}
    default_attempts = int(controls.get("max_recompression_attempts", 2))
    tiers = raw.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise PolicyError("policy must define at least one tier")

    result: dict[str, TierPolicy] = {}
    for tier in tiers:
        if not isinstance(tier, dict):
            raise PolicyError("tier entries must be mappings")
        tier_id = str(tier.get("id", "")).strip()
        if not tier_id:
            raise PolicyError("tier id is required")
        if tier_id in result:
            raise PolicyError(f"duplicate tier id: {tier_id}")
        input_cap = int(tier.get("input_token_cap", -1))
        output_cap = int(tier.get("output_token_cap", -1))
        if input_cap < 0 or output_cap < 0:
            raise PolicyError(f"tier {tier_id} has invalid token caps")
        result[tier_id] = TierPolicy(
            name=tier_id,
            input_token_cap=input_cap,
            output_token_cap=output_cap,
            max_recompress_attempts=int(
                tier.get("max_recompression_attempts", default_attempts)
            ),
        )
    return result
