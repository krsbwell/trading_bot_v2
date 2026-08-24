"""
memory/ package — facades over database/ (and, for trade_memory, the
existing data/live_state_<market>.json) built for the adaptive-strategy
integration (tasks/todo.md, 2026-08-21 Phase 2).
"""
import json

import pytest

from database.models import init_schema
from engine.decision import Decision
from memory import agent_memory, decision_memory, knowledge_memory, market_memory, trade_memory


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "adaptive_test.db"
    init_schema(db_path=p)
    return p


# ── decision_memory ──────────────────────────────────────────────────────

def test_record_and_recent_decision(db_path):
    d = Decision(pair="EUR_USD", action="BUY", confidence=0.7,
                 regime="TRENDING_UP", model_version="v1")
    decision_memory.record(d, db_path=db_path)
    rows = decision_memory.recent(pair="EUR_USD", db_path=db_path)
    assert len(rows) == 1 and rows[0]["action"] == "BUY"


def test_recent_trade_decisions_excludes_no_trade(db_path):
    decision_memory.record(Decision(pair="EUR_USD", action="BUY", confidence=0.6,
                                     regime="TRENDING_UP", model_version="v1"), db_path=db_path)
    decision_memory.record(Decision.unrated("EUR_USD", regime="UNKNOWN", reason="thin data"),
                            db_path=db_path)
    rows = decision_memory.recent_trade_decisions(pair="EUR_USD", db_path=db_path)
    assert len(rows) == 1 and rows[0]["action"] == "BUY"


# ── market_memory ────────────────────────────────────────────────────────

def _regime_info(confidence=0.5):
    return {"regime": "RANGING", "confidence": confidence, "adx": 15.0,
            "atr_pct": 0.1, "ema_slope_pct": 0.0}


def test_no_baseline_below_minimum_snapshots(db_path):
    market_memory.record("EUR_USD", "M30", _regime_info(), db_path=db_path)
    assert market_memory.has_baseline("EUR_USD", "M30", db_path=db_path) is False
    assert market_memory.baseline_confidence_stats("EUR_USD", "M30", db_path=db_path) is None


def test_baseline_available_after_enough_snapshots(db_path):
    for _ in range(market_memory.MIN_SNAPSHOTS_FOR_BASELINE):
        market_memory.record("EUR_USD", "M30", _regime_info(confidence=0.6), db_path=db_path)
    assert market_memory.has_baseline("EUR_USD", "M30", db_path=db_path) is True
    stats = market_memory.baseline_confidence_stats("EUR_USD", "M30", db_path=db_path)
    assert stats is not None
    assert abs(stats["mean"] - 0.6) < 1e-9


# ── trade_memory ─────────────────────────────────────────────────────────

def test_recent_closed_trades_reads_existing_state_file(tmp_path):
    state = {
        "open_trades": {}, "pending_orders": {},
        "closed_trades": [
            {"pair": "EUR_JPY", "close_time": "2026-08-14T14:00:00+00:00", "realised_pnl": 5.7395},
            {"pair": "GBP_CAD", "close_time": "2026-08-13T17:35:53+00:00", "realised_pnl": 4.4301},
        ],
    }
    state_path = tmp_path / "live_state_forex.json"
    state_path.write_text(json.dumps(state), "utf-8")
    trades = trade_memory.recent_closed_trades(state_path=state_path)
    assert len(trades) == 2
    assert trades[0]["pair"] == "EUR_JPY"   # most recent close_time first


def test_recent_closed_trades_missing_file_returns_empty(tmp_path):
    assert trade_memory.recent_closed_trades(state_path=tmp_path / "nope.json") == []


def test_adaptive_trade_outcomes_empty_by_default(db_path):
    assert trade_memory.adaptive_trade_outcomes(db_path=db_path) == []


# ── agent_memory ─────────────────────────────────────────────────────────

def test_last_run_none_when_never_run(db_path):
    assert agent_memory.last_run("EUR_USD", db_path=db_path) is None


def test_record_and_last_run(db_path):
    agent_memory.record_run("EUR_USD", regime="RANGING", action_taken="NO_TRADE", db_path=db_path)
    last = agent_memory.last_run("EUR_USD", db_path=db_path)
    assert last["action_taken"] == "NO_TRADE"


def test_consecutive_no_trade_streak(db_path):
    agent_memory.record_run("EUR_USD", regime="RANGING", action_taken="BUY", db_path=db_path)
    agent_memory.record_run("EUR_USD", regime="RANGING", action_taken="NO_TRADE", db_path=db_path)
    agent_memory.record_run("EUR_USD", regime="RANGING", action_taken="NO_TRADE", db_path=db_path)
    assert agent_memory.consecutive_no_trade_streak("EUR_USD", db_path=db_path) == 2


# ── knowledge_memory ─────────────────────────────────────────────────────

def test_store_and_recent_knowledge(db_path):
    knowledge_memory.store("forexfactory", content_hash="h1", title="NFP", db_path=db_path)
    rows = knowledge_memory.recent(db_path=db_path)
    assert rows[0]["title"] == "NFP"


def test_store_dedups(db_path):
    first = knowledge_memory.store("forexfactory", content_hash="dupe", db_path=db_path)
    second = knowledge_memory.store("forexfactory", content_hash="dupe", db_path=db_path)
    assert first is not None and second is None
