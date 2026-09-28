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
        sig   = res["signal_log"][0]   # TP levels come from the SIGNAL price; the trade's
                                       # entry is the spread-adjusted fill (2026-09-28)
        expected = get_tp_levels(sig["entry"], first["sl"], first["direction"], "EUR_USD")
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



def test_spread_model_makes_backtest_pay_the_spread(monkeypatch):
    """2026-09-28: the runner used to fill at the signal price (zero spread),
    overstating every backtest. With the model on, the long fill must sit
    exactly one spread above the signal price and P&L must be lower."""
    import config
    df = _make_df()
    kw = dict(buy_fn=_stub_buy_always, sell_fn=_stub_sell_never, stop_loss_fn=_stub_sl)
    monkeypatch.setattr(config, "BACKTEST_SPREAD_ENABLED", False)
    ema_strat.clear_cache()
    free = bt.run_backtest("EUR_USD", df, df, **kw)
    monkeypatch.setattr(config, "BACKTEST_SPREAD_ENABLED", True)
    ema_strat.clear_cache()
    paid = bt.run_backtest("EUR_USD", df, df, **kw)
    sig, trade = paid["signal_log"][0], paid["trades"][0]
    assert abs(trade["entry"] - (sig["entry"] + config.spread_pips_for("EUR_USD") * 0.0001)) < 1e-9
    assert paid["total_pnl"] < free["total_pnl"]


def test_backtest_never_sees_unclosed_confirm_candle():
    """2026-09-28 look-ahead fix: at each primary bar's close, every H4
    candle handed to the strategy must already have CLOSED, and the primary
    slice must be the live-sized window."""
    import pandas as pd, numpy as np, config
    idx = pd.date_range("2026-01-05", periods=400, freq="30min")
    px = 1.08 + np.cumsum(np.random.default_rng(1).normal(0, 1e-4, len(idx)))
    m30 = pd.DataFrame({"open": px, "high": px + 2e-4, "low": px - 2e-4,
                        "close": px, "volume": 100}, index=idx)
    h4 = m30.resample("4h").agg({"open": "first", "high": "max", "low": "min",
                                 "close": "last", "volume": "sum"}).dropna()
    seen = []
    def spy(pair, df_h1, df_h4, adaptive=None):
        seen.append((df_h1.index[-1], len(df_h1), df_h4.index[-1]))
        return 0.0
    bt.run_backtest("EUR_USD", m30, h4, buy_fn=spy, sell_fn=spy, stop_loss_fn=_stub_sl)
    assert seen
    for bar_open, n, h4_open in seen:
        decided_at = bar_open + pd.Timedelta(minutes=30)
        assert h4_open + pd.Timedelta(hours=4) <= decided_at
        assert n <= config.LIVE_CANDLE_WINDOW


def test_retest_since_break_accepts_earlier_pullback_not_break_candle():
    import pandas as pd
    from engine.strategy_breakout_retest import retest_since_break
    lvl, band = 1.1000, 0.0005
    def df(rows):
        return pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                            index=pd.date_range("2026-01-05", periods=len(rows), freq="30min"))
    # break on candle 2, pullback to the level on candle 3, confirming candle 4 away from it
    ok = df([(1.098, 1.0995, 1.097, 1.0990), (1.099, 1.0998, 1.0985, 1.0995),
             (1.0995, 1.1030, 1.0994, 1.1025), (1.1025, 1.1028, 1.1002, 1.1010),
             (1.1010, 1.1040, 1.1008, 1.1035)])
    assert retest_since_break(ok, lvl, band, "long") is True
    # only the break candle's wick is near the level — not a retest
    no = df([(1.098, 1.0995, 1.097, 1.0990), (1.099, 1.0998, 1.0985, 1.0995),
             (1.0995, 1.1030, 1.0997, 1.1025), (1.1025, 1.1050, 1.1020, 1.1045),
             (1.1045, 1.1060, 1.1040, 1.1055)])
    assert retest_since_break(no, lvl, band, "long") is False


def test_addon_ma_gate_only_when_enabled(monkeypatch):
    import numpy as np, pandas as pd, config
    from engine.strategy_breakout_retest import addon_ok
    falling = pd.DataFrame({"close": np.linspace(1.2, 1.0, 300)},
                           index=pd.date_range("2026-01-05", periods=300, freq="30min"))
    monkeypatch.setattr(config, "PYRAMID_REQUIRE_MA_CROSS", False)
    assert addon_ok(falling, "long") is True
    monkeypatch.setattr(config, "PYRAMID_REQUIRE_MA_CROSS", True)
    assert addon_ok(falling, "long") is False
    assert addon_ok(falling, "short") is True
