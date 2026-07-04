"""
Signal engine tests — all use synthetic DataFrames, no live API calls.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import pytest

from engine.confluence_scorer import score_signal
from engine.strategy_price_action import detect_patterns, score_price_action
from engine.strategy_market_structure import (
    detect_pivots, classify_structure, detect_bos_choch, get_sr_zones, score_structure,
)
from engine.strategy_ema_cci_macd import (
    get_best_emas, check_buy_signal, check_sell_signal, clear_cache,
)
from engine.indicators import ema, atr, cci, macd_histogram


# ── Synthetic data helpers ─────────────────────────────────────────────────────

def _make_candle(open_, high, low, close, volume=1000) -> dict:
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def _make_df(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df.index = pd.date_range("2024-01-01", periods=len(df), freq="h")
    return df


def _trending_up(n=200, start=1.08, step=0.0002, noise=0.0001) -> pd.DataFrame:
    """DataFrame trending up — price above all EMAs."""
    rng = np.random.default_rng(42)
    rows = []
    price = start
    for _ in range(n):
        price += step + rng.uniform(-noise, noise)
        rows.append(_make_candle(price - 0.0003, price + 0.0005, price - 0.0006, price))
    return _make_df(rows)


def _trending_down(n=200, start=1.10, step=0.0002, noise=0.0001) -> pd.DataFrame:
    """DataFrame trending down — price below all EMAs."""
    rng = np.random.default_rng(42)
    rows = []
    price = start
    for _ in range(n):
        price -= step + rng.uniform(-noise, noise)
        rows.append(_make_candle(price + 0.0003, price + 0.0006, price - 0.0005, price))
    return _make_df(rows)


def _flat(n=200, price=1.08) -> pd.DataFrame:
    rows = [_make_candle(price, price + 0.0002, price - 0.0002, price) for _ in range(n)]
    return _make_df(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# Indicators
# ═══════════════════════════════════════════════════════════════════════════════

class TestIndicators:
    def test_ema_converges(self):
        s = pd.Series([1.0] * 100)
        result = ema(s, 20)
        assert abs(result.iloc[-1] - 1.0) < 1e-10

    def test_atr_positive(self):
        df = _trending_up(50)
        a = atr(df["high"], df["low"], df["close"], 14)
        assert a.dropna().gt(0).all()

    def test_cci_positive_on_uptrend(self):
        """CCI stays mostly positive on a clean uptrend (price above its SMA)."""
        df = _trending_up(100)
        c = cci(df["high"], df["low"], df["close"], 20).dropna()
        assert (c > 0).mean() > 0.7  # at least 70 % of values positive

    def test_cci_crosses_zero_on_pullback(self):
        """Series with a mid-section pullback should produce negative CCI."""
        rng = np.random.default_rng(0)
        prices = np.concatenate([
            np.linspace(1.08, 1.10, 40),   # uptrend
            np.linspace(1.10, 1.07, 30),   # sharp pullback
            np.linspace(1.07, 1.09, 30),   # recovery
        ])
        prices += rng.uniform(-0.0002, 0.0002, len(prices))
        candles = [_make_candle(p, p + 0.0005, p - 0.0005, p) for p in prices]
        df = _make_df(candles)
        c = cci(df["high"], df["low"], df["close"], 20).dropna()
        assert (c > 0).any() and (c < 0).any()

    def test_macd_histogram_shape(self):
        df = _trending_up(100)
        h = macd_histogram(df["close"])
        assert len(h) == len(df)
        assert not h.dropna().empty


# ═══════════════════════════════════════════════════════════════════════════════
# Confluence scorer
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfluenceScorer:
    def test_full_score_no_ml(self):
        assert score_signal(25, 45, 30) == 100

    def test_partial_score(self):
        s = score_signal(10, 20, 10)
        assert 0 <= s <= 100

    def test_ml_boost_applied(self):
        base = score_signal(25, 35, 20)
        boosted = score_signal(25, 35, 20, ml_win_prob=0.70)
        assert boosted >= base

    def test_ml_suppress_reduces_score(self):
        # Suppression is now a soft 0.70× penalty (not hard zero) for ml_win_prob < 0.35
        base       = score_signal(25, 35, 20)
        suppressed = score_signal(25, 35, 20, ml_win_prob=0.20)
        assert suppressed < base

    def test_ml_boost_capped_at_100(self):
        assert score_signal(25, 45, 30, ml_win_prob=0.80) == 100


# ═══════════════════════════════════════════════════════════════════════════════
# Price Action patterns
# ═══════════════════════════════════════════════════════════════════════════════

class TestPriceActionPatterns:
    # ── Single-candle ──────────────────────────────────────────────────────────

    def _df(self, *candles):
        return _make_df([_make_candle(*c) for c in candles])

    def test_bullish_pin_bar(self):
        # Long lower wick, close in top 30%
        # range 0→100, body 80→90, lower wick 0→80, upper wick 90→100
        df = self._df(
            (1.0820, 1.0830, 1.0800, 1.0826),  # c2 (filler)
            (1.0820, 1.0830, 1.0800, 1.0826),  # c1 (filler)
            (1.0820, 1.0830, 1.0780, 1.0828),  # c0: low=1.0780, close near high
        )
        assert "bullish_pin_bar" in detect_patterns(df)

    def test_bearish_pin_bar(self):
        # Long upper wick, close in bottom 30%
        df = self._df(
            (1.0820, 1.0830, 1.0800, 1.0826),
            (1.0820, 1.0830, 1.0800, 1.0826),
            (1.0820, 1.0860, 1.0810, 1.0812),  # high far above, close near low
        )
        assert "bearish_pin_bar" in detect_patterns(df)

    def test_bullish_engulfing(self):
        # c0 bullish body contains c1 bearish body
        df = self._df(
            (1.0820, 1.0830, 1.0800, 1.0826),  # c2
            (1.0825, 1.0830, 1.0810, 1.0812),  # c1 bearish: open 1.0825 close 1.0812
            (1.0810, 1.0840, 1.0808, 1.0830),  # c0 bullish: open 1.0810 close 1.0830 → contains c1
        )
        assert "bullish_engulfing" in detect_patterns(df)

    def test_bearish_engulfing(self):
        # c0 bearish body contains c1 bullish body
        df = self._df(
            (1.0820, 1.0830, 1.0800, 1.0826),  # c2
            (1.0810, 1.0830, 1.0808, 1.0825),  # c1 bullish
            (1.0828, 1.0832, 1.0805, 1.0808),  # c0 bearish: open > c1.close, close < c1.open
        )
        assert "bearish_engulfing" in detect_patterns(df)

    def test_hammer(self):
        # Small body, long lower wick, tiny upper wick
        df = self._df(
            (1.0820, 1.0830, 1.0800, 1.0826),
            (1.0820, 1.0830, 1.0800, 1.0826),
            (1.0822, 1.0823, 1.0800, 1.0821),  # body 0.0001, lower_wick 0.0021, upper_wick 0.0002
        )
        assert "hammer" in detect_patterns(df)

    def test_shooting_star(self):
        # Small body, long upper wick, tiny lower wick
        df = self._df(
            (1.0820, 1.0830, 1.0800, 1.0826),
            (1.0820, 1.0830, 1.0800, 1.0826),
            (1.0820, 1.0842, 1.0819, 1.0821),  # upper_wick 0.0021, body 0.0001
        )
        assert "shooting_star" in detect_patterns(df)

    def test_bullish_marubozu(self):
        # Full bullish candle, no wicks
        df = self._df(
            (1.0820, 1.0830, 1.0800, 1.0826),
            (1.0820, 1.0830, 1.0800, 1.0826),
            (1.0810, 1.0830, 1.0810, 1.0830),  # open == low, close == high
        )
        assert "bullish_marubozu" in detect_patterns(df)

    def test_bearish_marubozu(self):
        df = self._df(
            (1.0820, 1.0830, 1.0800, 1.0826),
            (1.0820, 1.0830, 1.0800, 1.0826),
            (1.0830, 1.0830, 1.0810, 1.0810),  # open == high, close == low
        )
        assert "bearish_marubozu" in detect_patterns(df)

    def test_morning_star(self):
        df = self._df(
            (1.0830, 1.0835, 1.0820, 1.0820),  # c2 bearish
            (1.0820, 1.0822, 1.0818, 1.0820),  # c1 doji
            (1.0820, 1.0845, 1.0818, 1.0840),  # c0 bullish, closes above c2 midpoint
        )
        assert "morning_star" in detect_patterns(df)

    def test_evening_star(self):
        df = self._df(
            (1.0810, 1.0835, 1.0808, 1.0830),  # c2 bullish
            (1.0830, 1.0832, 1.0828, 1.0830),  # c1 doji
            (1.0828, 1.0832, 1.0808, 1.0812),  # c0 bearish, closes below c2 midpoint
        )
        assert "evening_star" in detect_patterns(df)

    # ── Scoring ────────────────────────────────────────────────────────────────

    def test_one_confirming_scores_20(self):
        assert score_price_action(["bullish_pin_bar"], "long") == 20.0

    def test_two_confirming_scores_30(self):
        assert score_price_action(["bullish_pin_bar", "hammer"], "long") == 30.0

    def test_more_than_two_capped_at_30(self):
        assert score_price_action(["bullish_pin_bar", "hammer", "bullish_marubozu"], "long") == 30.0

    def test_contradicting_penalty(self):
        # One confirming (20) + one contradicting (-10) = 10
        assert score_price_action(["bullish_pin_bar", "bearish_pin_bar"], "long") == 10.0

    def test_only_contradicting(self):
        assert score_price_action(["bearish_pin_bar"], "long") == -10.0

    def test_empty_patterns_zero(self):
        assert score_price_action([], "long") == 0.0

    def test_direction_flips_correctly(self):
        # bearish_pin_bar confirms a short
        assert score_price_action(["bearish_pin_bar"], "short") == 20.0


# ═══════════════════════════════════════════════════════════════════════════════
# Market Structure
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarketStructure:
    def _hh_hl_df(self) -> pd.DataFrame:
        """DataFrame with clear Higher Highs / Higher Lows."""
        candles = []
        base = 1.08
        for i in range(100):
            # Alternating peaks and troughs, each one higher than the last
            h = base + (i * 0.0004) + 0.0010
            l = base + (i * 0.0004) - 0.0005
            candles.append(_make_candle(base + i * 0.0004, h, l, base + i * 0.0004 + 0.0002))
        return _make_df(candles)

    def _ll_lh_df(self) -> pd.DataFrame:
        """DataFrame with clear Lower Lows / Lower Highs."""
        candles = []
        base = 1.12
        for i in range(100):
            h = base - (i * 0.0004) + 0.0010
            l = base - (i * 0.0004) - 0.0005
            candles.append(_make_candle(base - i * 0.0004, h, l, base - i * 0.0004 - 0.0002))
        return _make_df(candles)

    def test_detect_pivots_returns_dict(self):
        df = self._hh_hl_df()
        p = detect_pivots(df)
        assert "highs" in p and "lows" in p

    def test_detect_pivots_max_6_each(self):
        df = self._hh_hl_df()
        p = detect_pivots(df)
        assert len(p["highs"]) <= 6
        assert len(p["lows"])  <= 6

    def test_classify_bullish(self):
        # Strictly increasing highs and lows
        pivots = {"highs": [1.08, 1.09, 1.10], "lows": [1.07, 1.08, 1.09]}
        assert classify_structure(pivots) == "bullish"

    def test_classify_bearish(self):
        pivots = {"highs": [1.10, 1.09, 1.08], "lows": [1.09, 1.08, 1.07]}
        assert classify_structure(pivots) == "bearish"

    def test_classify_ranging(self):
        pivots = {"highs": [1.09, 1.10, 1.09], "lows": [1.07, 1.08, 1.07]}
        assert classify_structure(pivots) == "ranging"

    def test_classify_too_few_pivots(self):
        assert classify_structure({"highs": [1.09], "lows": [1.07]}) == "ranging"

    def test_bos_bullish_detected(self):
        # Build df where recent candles close above the swing high
        pivots = {"highs": [1.0900], "lows": [1.0800]}
        candles = [_make_candle(1.0910, 1.0920, 1.0905, 1.0915)] * 5
        df = _make_df(candles)
        result = detect_bos_choch(df, pivots, "bullish")
        assert result["bos"] is True
        assert result["bos_direction"] == "bullish"

    def test_bos_bearish_detected(self):
        pivots = {"highs": [1.0900], "lows": [1.0800]}
        candles = [_make_candle(1.0785, 1.0790, 1.0780, 1.0785)] * 5
        df = _make_df(candles)
        result = detect_bos_choch(df, pivots, "bullish")
        assert result["bos"] is True
        assert result["bos_direction"] == "bearish"
        assert result["choch"] is True   # against bullish structure → ChoCH

    def test_no_bos_when_inside(self):
        pivots = {"highs": [1.0950], "lows": [1.0750]}
        candles = [_make_candle(1.0850, 1.0860, 1.0840, 1.0855)] * 5
        df = _make_df(candles)
        result = detect_bos_choch(df, pivots, "bullish")
        assert result["bos"] is False

    def test_sr_zones_structure(self):
        df = self._hh_hl_df()
        pivots = detect_pivots(df)
        zones = get_sr_zones(df, pivots)
        assert isinstance(zones, list)
        for z in zones:
            assert {"price", "upper", "lower", "type", "touches", "tested"} <= z.keys()
            assert z["upper"] > z["lower"]

    def test_score_structure_max(self):
        # All three bonuses
        score = score_structure(
            structure="bullish",
            signal_direction="long",
            entry_price=1.0850,
            sr_zones=[{"lower": 1.0845, "upper": 1.0855, "tested": True}],
            bos_confirmed=True,
        )
        assert score == 45.0

    def test_score_structure_direction_mismatch(self):
        score = score_structure("bearish", "long", 1.08, [], False)
        assert score == 0.0

    def test_score_structure_no_sr_zone(self):
        score = score_structure("bullish", "long", 1.08, [], False)
        assert score == 20.0


# ═══════════════════════════════════════════════════════════════════════════════
# EMA strategy
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmaStrategy:
    def setup_method(self):
        clear_cache()

    def test_get_best_emas_returns_3_periods(self):
        df = _trending_up(220)
        s, m, l = get_best_emas("TEST", "H1", df)
        assert s < m < l

    def test_get_best_emas_periods_from_config(self):
        import config as cfg
        df = _trending_up(220)
        s, m, l = get_best_emas("TEST2", "H1", df)
        for p in (s, m, l):
            assert p in cfg.EMA_TEST_PERIODS

    def test_cache_used_on_second_call(self):
        df = _trending_up(220)
        r1 = get_best_emas("CACHE_TEST", "H1", df)
        r2 = get_best_emas("CACHE_TEST", "H1", df)
        assert r1 == r2

    def test_buy_signal_returns_zero_on_downtrend(self):
        """In a downtrend, H4 gate fails → buy score = 0."""
        df = _trending_down(220)
        score = check_buy_signal("DOWN", df, df)
        assert score == 0.0

    def test_sell_signal_returns_zero_on_uptrend(self):
        """In an uptrend, H4 gate fails → sell score = 0."""
        df = _trending_up(220)
        score = check_sell_signal("UP", df, df)
        assert score == 0.0

    def test_buy_score_in_valid_range(self):
        df = _trending_up(220)
        score = check_buy_signal("RANGE", df, df)
        assert 0 <= score <= 25

    def test_sell_score_in_valid_range(self):
        df = _trending_down(220)
        score = check_sell_signal("RANGE2", df, df)
        assert 0 <= score <= 25

    def test_insufficient_data_returns_zero(self):
        df = _flat(30)  # only 30 candles — not enough
        assert check_buy_signal("SHORT", df, df) == 0.0
        assert check_sell_signal("SHORT2", df, df) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Signal audit contract — engine/signal_engine.py must only pass kwargs that
# engine/signal_audit.log_signal() actually accepts
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalAuditContract:
    """
    Regression test for a real 2026-07-03 incident: signal_engine.py's audit
    calls passed c7_macd_momentum=... but log_signal()'s signature never had
    that parameter (keyword-only, no **kwargs catch-all) — every scan that
    landed in the "SCANNING"/"WATCHING" score band raised an unhandled
    TypeError, which (combined with the scan-loop bug fixed the same day)
    silently killed signal processing for whichever pairs came after the one
    that hit it. See bugs_scheduler_reliability memory.

    Rather than hardcoding the exact kwargs of today's fix (which would only
    catch this exact regression), this statically verifies every kwarg name
    signal_engine.py passes to its audit calls is one log_signal() actually
    accepts — catches this whole class of "these two files drifted out of
    sync" bug, not just today's specific instance of it.
    """

    def test_signal_engine_audit_kwargs_are_all_valid(self):
        """
        Uses ast (a real Python parser), not regex, to find every _do_audit(...)
        call site and check its keyword arguments — a first attempt at this
        test used regex and silently failed to catch a deliberately
        reintroduced copy of the real bug (multi-line calls with nested
        parens broke the regex's block boundaries). Verified this ast-based
        version DOES catch it before relying on it — see the manual
        verification note in tasks/todo.md for this fix.
        """
        import ast
        import inspect
        import engine.signal_engine as se
        from engine.signal_audit import log_signal

        valid_params = set(inspect.signature(log_signal).parameters)

        source = inspect.getsource(se)
        tree = ast.parse(source)

        call_sites = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_do_audit"):
                call_sites.append(node)

        assert len(call_sites) >= 1, "expected to find _do_audit(...) call sites — parsing may be broken"

        for node in call_sites:
            kwarg_names = {kw.arg for kw in node.keywords if kw.arg is not None}
            invalid = kwarg_names - valid_params
            assert not invalid, (
                f"signal_engine.py line {node.lineno} passes kwarg(s) {invalid} "
                f"to _do_audit() that engine.signal_audit.log_signal() does not "
                f"accept (valid params: {sorted(valid_params)}) — this raises "
                f"TypeError at runtime for any signal that reaches this call, "
                f"exactly like the 2026-07-03 c7_macd_momentum bug"
            )
