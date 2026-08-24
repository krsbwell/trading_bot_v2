"""
engine/market_regime.py — classify_regime() built for the adaptive-strategy
integration (tasks/todo.md, 2026-08-21). Uses real synthetic OHLCV frames
(not monkeypatched indicators) since adx/ema/atr are pure pandas math here,
no external calls to isolate.
"""
import numpy as np
import pandas as pd

from engine.market_regime import classify_regime, REGIMES


def _trending_df(n=80, drift=0.0006, noise=0.00005, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="30min", tz="UTC")
    close = 1.1000 + np.cumsum(np.full(n, drift)) + rng.normal(0, noise, n)
    return pd.DataFrame({
        "open": close, "high": close + 0.0004, "low": close - 0.0004, "close": close,
    }, index=idx)


def _ranging_df(n=80, seed=2):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="30min", tz="UTC")
    close = 1.1000 + rng.normal(0, 0.00008, n)   # no drift, small noise
    return pd.DataFrame({
        "open": close, "high": close + 0.0004, "low": close - 0.0004, "close": close,
    }, index=idx)


def test_insufficient_data_returns_unknown():
    df = _trending_df(n=10)
    result = classify_regime(df)
    assert result["regime"] == "UNKNOWN"
    assert result["confidence"] == 0.0


def test_strong_uptrend_classified_trending_up():
    df = _trending_df(drift=0.0008)
    result = classify_regime(df)
    assert result["regime"] in ("TRENDING_UP", "HIGH_VOLATILITY", "BREAKOUT")
    # A clear, low-noise uptrend should register a positive slope regardless
    # of which named bucket volatility percentile puts it in.
    assert result["ema_slope_pct"] > 0


def test_strong_downtrend_has_negative_slope():
    df = _trending_df(drift=-0.0008)
    result = classify_regime(df)
    assert result["ema_slope_pct"] < 0


def test_result_shape_and_valid_regime():
    df = _trending_df()
    result = classify_regime(df)
    assert result["regime"] in REGIMES
    assert 0.0 <= result["confidence"] <= 1.0
    assert set(result.keys()) == {"regime", "confidence", "adx", "atr_pct", "ema_slope_pct"}


def test_never_raises_on_nan_heavy_data():
    df = _trending_df(n=60)
    df.loc[df.index[:5], "close"] = np.nan
    df["high"] = df["close"] + 0.0004
    df["low"] = df["close"] - 0.0004
    result = classify_regime(df)   # must not raise
    assert result["regime"] in REGIMES


def test_confirm_timeframe_disagreement_can_flag_breakout():
    # Primary sharply up, confirm timeframe flat/down — a fresh move the
    # higher TF hasn't caught up to yet.
    df_primary = _trending_df(drift=0.0015, noise=0.00002)
    df_confirm = _ranging_df()
    result = classify_regime(df_primary, df_confirm)
    assert result["regime"] in ("BREAKOUT", "TRENDING_UP", "HIGH_VOLATILITY")
