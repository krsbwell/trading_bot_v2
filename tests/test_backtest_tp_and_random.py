"""
Tests for two backtest/runner.py additions made 2026-08-12 alongside
engine/strategy_trend_retest.py (see tasks/todo.md):

1. run_backtest()'s new optional tp_fn param — lets a backtest use a
   different take-profit calculation (e.g. structure-based targets) instead
   of the default risk_manager.get_tp_levels, so the new strategy's TP
   approach could actually be tested rather than assumed.
2. random_signal_fns() — a control strategy (random direction/timing, same
   session/SL/TP mechanics) for isolating whether a real strategy's entry
   logic carries information beyond its risk/exit rules alone.
"""
import numpy as np
import pandas as pd
import pytest

import backtest.runner as bt
import engine.strategy_ema_cci_macd as ema_strat


def _make_df(n=150, start=1.0800, seed=7):
    rng = np.random.default_rng(seed)
    rows, price = [], start
    for _ in range(n):
        price += rng.uniform(-0.0003, 0.0003)
        rows.append({"open": price, "high": price + 0.0006,
                     "low": price - 0.0006, "close": price})
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2024-01-01 08:00", periods=n, freq="30min")
    return df


def _stub_buy_always(pair, df_h1, df_h4, adaptive=None):
    return 60.0


def _stub_sell_never(pair, df_h1, df_h4, adaptive=None):
    return 0.0


def _stub_sl(pair, df_h1, direction):
    return float(df_h1["close"].iloc[-1]) - 0.0050


@pytest.fixture(autouse=True)
def _clear_caches():
    ema_strat.clear_cache()
    yield
    ema_strat.clear_cache()


class TestTpFnPlumbing:
    def test_default_tp_fn_matches_risk_manager(self):
        df = _make_df()
        res = bt.run_backtest("EUR_USD", df, df, buy_fn=_stub_buy_always,
                              sell_fn=_stub_sell_never, stop_loss_fn=_stub_sl)
        assert res["total_trades"] >= 1
        from risk.risk_manager import get_tp_levels
        first = res["trades"][0]
        expected = get_tp_levels(first["entry"], first["sl"], first["direction"], "EUR_USD")
        assert first["tp1"] == expected["tp1"]

    def test_custom_tp_fn_overrides_default(self):
        def _flat_tp(entry, stop_loss, direction, pair, df_h1):
            dist = abs(entry - stop_loss)
            mult = 1 if direction == "long" else -1
            # Deliberately different ratios from the default 1.5/2.5/3.5R,
            # so a passing test proves the custom fn was actually used.
            return {"tp1": entry + mult * dist * 9.0,
                    "tp2": entry + mult * dist * 9.5,
                    "tp3": entry + mult * dist * 10.0}

        df = _make_df()
        res = bt.run_backtest("EUR_USD", df, df, buy_fn=_stub_buy_always,
                              sell_fn=_stub_sell_never, stop_loss_fn=_stub_sl,
                              tp_fn=_flat_tp)
        assert res["total_trades"] >= 1
        from risk.risk_manager import get_tp_levels
        first = res["trades"][0]
        default = get_tp_levels(first["entry"], first["sl"], first["direction"], "EUR_USD")
        assert first["tp1"] != default["tp1"]

    def test_trend_retest_structure_tp_is_wireable(self):
        """The actual function this was built for — smoke-test it doesn't
        blow up wired through run_backtest end-to-end, not just in isolation."""
        from engine.strategy_trend_retest import get_tp_levels_structure
        df = _make_df()
        res = bt.run_backtest("EUR_USD", df, df, buy_fn=_stub_buy_always,
                              sell_fn=_stub_sell_never, stop_loss_fn=_stub_sl,
                              tp_fn=get_tp_levels_structure)
        assert "error" not in res


class TestRandomSignalFns:
    def test_zero_fire_rate_never_trades(self):
        buy_fn, sell_fn = bt.random_signal_fns(fire_rate=0.0)
        df = _make_df()
        for i in range(50, len(df)):
            slice_h1 = df.iloc[:i + 1]
            assert buy_fn("EUR_USD", slice_h1, slice_h1) == 0.0
            assert sell_fn("EUR_USD", slice_h1, slice_h1) == 0.0

    def test_full_fire_rate_always_fires_exactly_one_direction(self):
        buy_fn, sell_fn = bt.random_signal_fns(fire_rate=1.0)
        df = _make_df()
        for i in range(50, len(df)):
            slice_h1 = df.iloc[:i + 1]
            b = buy_fn("EUR_USD", slice_h1, slice_h1)
            s = sell_fn("EUR_USD", slice_h1, slice_h1)
            assert (b > 0) != (s > 0), "exactly one direction should fire per bar at fire_rate=1.0"

    def test_deterministic_given_same_seed(self):
        df = _make_df()
        buy1, sell1 = bt.random_signal_fns(fire_rate=0.5, seed=99)
        buy2, sell2 = bt.random_signal_fns(fire_rate=0.5, seed=99)
        for i in range(50, min(70, len(df))):
            slice_h1 = df.iloc[:i + 1]
            assert buy1("EUR_USD", slice_h1, slice_h1) == buy2("EUR_USD", slice_h1, slice_h1)
            assert sell1("EUR_USD", slice_h1, slice_h1) == sell2("EUR_USD", slice_h1, slice_h1)

    def test_wireable_end_to_end_through_run_backtest(self):
        buy_fn, sell_fn = bt.random_signal_fns(fire_rate=0.3, seed=1)
        df = _make_df()
        res = bt.run_backtest("EUR_USD", df, df, buy_fn=buy_fn, sell_fn=sell_fn,
                              stop_loss_fn=_stub_sl)
        assert "error" not in res
