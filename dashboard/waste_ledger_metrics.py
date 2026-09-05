from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def load_waste_ledger_metrics(db_path: str | Path) -> dict[str, Any]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        totals = conn.execute(
            """
            SELECT
              COUNT(DISTINCT artifact_id) AS artifacts,
              COALESCE(SUM(tokens_candidate), 0) AS tokens_candidate,
              COALESCE(SUM(tokens_transmitted), 0) AS tokens_transmitted,
              COALESCE(SUM(tokens_rejected), 0) AS tokens_rejected,
              COALESCE(SUM(actual_cost_usd), 0) AS actual_cost_usd,
              SUM(CASE WHEN decision='blocked' THEN 1 ELSE 0 END) AS blocked_events,
              SUM(CASE WHEN decision='admitted' THEN 1 ELSE 0 END) AS admitted_events
            FROM waste_ledger_events
            """
        ).fetchone()
        by_tier = [
            dict(row)
            for row in conn.execute(
                """
                SELECT tier,
                       COUNT(*) AS events,
                       COALESCE(SUM(tokens_transmitted), 0) AS tokens_transmitted,
                       COALESCE(SUM(actual_cost_usd), 0) AS actual_cost_usd
                FROM waste_ledger_events
                GROUP BY tier
                ORDER BY events DESC
                """
            )
        ]
        by_reason = [
            dict(row)
            for row in conn.execute(
                """
                SELECT reason_code, COUNT(*) AS events
                FROM waste_ledger_events
                WHERE reason_code IS NOT NULL
                GROUP BY reason_code
                ORDER BY events DESC
                """
            )
        ]
        active_reservations = conn.execute(
            "SELECT COUNT(*) FROM zwca_budget_reservations WHERE status='active'"
        ).fetchone()[0]

    candidate = int(totals["tokens_candidate"])
    transmitted = int(totals["tokens_transmitted"])
    reduction_pct = round((1 - transmitted / candidate) * 100, 2) if candidate else 0.0
    return {
        "summary": {
            **dict(totals),
            "blended_reduction_pct": reduction_pct,
            "active_reservations": int(active_reservations),
        },
        "by_tier": by_tier,
        "by_reason": by_reason,
    }
