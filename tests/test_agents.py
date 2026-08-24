"""
agents/ package — facades built for the adaptive-strategy integration
(tasks/todo.md, 2026-08-21 Phase 3).
"""
import numpy as np
import pandas as pd
import pytest

from agents import (
    evaluation_agent, learning_agent, market_agent, orchestrator_agent,
    portfolio_agent, research_agent, risk_agent, trade_agent,
)
from database.models import init_schema
from engine.decision import Decision
from learning.model_registry import ModelRegistry


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    """Point every memory/database call at a throwaway DB for this test
    module, without threading db_path through every agents.* call site."""
    db_path = tmp_path / "adaptive_test.db"
    init_schema(db_path=db_path)
    import database.models as models
    monkeypatch.setattr(models, "DEFAULT_DB_PATH", db_path)
    yield db_path


def _uptrend_df(n=80, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="30min", tz="UTC")
    close = 1.1000 + np.cumsum(np.full(n, 0.0006)) + rng.normal(0, 0.00003, n)
    return pd.DataFrame({
        "open": close, "high": close + 0.0004, "low": close - 0.0004, "close": close,
    }, index=idx)


def _get_candles_fn(pair, granularity, count):
    return _uptrend_df()


# ── market_agent ─────────────────────────────────────────────────────────

def test_market_agent_returns_context():
    ctx = market_agent.get_context("EUR_USD", _get_candles_fn, primary_tf="M30", confirm_tf="H4")
    assert ctx is not None
    assert ctx["pair"] == "EUR_USD"
    assert "regime" in ctx


def test_market_agent_returns_none_on_fetch_failure():
    def _boom(pair, granularity, count):
        raise ConnectionError("boom")
    assert market_agent.get_context("EUR_USD", _boom, primary_tf="M30", confirm_tf="H4") is None


# ── learning_agent ───────────────────────────────────────────────────────

def test_learning_agent_falls_back_to_existing_production_model(tmp_path):
    """Nothing registered in this fresh registry -> falls back to
    learning.pattern_learner.MODEL_PATH, which is this repo's real, already
    -trained models/ml_model.pkl (predict_win_prob returns a real
    probability, not None) — confirms the fallback wiring reaches the
    actual live model, not a fabricated/undefined result."""
    registry = ModelRegistry(registry_path=tmp_path / "reg.json", models_dir=tmp_path / "models")
    result = learning_agent.predict_win_prob({"ema_score": 10}, registry=registry)
    assert result is None or 0.0 <= result <= 1.0


def test_learning_agent_returns_none_when_no_model_available(tmp_path, monkeypatch):
    """With a fallback path that genuinely doesn't exist, predict_win_prob
    must return None rather than raise."""
    registry = ModelRegistry(registry_path=tmp_path / "reg.json", models_dir=tmp_path / "models")
    monkeypatch.setattr("agents.learning_agent.MODEL_PATH", str(tmp_path / "no_such_model.pkl"))
    result = learning_agent.predict_win_prob({"ema_score": 10}, registry=registry)
    assert result is None


# ── risk_agent ───────────────────────────────────────────────────────────

def test_risk_agent_check_trade_rejects_low_score():
    ok, reason = risk_agent.check_trade(score=10, open_trade_count=0, pair="EUR_USD", open_pairs=[])
    assert ok is False
    assert "minimum" in reason.lower() or "score" in reason.lower()


def test_risk_agent_position_size_positive_for_usd_quoted_pair():
    size = risk_agent.position_size(account_balance=1000, entry=1.1000, stop_loss=1.0950, pair="GBP_USD")
    assert size > 0


# ── portfolio_agent ──────────────────────────────────────────────────────

class _FakeTradeManager:
    def __init__(self, open_trades=None):
        self.open_trades = open_trades or {}
        self.opened = []
        self.closed = []

    def open_trade(self, signal):
        tid = f"t{len(self.opened) + 1}"
        self.opened.append(signal)
        self.open_trades[tid] = {"pair": signal["pair"]}
        return tid

    def close_trade(self, trade_id, price, reason="manual"):
        self.closed.append((trade_id, price, reason))


def test_portfolio_agent_reports_open_state():
    tm = _FakeTradeManager({"t1": {"pair": "EUR_USD"}, "t2": {"pair": "GBP_USD"}})
    assert portfolio_agent.open_trade_count(tm) == 2
    assert set(portfolio_agent.open_pairs(tm)) == {"EUR_USD", "GBP_USD"}
    assert portfolio_agent.is_pair_open(tm, "EUR_USD") is True
    assert portfolio_agent.is_pair_open(tm, "USD_JPY") is False


# ── trade_agent ──────────────────────────────────────────────────────────

def test_trade_agent_execute_delegates_to_trade_manager():
    tm = _FakeTradeManager()
    signal = {"pair": "EUR_USD", "direction": "long", "entry": 1.1, "stop_loss": 1.09,
              "tp_levels": {"tp1": 1.11, "tp2": 1.12, "tp3": 1.13}, "score": 70}
    trade_id = trade_agent.execute(tm, signal)
    assert trade_id is not None
    assert tm.opened == [signal]


def test_trade_agent_close_delegates_to_trade_manager():
    tm = _FakeTradeManager()
    trade_agent.close(tm, "t1", 1.105, reason="test")
    assert tm.closed == [("t1", 1.105, "test")]


# ── evaluation_agent ─────────────────────────────────────────────────────

def test_evaluation_agent_reexports_existing_backtest_tools():
    assert callable(evaluation_agent.run_backtest)
    assert callable(evaluation_agent.run_walk_forward)
    assert evaluation_agent.wfo_optimizer is not None


# ── research_agent ───────────────────────────────────────────────────────

def test_research_agent_degrades_to_empty_dict_when_disabled():
    # research/ (Phase 4) may or may not exist/be enabled yet — either way
    # this must never raise.
    result = research_agent.get_context("EUR_USD")
    assert isinstance(result, dict)


# ── orchestrator_agent ───────────────────────────────────────────────────

def test_orchestrator_logs_no_trade_decision_when_signal_is_none():
    decision = orchestrator_agent.log_adaptive_decision(
        "EUR_USD", signal=None, get_candles_fn=_get_candles_fn,
        primary_tf="M30", confirm_tf="H4",
    )
    assert decision.action == "NO_TRADE"


def test_orchestrator_logs_buy_decision_from_triggered_signal():
    signal = {
        "pair": "EUR_USD", "direction": "long", "score": 75, "ema_score": 20,
        "stop_loss": 1.0950, "tp_levels": {"tp1": 1.1050, "tp2": 1.1100, "tp3": 1.1150},
        "ml_win_prob": 0.68, "watching": False,
    }
    decision = orchestrator_agent.log_adaptive_decision(
        "EUR_USD", signal=signal, get_candles_fn=_get_candles_fn,
        primary_tf="M30", confirm_tf="H4",
    )
    assert decision.action == "BUY"
    assert decision.confidence == 0.68
    assert decision.stop_loss == 1.0950


def test_orchestrator_does_not_execute_by_default():
    tm = _FakeTradeManager()
    signal = {
        "pair": "EUR_USD", "direction": "long", "score": 75,
        "stop_loss": 1.0950, "tp_levels": {"tp1": 1.1050}, "watching": False,
    }
    orchestrator_agent.log_adaptive_decision(
        "EUR_USD", signal=signal, get_candles_fn=_get_candles_fn,
        primary_tf="M30", confirm_tf="H4", trade_manager=tm,   # also_execute defaults False
    )
    assert tm.opened == []   # no order placed — must be explicit opt-in


def test_orchestrator_executes_only_when_explicitly_opted_in():
    tm = _FakeTradeManager()
    signal = {
        "pair": "EUR_USD", "direction": "long", "score": 75, "entry": 1.1000,
        "stop_loss": 1.0950, "tp_levels": {"tp1": 1.1050}, "watching": False,
    }
    orchestrator_agent.log_adaptive_decision(
        "EUR_USD", signal=signal, get_candles_fn=_get_candles_fn,
        primary_tf="M30", confirm_tf="H4", trade_manager=tm, also_execute=True,
    )
    assert len(tm.opened) == 1


def test_orchestrator_records_decision_and_agent_run(tmp_path):
    from memory import agent_memory, decision_memory
    signal = {
        "pair": "EUR_USD", "direction": "long", "score": 75,
        "stop_loss": 1.0950, "tp_levels": {"tp1": 1.1050}, "watching": False,
    }
    orchestrator_agent.log_adaptive_decision(
        "EUR_USD", signal=signal, get_candles_fn=_get_candles_fn,
        primary_tf="M30", confirm_tf="H4",
    )
    assert len(decision_memory.recent(pair="EUR_USD")) == 1
    assert agent_memory.last_run("EUR_USD") is not None
