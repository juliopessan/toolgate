"""Store central do agent-finops: SQLite em ~/.agent-finops/telemetry.db."""
import json
import os
import sqlite3
from pathlib import Path

DB_DIR = Path(os.environ.get("AGENT_FINOPS_HOME", Path.home() / ".agent-finops"))
DB_PATH = DB_DIR / "telemetry.db"
PRICING_PATH = Path(__file__).parent / "pricing.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT (datetime('now')),
    session_id TEXT,
    project TEXT,
    tool_name TEXT,
    input_chars INTEGER,
    output_chars INTEGER,
    est_tokens INTEGER
);
CREATE TABLE IF NOT EXISTS usage (
    message_id TEXT PRIMARY KEY,
    ts TEXT,
    session_id TEXT,
    project TEXT,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS savings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT (datetime('now')),
    source TEXT,            -- 'headroom' | 'ast' | 'rightsizing'
    project TEXT,
    tokens_saved INTEGER,
    usd_saved REAL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS budgets (
    project TEXT PRIMARY KEY,
    monthly_usd REAL,
    alert_pct REAL DEFAULT 0.8
);
CREATE TABLE IF NOT EXISTS agent_registry (
    name TEXT PRIMARY KEY,
    project TEXT,
    model TEXT,
    status TEXT DEFAULT 'draft',   -- draft|validated|production|deprecated
    owner TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_project ON usage(project, ts);
CREATE INDEX IF NOT EXISTS idx_events_project ON tool_events(project, ts);
"""


def connect() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def load_pricing() -> dict:
    with open(PRICING_PATH) as f:
        return json.load(f)


def cost_usd(pricing: dict, model: str, inp: int, out: int, cread: int, cwrite: int) -> float:
    base = None
    for key, p in pricing["models"].items():
        if model and model.startswith(key):
            base = p
            break
    if base is None:
        base = pricing["default"]
    i, o = base["input"] / 1e6, base["output"] / 1e6
    return (
        inp * i
        + out * o
        + cread * i * pricing["cache_read_multiplier"]
        + cwrite * i * pricing["cache_write_multiplier"]
    )
