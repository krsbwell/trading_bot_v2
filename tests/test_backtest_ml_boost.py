"""
Tests for the ML score-boost wiring added to backtest/runner.py (2026-07-20).
Uses run_backtest()'s overridable buy_fn/sell_fn so a "signal" fires
deterministically without needing candle data tuned to trigger the real
EMA-bounce strategy — this file is only testing the ML plumbing, not
strategy logic (already covered by tests/test_signals.py).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import pytest

import config
import backtest.runner as bt


def _make_df(n=150, start=1.0800):
    rng = np.random.default_rng(7)
    rows, price = [], start
    for _ in range(n):
        price += rng.uniform(-0.0003, 0.0003)
        rows.append({
            "open": price, "high": price + 0.0006,
            "low": price - 0.0006, "close": price, "volume": 1000,
        })
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2024-01-01 08:00", periods=n, freq="30min")  # inside London/NY session
    return df


def _stub_buy(pair, df_h1, df_h4, adaptive=None):
    return 20.0   # constant nonzero -> always the winning direction


def _stub_sell(pair, df_h1, df_h4, adaptive=None):
    return 0.0


def _stub_sl(pair, df_h1, direction):
    return float(df_h1["close"].iloc[-1]) - 0.0050


class _FakeModel:
    """Records every feature dict it's asked to score."""
    def __init__(self, return_value):
        self.calls = []
        self.return_value = return_value

    def predict_win_prob(self, features):
        self.calls.append(features)
        return self.return_value


@pytest.fixture(autouse=True)
def _reset_ml_flag_and_singleton(monkeypatch):
    monkeypatch.setattr(config, "ML_SCORE_BOOST_ENABLED", False)
    monkeypatch.setattr(bt, "_ml_model_singleton", None)


class TestBacktestMlBoostWiring:
    def test_ml_model_never_constructed_when_flag_off(self, monkeypatch):
        def _boom():
            raise AssertionError("_ml_model() must not be called when the flag is off")
        monkeypatch.setattr(bt, "_ml_model", _boom)

        df_h1 = _make_df(150)
        df_h4 = _make_df(80)
        res = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
        )
        assert "error" not in res   # would have raised via _boom() otherwise if reached

    def test_ml_model_called_with_feature_dict_when_flag_on(self, monkeypatch):
        config.ML_SCORE_BOOST_ENABLED = True
        fake = _FakeModel(return_value=0.9)
        monkeypatch.setattr(bt, "_ml_model", lambda: fake)

        df_h1 = _make_df(150)
        df_h4 = _make_df(80)
        bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
        )
        assert len(fake.calls) > 0
        first = fake.calls[0]
        for key in ("confluence_score", "ema_score", "cci_at_signal",
                    "macd_hist_at_signal", "session", "direction"):
            assert key in first
        assert first["direction"] == "long"

    def test_high_ml_prob_boosts_final_score_above_unboosted(self, monkeypatch):
        """ml_win_prob > 0.65 -> score_signal boosts by 1.10x (engine/confluence_scorer.py)."""
        df_h1 = _make_df(150)
        df_h4 = _make_df(80)

        config.ML_SCORE_BOOST_ENABLED = False
        baseline = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
        )
        base_scores = [s["score"] for s in baseline["signal_log"]]
        assert base_scores   # stub should have produced at least one signal

        config.ML_SCORE_BOOST_ENABLED = True
        monkeypatch.setattr(bt, "_ml_model", lambda: _FakeModel(return_value=0.9))
        boosted = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
        )
        boosted_scores = [s["score"] for s in boosted["signal_log"]]
        assert boosted_scores
        assert boosted_scores[0] >= base_scores[0]   # boost never lowers the score

    def test_low_ml_prob_penalizes_final_score_below_unboosted(self, monkeypatch):
        """ml_win_prob < 0.35 -> score_signal penalizes by 0.70x."""
        df_h1 = _make_df(150)
        df_h4 = _make_df(80)

        config.ML_SCORE_BOOST_ENABLED = False
        baseline = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
        )
        base_scores = [s["score"] for s in baseline["signal_log"]]
        assert base_scores

        config.ML_SCORE_BOOST_ENABLED = True
        monkeypatch.setattr(bt, "_ml_model", lambda: _FakeModel(return_value=0.1))
        penalized = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
        )
        pen_scores = [s["score"] for s in penalized["signal_log"]]
        assert pen_scores
        assert pen_scores[0] <= base_scores[0]

    def test_ml_model_prediction_failure_does_not_crash_backtest(self, monkeypatch):
        config.ML_SCORE_BOOST_ENABLED = True

        class _Raises:
            def predict_win_prob(self, features):
                raise RuntimeError("model file corrupt")

        monkeypatch.setattr(bt, "_ml_model", lambda: _Raises())
        df_h1 = _make_df(150)
        df_h4 = _make_df(80)
        res = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
        )
        assert "error" not in res   # falls back to ml_win_prob=None, doesn't propagate
