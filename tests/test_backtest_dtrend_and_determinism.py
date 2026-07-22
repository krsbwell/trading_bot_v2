"""
Tests for two related backtest/runner.py fixes made 2026-07-22:

1. run_backtest() previously left d_trend hardcoded to "neutral" for every
   trade — it never received Daily-candle data. Added an optional df_d
   param; when supplied, d_trend is computed the same way
   engine/signal_engine.py does live (close vs EMA20 on the last fully
   closed daily candle, no lookahead).
2. Found while verifying (1): repeated run_backtest() calls for the same
   pair in one process produced different trade counts/PnL for byte-
   identical arguments, because engine/strategy_ema_cci_macd.py's EMA
   auto-fit cache isn't cleared between calls — a real problem since
   run_walk_forward() and WFO's grid search both call run_backtest() many
   times for the same pair by design. Fixed by clearing all three
   strategy modules' caches at the top of every run_backtest() call.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import pytest

import backtest.runner as bt
import engine.strategy_ema_cci_macd as ema_strat


def _make_df(n=150, start=1.0800, trend=0.0, seed=7):
    rng = np.random.default_rng(seed)
    rows, price = [], start
    for _ in range(n):
        price += trend + rng.uniform(-0.0003, 0.0003)
        rows.append({
            "open": price, "high": price + 0.0006,
            "low": price - 0.0006, "close": price, "volume": 1000,
        })
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2024-01-01 08:00", periods=n, freq="30min")
    return df


def _make_daily_df(n=40, start=1.05, trend=0.0):
    """Daily candles ending the day BEFORE the H1 data's first bar (2024-01-01),
    so every H1 bar in _make_df() has fully-closed daily history available."""
    rows, price = [], start
    for _ in range(n):
        price += trend
        rows.append({"open": price, "high": price + 0.001, "low": price - 0.001, "close": price})
    df = pd.DataFrame(rows)
    df.index = pd.date_range(end="2023-12-31", periods=n, freq="1D")
    return df


def _stub_buy(pair, df_h1, df_h4, adaptive=None):
    return 20.0


def _stub_sell(pair, df_h1, df_h4, adaptive=None):
    return 0.0


def _stub_sl(pair, df_h1, direction):
    return float(df_h1["close"].iloc[-1]) - 0.0050


@pytest.fixture(autouse=True)
def _clear_caches_before_each_test():
    ema_strat.clear_cache()
    yield
    ema_strat.clear_cache()


class TestDeterminism:
    def test_repeated_calls_with_identical_args_produce_identical_results(self):
        """This is the actual bug found 2026-07-22: same pair backtested twice
        in one process (e.g. a param sweep) silently diverged because the EMA
        auto-fit cache from the first call leaked into the second."""
        df_h1 = _make_df(300)
        df_h4 = _make_df(160)

        first  = bt.run_backtest("EUR_USD", df_h1, df_h4)
        second = bt.run_backtest("EUR_USD", df_h1, df_h4)

        assert first["total_trades"] == second["total_trades"]
        assert first["total_pnl"]    == second["total_pnl"]
        assert first["win_rate"]     == second["win_rate"]

    def test_repeated_calls_with_different_pairs_dont_cross_contaminate(self):
        df_h1 = _make_df(300)
        df_h4 = _make_df(160)

        bt.run_backtest("EUR_USD", df_h1, df_h4)
        # A different pair run in between shouldn't change EUR_USD's own result.
        bt.run_backtest("GBP_USD", df_h1, df_h4)
        third = bt.run_backtest("EUR_USD", df_h1, df_h4)
        baseline = bt.run_backtest("EUR_USD", df_h1, df_h4)

        assert third["total_trades"] == baseline["total_trades"]
        assert third["total_pnl"]    == baseline["total_pnl"]


class TestDTrend:
    def test_d_trend_stays_neutral_without_df_d(self):
        df_h1 = _make_df(150)
        df_h4 = _make_df(80)
        res = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
        )
        sigs = [t["_sig"] for t in res["trades"] if t.get("_sig")]
        assert sigs
        assert all(s["d_trend"] == "neutral" for s in sigs)

    def test_d_trend_bull_when_daily_uptrending(self):
        df_h1 = _make_df(150)
        df_h4 = _make_df(80)
        df_d  = _make_daily_df(40, start=1.00, trend=0.003)  # clear uptrend
        res = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
            df_d=df_d,
        )
        sigs = [t["_sig"] for t in res["trades"] if t.get("_sig")]
        assert sigs
        assert all(s["d_trend"] == "bull" for s in sigs)

    def test_d_trend_bear_when_daily_downtrending(self):
        df_h1 = _make_df(150)
        df_h4 = _make_df(80)
        df_d  = _make_daily_df(40, start=1.20, trend=-0.003)  # clear downtrend
        res = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
            df_d=df_d,
        )
        sigs = [t["_sig"] for t in res["trades"] if t.get("_sig")]
        assert sigs
        assert all(s["d_trend"] == "bear" for s in sigs)

    def test_df_d_does_not_change_which_trades_are_taken(self):
        """d_trend is a logged/display feature only — it must never affect
        which signals fire or their outcomes, only what gets recorded."""
        df_h1 = _make_df(150)
        df_h4 = _make_df(80)
        df_d  = _make_daily_df(40, start=1.00, trend=0.003)

        without = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
        )
        with_d = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
            df_d=df_d,
        )
        assert without["total_trades"] == with_d["total_trades"]
        assert without["total_pnl"]    == with_d["total_pnl"]

    def test_short_or_missing_df_d_falls_back_to_neutral_without_crashing(self):
        df_h1 = _make_df(150)
        df_h4 = _make_df(80)
        tiny_df_d = _make_daily_df(5)   # below the 20-bar EMA minimum
        res = bt.run_backtest(
            "EUR_USD", df_h1, df_h4, min_score=1,
            buy_fn=_stub_buy, sell_fn=_stub_sell, stop_loss_fn=_stub_sl,
            df_d=tiny_df_d,
        )
        sigs = [t["_sig"] for t in res["trades"] if t.get("_sig")]
        assert sigs
        assert all(s["d_trend"] == "neutral" for s in sigs)
