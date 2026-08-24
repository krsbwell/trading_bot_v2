"""
engine/strategy_adaptive.py — rewritten 2026-08-22 to be ML-driven
(learning.pattern_learner's prediction IS the decision, not a booster on
hand-coded rules — see tasks/todo.md for why the first, rule-based version
was scrapped). Disabled by default (config.ADAPTIVE_STRATEGY["enabled"]
=False) — the first tests here confirm that default produces zero signal
regardless of how favorable the data looks, since that's the rollout-
safety guarantee the whole plan depends on.
"""
import numpy as np
import pandas as pd
import pytest

import config
import engine.strategy_adaptive as sa


def _df(n=80, start=1.1000, drift=0.0006, noise=0.00003, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="30min", tz="UTC")
    close = start + np.cumsum(np.full(n, drift)) + rng.normal(0, noise, n)
    return pd.DataFrame({
        "open": close, "high": close + 0.0004, "low": close - 0.0004, "close": close,
    }, index=idx)


@pytest.fixture(autouse=True)
def _clear():
    sa.clear_cache()
    yield
    sa.clear_cache()


def test_disabled_scores_zero_even_on_ideal_data(monkeypatch):
    """2026-08-23: ADAPTIVE_STRATEGY["enabled"] is True by default now
    (EUR_AUD is running it live, see config.STRATEGY_OVERRIDE) — this test
    explicitly forces the disabled path to confirm the *mechanism* still
    works, since the enabled/disabled gate itself is what every pair not
    routed to "adaptive" depends on staying correct."""
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", False)
    df = _df()
    assert sa.check_buy_signal("EUR_USD", df, df) == 0.0
    assert sa.check_sell_signal("EUR_USD", df, df) == 0.0


def test_enabled_but_insufficient_data_scores_zero(monkeypatch):
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", True)
    short_df = _df(n=10)
    assert sa.check_buy_signal("EUR_USD", short_df, short_df) == 0.0


def test_enabled_no_model_scores_zero_not_fabricated(monkeypatch):
    """When predict_win_prob() has no opinion (no model file / prediction
    error), the strategy must return 0.0, never guess a score."""
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", True)
    monkeypatch.setattr("engine.strategy_adaptive.predict_win_prob", lambda features: None)
    df = _df()
    assert sa.check_buy_signal("EUR_USD", df, df) == 0.0
    diag = sa.get_last_diag("EUR_USD", "long")
    assert diag["win_prob"] is None


def test_enabled_high_win_prob_scores_near_max(monkeypatch):
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", True)
    monkeypatch.setattr("engine.strategy_adaptive.predict_win_prob", lambda features: 0.9)
    df = _df()
    score = sa.check_buy_signal("EUR_USD", df, df)
    assert score == round(0.9 * 25)


def test_enabled_low_win_prob_below_min_confidence_scores_zero(monkeypatch):
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", True)
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "min_confidence", 0.55)
    monkeypatch.setattr("engine.strategy_adaptive.predict_win_prob", lambda features: 0.3)
    df = _df()
    assert sa.check_buy_signal("EUR_USD", df, df) == 0.0


def test_enabled_win_prob_at_exactly_min_confidence_fires(monkeypatch):
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", True)
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "min_confidence", 0.55)
    monkeypatch.setattr("engine.strategy_adaptive.predict_win_prob", lambda features: 0.55)
    df = _df()
    assert sa.check_buy_signal("EUR_USD", df, df) == round(0.55 * 25)


def test_score_never_exceeds_25(monkeypatch):
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", True)
    monkeypatch.setattr("engine.strategy_adaptive.predict_win_prob", lambda features: 1.0)
    df = _df()
    assert sa.check_buy_signal("EUR_USD", df, df) <= 25.0


def test_feature_vector_includes_pair_and_direction(monkeypatch):
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", True)
    seen = {}

    def _spy(features):
        seen.update(features)
        return 0.6

    monkeypatch.setattr("engine.strategy_adaptive.predict_win_prob", _spy)
    df = _df()
    sa.check_buy_signal("EUR_USD", df, df)
    assert seen["pair"] == "EUR_USD"
    assert seen["direction"] == "long"

    sa.check_sell_signal("GBP_USD", df, df)
    assert seen["pair"] == "GBP_USD"
    assert seen["direction"] == "short"


def test_feature_vector_includes_price_action_and_structure_keys(monkeypatch):
    """These are the exact feature names learning.pattern_learner.FEATURES
    trains on — a name mismatch here would mean the model silently gets
    fed nothing useful (predict_win_prob defaults missing keys to 0)."""
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", True)
    seen = {}

    def _spy(features):
        seen.update(features)
        return 0.6

    monkeypatch.setattr("engine.strategy_adaptive.predict_win_prob", _spy)
    df = _df()
    sa.check_buy_signal("EUR_USD", df, df)

    for key in ("candle_body_ratio", "upper_wick_ratio", "lower_wick_ratio",
                "has_bullish_pattern", "has_bearish_pattern", "pattern_count",
                "market_structure", "cci_at_signal", "macd_hist_at_signal",
                "atr_pips", "hour_utc", "h4_trend", "d_trend", "session"):
        assert key in seen, f"missing feature: {key}"


def test_get_last_diag_populated_after_call(monkeypatch):
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", True)
    monkeypatch.setattr("engine.strategy_adaptive.predict_win_prob", lambda features: 0.6)
    df = _df()
    sa.check_buy_signal("EUR_USD", df, df)
    diag = sa.get_last_diag("EUR_USD", "long")
    assert diag["win_prob"] == 0.6


def test_get_last_diag_empty_for_unknown_pair():
    assert sa.get_last_diag("XXX_YYY", "long") == {}


def test_clear_cache_empties_diagnostics(monkeypatch):
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", True)
    monkeypatch.setattr("engine.strategy_adaptive.predict_win_prob", lambda features: 0.6)
    df = _df()
    sa.check_buy_signal("EUR_USD", df, df)
    sa.clear_cache()
    assert sa.get_last_diag("EUR_USD", "long") == {}


def test_get_stop_loss_long_below_entry():
    df = _df()
    entry = float(df["close"].iloc[-1])
    sl = sa.get_stop_loss("EUR_USD", df, "long")
    assert sl < entry


def test_get_stop_loss_short_above_entry():
    df = _df()
    entry = float(df["close"].iloc[-1])
    sl = sa.get_stop_loss("EUR_USD", df, "short")
    assert sl > entry


def test_get_stop_loss_handles_empty_df_without_raising():
    empty = pd.DataFrame(columns=["open", "high", "low", "close"])
    sl = sa.get_stop_loss("EUR_USD", empty, "long")
    assert isinstance(sl, float)


def test_real_model_end_to_end_does_not_raise(monkeypatch):
    """No mocking — exercises the real predict_win_prob() -> real
    models/ml_model.pkl path once, so a wiring break (bad feature name,
    import error) shows up here even if every mocked test above passes.
    monkeypatch.setitem (not a direct dict mutation) so this can't leak
    state into other test modules that share the same config.py process —
    the previous version's manual try/finally reset the flag to False,
    which was wrong even before 2026-08-23 (any test error before the
    finally still skipped it) and is actively wrong now that True is the
    real default."""
    monkeypatch.setitem(config.ADAPTIVE_STRATEGY, "enabled", True)
    df = _df()
    score = sa.check_buy_signal("EUR_USD", df, df)
    assert isinstance(score, float)
    assert 0.0 <= score <= 25.0
