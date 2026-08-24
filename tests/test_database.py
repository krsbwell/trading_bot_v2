"""
database/ package — sqlite-backed storage for the adaptive-strategy path
only (tasks/todo.md, 2026-08-21 Phase 2). Every test uses a tmp_path DB
file — never touches the real data/adaptive.db.
"""
import json

import pytest

from database import experiences, knowledge, market, models, trades


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "adaptive_test.db"


# ── models.py ────────────────────────────────────────────────────────────

def test_init_schema_is_idempotent(db_path):
    models.init_schema(db_path=db_path)
    models.init_schema(db_path=db_path)   # must not raise on second call


def test_get_connection_creates_parent_dir(tmp_path):
    nested = tmp_path / "nested" / "dir" / "adaptive.db"
    conn = models.get_connection(nested)
    conn.close()
    assert nested.exists()


# ── experiences.py (decisions table) ────────────────────────────────────

def _decision_dict(pair="EUR_USD", action="BUY", confidence=0.6, regime="TRENDING_UP"):
    return {"pair": pair, "action": action, "confidence": confidence, "regime": regime,
            "model_version": "v1", "stop_loss": 1.09, "take_profit": 1.11,
            "reasoning": {"why": "test"}, "timestamp": "2026-08-21T00:00:00+00:00"}


def test_insert_and_get_decision(db_path):
    row_id = experiences.insert_decision(_decision_dict(), db_path=db_path)
    assert row_id is not None
    rows = experiences.get_decisions(db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["pair"] == "EUR_USD"
    assert json.loads(rows[0]["reasoning_json"]) == {"why": "test"}


def test_get_decisions_filters_by_pair_and_action(db_path):
    experiences.insert_decision(_decision_dict(pair="EUR_USD", action="BUY"), db_path=db_path)
    experiences.insert_decision(_decision_dict(pair="GBP_USD", action="SELL"), db_path=db_path)
    rows = experiences.get_decisions(pair="EUR_USD", db_path=db_path)
    assert len(rows) == 1 and rows[0]["pair"] == "EUR_USD"
    rows = experiences.get_decisions(action="SELL", db_path=db_path)
    assert len(rows) == 1 and rows[0]["action"] == "SELL"


def test_no_trade_decisions_are_kept_too(db_path):
    experiences.insert_decision(_decision_dict(action="NO_TRADE", confidence=None), db_path=db_path)
    rows = experiences.get_decisions(db_path=db_path)
    assert rows[0]["action"] == "NO_TRADE"
    assert rows[0]["confidence"] is None


# ── market.py (regime_history table) ────────────────────────────────────

def test_insert_and_get_regime_history(db_path):
    info = {"regime": "RANGING", "confidence": 0.5, "adx": 15.0, "atr_pct": 0.1, "ema_slope_pct": 0.0}
    market.insert_regime_snapshot("EUR_USD", "M30", info, db_path=db_path)
    rows = market.get_regime_history("EUR_USD", "M30", db_path=db_path)
    assert len(rows) == 1
    assert rows[0]["regime"] == "RANGING"


def test_regime_history_scoped_by_pair_and_timeframe(db_path):
    info = {"regime": "RANGING", "confidence": 0.5, "adx": 15.0, "atr_pct": 0.1, "ema_slope_pct": 0.0}
    market.insert_regime_snapshot("EUR_USD", "M30", info, db_path=db_path)
    market.insert_regime_snapshot("EUR_USD", "H4", info, db_path=db_path)
    market.insert_regime_snapshot("GBP_USD", "M30", info, db_path=db_path)
    assert len(market.get_regime_history("EUR_USD", "M30", db_path=db_path)) == 1


# ── trades.py (trade_outcomes table) ────────────────────────────────────

def test_insert_and_get_trade_outcome(db_path):
    trades.insert_trade_outcome("EUR_USD", "long", pnl=5.0, outcome="win", db_path=db_path)
    rows = trades.get_trade_outcomes(db_path=db_path)
    assert len(rows) == 1 and rows[0]["outcome"] == "win"


def test_trade_outcomes_empty_by_default(db_path):
    assert trades.get_trade_outcomes(db_path=db_path) == []


def test_win_rate_by_regime_joins_decisions(db_path):
    decision_id = experiences.insert_decision(_decision_dict(regime="TRENDING_UP"), db_path=db_path)
    trades.insert_trade_outcome("EUR_USD", "long", pnl=5.0, outcome="win",
                                 decision_id=decision_id, db_path=db_path)
    other_id = experiences.insert_decision(_decision_dict(regime="RANGING"), db_path=db_path)
    trades.insert_trade_outcome("EUR_USD", "long", pnl=-3.0, outcome="loss",
                                 decision_id=other_id, db_path=db_path)
    result = trades.win_rate_by_regime(db_path=db_path)
    assert result["TRENDING_UP"]["wins"] == 1
    assert result["TRENDING_UP"]["win_rate"] == 1.0
    assert result["RANGING"]["win_rate"] == 0.0


def test_win_rate_by_regime_empty_when_no_trades(db_path):
    assert trades.win_rate_by_regime(db_path=db_path) == {}


# ── knowledge.py ─────────────────────────────────────────────────────────

def test_insert_and_get_knowledge(db_path):
    row_id = knowledge.insert_knowledge("forexfactory", content_hash="abc123",
                                         title="NFP release", db_path=db_path)
    assert row_id is not None
    rows = knowledge.get_knowledge(db_path=db_path)
    assert rows[0]["title"] == "NFP release"


def test_knowledge_dedups_on_content_hash(db_path):
    first = knowledge.insert_knowledge("forexfactory", content_hash="dupe", db_path=db_path)
    second = knowledge.insert_knowledge("forexfactory", content_hash="dupe", db_path=db_path)
    assert first is not None
    assert second is None
    assert len(knowledge.get_knowledge(db_path=db_path)) == 1
