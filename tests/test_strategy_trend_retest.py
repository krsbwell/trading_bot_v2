"""
engine/strategy_trend_retest.py — the H4-trend-gated break+retest+price-action
strategy built 2026-08-12 (see tasks/todo.md for the design discussion and
web-research validation).

Dependencies (detect_pivots/classify_structure/detect_bos_choch/detect_patterns/
ema/macd_full) are monkeypatched to isolate this module's own gating/scoring
logic — those helpers are shared with strategy_breakout_retest.py and
strategy_market_structure/strategy_price_action are exercised elsewhere
(tests/test_signals.py); no established real-OHLC-fixture pattern exists for
strategy modules in this repo (strategy_breakout_retest.py itself has no
dedicated test file either), so this follows the isolation-via-monkeypatch
approach used for the connector/trade-manager tests instead.
"""
import numpy as np
import pandas as pd
import pytest

import config
import engine.strategy_trend_retest as tr


def _df(n=60, hour=10):
    """Minimal OHLC frame long enough to pass the >=50-bar gate, timestamped
    inside the session window (04:00-17:00 UTC) by default."""
    idx = pd.date_range("2026-01-01", periods=n, freq="30min", tz="UTC")
    idx = idx[: n - 1].append(pd.DatetimeIndex([idx[-1].replace(hour=hour)]))
    close = np.linspace(1.1000, 1.1050, n)
    return pd.DataFrame({
        "open":  close, "high": close + 0.0005, "low": close - 0.0005,
        "close": close,
    }, index=idx)


@pytest.fixture(autouse=True)
def _clear():
    tr.clear_cache()
    yield
    tr.clear_cache()


@pytest.fixture
def patched(monkeypatch):
    """Everything wired to a clean 'setup fully qualifies' baseline —
    individual tests override one piece at a time to isolate each gate."""
    # Highs/lows both sit at the last close (1.1050, from _df()'s default
    # linspace) so the retest condition (c2) is unambiguously satisfied for
    # whichever direction a test selects via bos_dir, given a band wide
    # enough (atr=0.0030 below) to cover the +-0.0005 high/low wick offset.
    state = {
        "h4_bull": True,
        "bos_dir": "bullish",
        "highs": [1.1050], "lows": [1.1050],
        "patterns": ["bullish_pin_bar"],
        "macd_positive": True,
    }

    def _ema(series, period):
        # H4 bias: bull -> close above "ema", bear -> close below.
        val = series.iloc[-1] - 0.0010 if state["h4_bull"] else series.iloc[-1] + 0.0010
        return pd.Series([val] * len(series), index=series.index)

    def _detect_pivots(df):
        return {"highs": list(state["highs"]), "lows": list(state["lows"])}

    def _classify_structure(pivots):
        return {}

    def _detect_bos_choch(df, pivots, structure):
        return {"bos": state["bos_dir"] is not None, "bos_direction": state["bos_dir"]}

    def _detect_patterns(df):
        return list(state["patterns"])

    def _macd_full(close, fast, slow, signal):
        hist_val = 0.0001 if state["macd_positive"] else -0.0001
        s = pd.Series([hist_val] * len(close), index=close.index)
        return s, s, s

    def _atr(high, low, close, period):
        return pd.Series([0.0030] * len(high), index=high.index)

    monkeypatch.setattr(tr, "ema", _ema)
    monkeypatch.setattr(tr, "detect_pivots", _detect_pivots)
    monkeypatch.setattr(tr, "classify_structure", _classify_structure)
    monkeypatch.setattr(tr, "detect_bos_choch", _detect_bos_choch)
    monkeypatch.setattr(tr, "detect_patterns", _detect_patterns)
    monkeypatch.setattr(tr, "macd_full", _macd_full)
    monkeypatch.setattr(tr, "atr", _atr)
    return state


class TestSessionGate:
    def test_outside_session_window_no_signal(self, patched):
        df = _df(hour=2)   # before SESSION_START_UTC=4
        assert tr.check_buy_signal("EUR_USD", df, df) == 0.0
        assert tr.check_sell_signal("EUR_USD", df, df) == 0.0


class TestH4TrendHardGate:
    def test_bullish_setup_blocked_when_h4_bear(self, patched):
        patched["h4_bull"] = False
        df = _df()
        assert tr.check_buy_signal("EUR_USD", df, df) == 0.0
        diag = tr.get_last_diag("EUR_USD", "long")
        assert diag["h4_bias"] is False

    def test_bullish_setup_fires_when_h4_bull(self, patched):
        df = _df()
        score = tr.check_buy_signal("EUR_USD", df, df)
        assert score > 0.0
        diag = tr.get_last_diag("EUR_USD", "long")
        assert diag["c2"] and diag["c3"] and diag["c4"], (
            "full setup should satisfy all 3 required conditions, not just clear >0"
        )

    def test_bearish_setup_blocked_when_h4_bull(self, patched):
        patched["bos_dir"] = "bearish"
        df = _df()   # h4_bull defaults True
        assert tr.check_sell_signal("EUR_USD", df, df) == 0.0

    def test_bearish_setup_fires_when_h4_bear(self, patched):
        patched["h4_bull"] = False
        patched["bos_dir"] = "bearish"
        patched["patterns"] = ["bearish_pin_bar"]
        patched["macd_positive"] = False   # sell's MACD bonus needs hist < 0
        df = _df()
        score = tr.check_sell_signal("EUR_USD", df, df)
        assert score > 0.0
        diag = tr.get_last_diag("EUR_USD", "short")
        assert diag["c2"] and diag["c3"] and diag["c4"]


class TestH4BiasPeriodTunable:
    """H4 bias period is overridable via adaptive["h4_bias_period"] — added
    2026-08-12 so this strategy can get a real tuning pass instead of being
    judged on a hardcoded default. Default (untouched) behavior is covered
    by every other test in this file already."""

    def test_custom_period_is_read_from_adaptive_dict(self, patched, monkeypatch):
        seen_periods = []

        def _tracking_ema(series, period):
            seen_periods.append(period)
            val = series.iloc[-1] - 0.0010   # h4_bull=True behavior, same as fixture default
            return pd.Series([val] * len(series), index=series.index)

        monkeypatch.setattr(tr, "ema", _tracking_ema)
        # n=60 must stay >= the requested period, or _h4_bias's own length
        # guard returns "neutral" before ever calling ema() at all.
        df = _df(n=60)
        tr.check_buy_signal("EUR_USD", df, df, adaptive={"h4_bias_period": 55})
        assert 55 in seen_periods


class TestBosHardGate:
    def test_no_bos_no_signal_even_with_h4_aligned(self, patched):
        patched["bos_dir"] = None
        df = _df()
        assert tr.check_buy_signal("EUR_USD", df, df) == 0.0

    def test_wrong_direction_bos_no_signal(self, patched):
        patched["bos_dir"] = "bearish"   # h4_bull=True wants a bullish BOS
        df = _df()
        assert tr.check_buy_signal("EUR_USD", df, df) == 0.0


class TestMacdIsBonusNotGate:
    def test_full_setup_scores_without_macd_confluence(self, patched):
        """MACD must be optional — a signal with H4+BOS+retest+PA but no
        MACD confluence should still score, per the reference material's
        'indicators are optional, not implicitly needed' instruction."""
        patched["macd_positive"] = False
        df = _df()
        score_without = tr.check_buy_signal("EUR_USD", df, df)
        assert score_without > 0.0, "MACD must not be a hard gate"

        tr.clear_cache()
        patched["macd_positive"] = True
        score_with = tr.check_buy_signal("EUR_USD", df, df)
        assert score_with > score_without, "MACD alignment should still add a scoring bonus"


class TestGetTpLevelsStructure:
    def test_uses_next_pivot_beyond_min_r_floor(self, monkeypatch):
        def _detect_pivots(df):
            return {"highs": [1.1030, 1.1060, 1.1100], "lows": []}
        monkeypatch.setattr(tr, "detect_pivots", _detect_pivots)

        df = _df()
        levels = tr.get_tp_levels_structure(
            entry=1.1000, stop_loss=1.0980, direction="long", pair="EUR_USD", df_h1=df,
        )
        # dist = 0.0020, min_r floor = entry + 1.0*dist = 1.1020 — 1.1030 qualifies
        assert levels["tp1"] == 1.1030
        assert levels["tp2"] == 1.1060
        assert levels["tp3"] == 1.1100

    def test_falls_back_to_fixed_rr_when_no_pivot_qualifies(self, monkeypatch):
        def _detect_pivots(df):
            return {"highs": [], "lows": []}   # nothing beyond entry at all
        monkeypatch.setattr(tr, "detect_pivots", _detect_pivots)

        df = _df()
        levels = tr.get_tp_levels_structure(
            entry=1.1000, stop_loss=1.0980, direction="long", pair="EUR_USD", df_h1=df,
        )
        from risk.risk_manager import get_tp_levels as fixed_rr
        expected = fixed_rr(1.1000, 1.0980, "long", "EUR_USD")
        assert levels == expected

    def test_none_df_h1_falls_back_to_fixed_rr(self):
        levels = tr.get_tp_levels_structure(
            entry=1.1000, stop_loss=1.0980, direction="long", pair="EUR_USD", df_h1=None,
        )
        from risk.risk_manager import get_tp_levels as fixed_rr
        assert levels == fixed_rr(1.1000, 1.0980, "long", "EUR_USD")

    def test_short_direction_uses_lows_descending(self, monkeypatch):
        def _detect_pivots(df):
            return {"highs": [], "lows": [1.0970, 1.0940, 1.0900]}
        monkeypatch.setattr(tr, "detect_pivots", _detect_pivots)

        df = _df()
        levels = tr.get_tp_levels_structure(
            entry=1.1000, stop_loss=1.1020, direction="short", pair="EUR_USD", df_h1=df,
        )
        assert levels["tp1"] == 1.0970
        assert levels["tp2"] == 1.0940
        assert levels["tp3"] == 1.0900


class TestGetStopLoss:
    def test_long_sl_below_entry(self, monkeypatch):
        def _detect_pivots(df):
            return {"highs": [1.1010], "lows": []}
        monkeypatch.setattr(tr, "detect_pivots", _detect_pivots)
        monkeypatch.setattr(tr, "atr", lambda h, l, c, p: pd.Series([0.0010] * len(h), index=h.index))
        df = _df()
        sl = tr.get_stop_loss("EUR_USD", df, "long")
        assert sl < float(df["close"].iloc[-1])

    def test_short_sl_above_entry(self, monkeypatch):
        def _detect_pivots(df):
            return {"highs": [], "lows": [1.0990]}
        monkeypatch.setattr(tr, "detect_pivots", _detect_pivots)
        monkeypatch.setattr(tr, "atr", lambda h, l, c, p: pd.Series([0.0010] * len(h), index=h.index))
        df = _df()
        sl = tr.get_stop_loss("EUR_USD", df, "short")
        assert sl > float(df["close"].iloc[-1])
