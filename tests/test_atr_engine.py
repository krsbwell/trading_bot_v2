"""
engine/atr_engine.py — compute_atr_stops(), built for the adaptive-strategy
integration (tasks/todo.md, 2026-08-21). Note: ATR *trailing* stops were
already backtested and rejected on 2026-07-18 — these tests only verify
this module's own arithmetic/contracts, not that ATR-based initial stops
are profitable (that requires a real backtest, tracked separately in
tasks/todo.md, before any live pair uses this).
"""
import numpy as np
import pandas as pd

from engine.atr_engine import compute_atr_stops


def _df(n=30, close=1.1000, atr_like_range=0.0010):
    idx = pd.date_range("2026-01-01", periods=n, freq="30min", tz="UTC")
    closes = np.full(n, close)
    return pd.DataFrame({
        "open": closes, "high": closes + atr_like_range, "low": closes - atr_like_range,
        "close": closes,
    }, index=idx)


def test_insufficient_data_returns_none():
    assert compute_atr_stops(_df(n=5), "long", "EUR_USD") is None


def test_long_stop_below_entry_tp_above():
    result = compute_atr_stops(_df(), "long", "EUR_USD", sl_mult=1.5, tp_mult=3.0)
    assert result is not None
    assert result["stop_loss"] < result["entry"] < result["take_profit"]


def test_short_stop_above_entry_tp_below():
    result = compute_atr_stops(_df(), "short", "EUR_USD", sl_mult=1.5, tp_mult=3.0)
    assert result is not None
    assert result["take_profit"] < result["entry"] < result["stop_loss"]


def test_tp_further_than_sl_when_tp_mult_bigger():
    result = compute_atr_stops(_df(), "long", "EUR_USD", sl_mult=1.5, tp_mult=3.0)
    sl_dist = result["entry"] - result["stop_loss"]
    tp_dist = result["take_profit"] - result["entry"]
    assert tp_dist > sl_dist


def test_jpy_pair_uses_3_decimals():
    result = compute_atr_stops(_df(close=150.00, atr_like_range=0.15), "long", "EUR_JPY")
    assert result is not None
    # round(x, 3) shouldn't leave more than 3 decimal places
    assert result["stop_loss"] == round(result["stop_loss"], 3)


def test_respects_minimum_stop_distance_floor(monkeypatch):
    import config
    monkeypatch.setattr(config, "min_sl_pips_for", lambda pair: 500)  # absurdly wide floor
    # A tiny ATR would otherwise produce a much tighter stop than the floor.
    result = compute_atr_stops(_df(atr_like_range=0.00001), "long", "EUR_USD", sl_mult=1.0)
    assert result is not None
    sl_pips = (result["entry"] - result["stop_loss"]) / 0.0001
    assert sl_pips >= 500 - 1e-6
