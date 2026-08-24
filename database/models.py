"""
Storage layer for the adaptive-strategy path only — stdlib sqlite3, one
new file at data/adaptive.db.

This is deliberately NOT a migration of this project's existing storage
(data/signal_log.csv, data/live_state_forex.json, data/wfo_params.json,
data/adaptive_state.json). Those stay exactly as they are — the dashboard
reads several of them directly — so nothing about the existing live system
changes. This database only holds data that doesn't exist anywhere else
today: structured Decision records, regime-classification history, and
(once the adaptive strategy actually trades) the trade outcomes linked
back to the decision that produced them.

See tasks/todo.md 2026-08-21 "Adaptive AI/ML Strategy — Integration Plan"
for the full rationale. Used only via database/trades.py, market.py,
experiences.py, knowledge.py — no other module should import sqlite3
directly for this data.
"""
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
DEFAULT_DB_PATH = _ROOT / "data" / "adaptive.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    pair           TEXT NOT NULL,
    action         TEXT NOT NULL,
    confidence     REAL,
    regime         TEXT NOT NULL,
    model_version  TEXT,
    stop_loss      REAL,
    take_profit    REAL,
    reasoning_json TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_pair ON decisions(pair);
CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON decisions(created_at);

CREATE TABLE IF NOT EXISTS regime_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    pair           TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    regime         TEXT NOT NULL,
    confidence     REAL,
    adx            REAL,
    atr_pct        REAL,
    ema_slope_pct  REAL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_regime_pair_tf ON regime_history(pair, timeframe);

CREATE TABLE IF NOT EXISTS trade_outcomes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id   INTEGER REFERENCES decisions(id),
    pair          TEXT NOT NULL,
    direction     TEXT NOT NULL,
    entry_price   REAL,
    exit_price    REAL,
    pnl           REAL,
    outcome       TEXT,
    opened_at     TEXT,
    closed_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_pair ON trade_outcomes(pair);

CREATE TABLE IF NOT EXISTS agent_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pair         TEXT NOT NULL,
    regime       TEXT,
    action_taken TEXT,
    notes_json   TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT NOT NULL,
    url            TEXT,
    retrieved_at   TEXT NOT NULL,
    published_at   TEXT,
    title          TEXT,
    content_hash   TEXT NOT NULL UNIQUE,
    summary        TEXT,
    topics_json    TEXT
);
"""


def get_connection(db_path: "Path | str | None" = None) -> sqlite3.Connection:
    """One connection per call — sqlite3 connections aren't safe to share
    across threads, and main.py's scheduler + dashboard run in different
    threads/processes. Callers are expected to use this via a `with`
    block or close it themselves; the database/* helper functions below
    all do this internally so most callers never touch this directly.

    db_path=None resolves to this module's DEFAULT_DB_PATH *at call time*
    (looked up here, not baked into a default-argument value) — every
    database/*.py and memory/*.py function threads db_path=None through
    the same way, so monkeypatching database.models.DEFAULT_DB_PATH in a
    test redirects all of them at once."""
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: "sqlite3.Connection | None" = None, db_path: "Path | str | None" = None) -> None:
    """Idempotent — CREATE TABLE IF NOT EXISTS throughout, safe to call on
    every startup the way main.py already does for other state files."""
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
