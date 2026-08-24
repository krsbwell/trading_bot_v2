"""
Adaptive strategy — ML-driven, not rule-driven. Rewritten 2026-08-22 (see
tasks/todo.md "Adaptive AI/ML Strategy — Integration Plan" and the
2026-08-22 course-correction entry) after the first version (a hand-coded
regime+momentum scorer) turned out to just be "another indicator strategy"
— exactly what the original architecture request said not to build, and
backtested with no broad edge (7 of 8 pairs unprofitable).

This version does what was actually asked: learning.pattern_learner's
XGBoost model — trained on this project's real signal history (candle
price action, detected candlestick patterns, market structure/trend,
momentum, session, and which pair) — IS the decision-maker. Hand-coded
logic here only builds the feature vector; it never independently decides
buy/sell/no-trade. That's the inversion from v1, where rules decided and
ML only nudged the score afterward.

One shared model across all pairs, not a model per pair — this project's
entire signal history is ~200 usable rows across 5-6 pairs, nowhere near
enough to split further. `pair` is passed as a model feature instead, so
it can still learn pair-specific differences without starving any one
pair of its own dataset (see learning/pattern_learner.py's `_PAIR_ENCODING`
comment) — this is the "compartmentalize as it grows" mechanism.

Same interface every engine/strategy_*.py module implements
(check_buy_signal, check_sell_signal, get_stop_loss, get_last_diag), so
engine.strategy_dispatch can route a pair to "adaptive" exactly like any
other strategy. Still disabled by default (config.ADAPTIVE_STRATEGY
["enabled"]=False) and not in config.STRATEGY_OVERRIDE — same rollout-
safety rule as before, unchanged.
"""
import logging
from datetime import datetime, timezone

import pandas as pd

import config
from agents.learning_agent import predict_win_prob
from engine.atr_engine import compute_atr_stops
from engine.indicators import atr as calc_atr, cci, ema, macd_histogram
from engine.strategy_market_structure import classify_structure, detect_pivots
from engine.strategy_price_action import detect_patterns
from learning.data_collector import _get_session
from learning.pattern_learner import _pattern_name_to_features

logger = logging.getLogger(__name__)

_buy_diag:  dict = {}
_sell_diag: dict = {}


def get_last_diag(pair: str, direction: str) -> dict:
    if direction == "long":
        return dict(_buy_diag.get(pair, {}))
    return dict(_sell_diag.get(pair, {}))


def _pip_size(pair: str) -> float:
    p = pair.upper()
    return 0.01 if ("JPY" in p or "XAU" in p) else 0.0001


def _confirm_bias(df_confirm: pd.DataFrame) -> str:
    """Close vs EMA on the confirm timeframe — same "bull"/"bear"/"neutral"
    convention engine.signal_engine.py and engine.strategy_trend_retest.py
    already use for h4_trend, so this feature means the same thing here as
    it does in the historical training data."""
    period = config.EMA_FIXED_PERIODS[1]
    if len(df_confirm) < period:
        return "neutral"
    ema_val = float(ema(df_confirm["close"], period).iloc[-1])
    close = float(df_confirm["close"].iloc[-1])
    if ema_val != ema_val:  # NaN
        return "neutral"
    return "bull" if close > ema_val else "bear"


def _build_features(pair: str, df_primary: pd.DataFrame, df_confirm: pd.DataFrame,
                     direction: str) -> dict:
    """Price action + trend + candlestick pattern + momentum features for
    one bar — the same feature *names* learning.pattern_learner.FEATURES
    trains on, so predict_win_prob() can actually use them (any name it
    doesn't recognize is silently ignored, any name it expects but doesn't
    get here defaults to 0 — see PatternLearner.predict_win_prob)."""
    c = df_primary.iloc[-1]
    candle_range = c["high"] - c["low"]

    patterns = detect_patterns(df_primary)
    pattern_features = _pattern_name_to_features("|".join(patterns))

    pivots = detect_pivots(df_primary)
    structure = classify_structure(pivots)

    cci_val = float(cci(df_primary["high"], df_primary["low"], df_primary["close"],
                         config.CCI_PERIOD).iloc[-1])
    hist_s = macd_histogram(df_primary["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    hist_val = float(hist_s.iloc[-1]) if len(hist_s) else 0.0

    atr_val = float(calc_atr(df_primary["high"], df_primary["low"], df_primary["close"], 14).iloc[-1])
    pip = _pip_size(pair)
    atr_pips = (atr_val / pip) if pip else 0.0

    return {
        "pair": pair,
        "direction": direction,
        "candle_body_ratio": abs(c["close"] - c["open"]) / candle_range if candle_range > 0 else 0.0,
        "upper_wick_ratio": (c["high"] - max(c["open"], c["close"])) / candle_range if candle_range > 0 else 0.0,
        "lower_wick_ratio": (min(c["open"], c["close"]) - c["low"]) / candle_range if candle_range > 0 else 0.0,
        **pattern_features,
        "market_structure": structure,
        "cci_at_signal": cci_val,
        "macd_hist_at_signal": hist_val,
        "atr_pips": atr_pips,
        "hour_utc": df_primary.index[-1].hour,
        "h4_trend": _confirm_bias(df_confirm),
        "d_trend": "neutral",   # no Daily candles at this interface layer — see docstring
        "session": _get_session(datetime.now(timezone.utc)),
    }


def _score_direction(pair: str, df_primary: pd.DataFrame, df_confirm: pd.DataFrame,
                      direction: str, diag_store: dict) -> float:
    params = config.adaptive_strategy_params_for(pair)
    if not params.get("enabled", False):
        return 0.0
    if len(df_primary) < 50 or len(df_confirm) < 50:
        return 0.0

    features = _build_features(pair, df_primary, df_confirm, direction)
    win_prob = predict_win_prob(features)

    if win_prob is None:
        # Model genuinely has no opinion (no model file yet, or a
        # prediction error) — NO_TRADE, not a fabricated score. Matches
        # the "if the model cannot produce a valid confidence value,
        # represent that explicitly" rule from the source design doc.
        diag_store[pair] = {"win_prob": None, "reason": "model unavailable", "features": features}
        return 0.0

    diag_store[pair] = {"win_prob": win_prob, "features": features}

    if win_prob < params["min_confidence"]:
        return 0.0

    # Scale to the same 0-25 range every other strategy's directional
    # score uses (see engine/confluence_scorer.py's ema_score slot).
    return float(round(win_prob * 25))


def check_buy_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame,
                      adaptive: "dict | None" = None) -> float:
    return _score_direction(pair, df_h1, df_h4, "long", _buy_diag)


def check_sell_signal(pair: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame,
                       adaptive: "dict | None" = None) -> float:
    return _score_direction(pair, df_h1, df_h4, "short", _sell_diag)


def get_stop_loss(pair: str, df_h1: pd.DataFrame, direction: str) -> float:
    """ATR-based stop distance (engine.atr_engine), unchanged from v1 —
    SL/TP is a risk-management question, not a learning question, and
    ATR + risk.risk_manager already handle it well."""
    params = config.adaptive_strategy_params_for(pair)
    result = compute_atr_stops(
        df_h1, direction, pair,
        period=params["atr_period"],
        sl_mult=params["atr_sl_mult"],
        tp_mult=params["atr_tp_mult"],
    )
    if result is None:
        entry = float(df_h1["close"].iloc[-1]) if len(df_h1) else 0.0
        pip = _pip_size(pair)
        min_dist = pip * config.min_sl_pips_for(pair)
        return round(entry - min_dist if direction == "long" else entry + min_dist, 5)
    return result["stop_loss"]


def clear_cache() -> None:
    """Reset diagnostics — used in tests, same convention as the other
    strategy_*.py modules."""
    _buy_diag.clear()
    _sell_diag.clear()
