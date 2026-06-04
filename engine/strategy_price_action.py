import logging

import pandas as pd

logger = logging.getLogger(__name__)

_BULLISH = frozenset({
    "bullish_pin_bar", "bullish_engulfing", "morning_star",
    "hammer", "bullish_marubozu",
})
_BEARISH = frozenset({
    "bearish_pin_bar", "bearish_engulfing", "evening_star",
    "shooting_star", "bearish_marubozu",
})


# ── Candle helpers ────────────────────────────────────────────────────────────

def _body(c)       -> float: return abs(c["close"] - c["open"])
def _range(c)      -> float: return c["high"] - c["low"]
def _upper_wick(c) -> float: return c["high"] - max(c["open"], c["close"])
def _lower_wick(c) -> float: return min(c["open"], c["close"]) - c["low"]
def _is_bull(c)    -> bool:  return c["close"] > c["open"]
def _is_bear(c)    -> bool:  return c["close"] < c["open"]
def _is_doji(c)    -> bool:
    r = _range(c)
    return r == 0 or _body(c) / r < 0.10


# ── Pattern detectors ─────────────────────────────────────────────────────────

def _single(c) -> list[str]:
    r = _range(c)
    if r == 0:
        return []
    body = _body(c)
    uw   = _upper_wick(c)
    lw   = _lower_wick(c)
    out  = []

    # Bullish Pin Bar: lower_wick >= 2×body, close in top 30% of range
    if body > 0 and lw >= 2 * body and (c["close"] - c["low"]) / r >= 0.70:
        out.append("bullish_pin_bar")

    # Bearish Pin Bar: upper_wick >= 2×body, close in bottom 30% of range
    if body > 0 and uw >= 2 * body and (c["high"] - c["close"]) / r >= 0.70:
        out.append("bearish_pin_bar")

    # Hammer: small body, lower_wick >= 2×body, upper_wick < 10% of range
    if body > 0 and lw >= 2 * body and uw < 0.10 * r:
        out.append("hammer")

    # Shooting Star: small body, upper_wick >= 2×body, lower_wick < 10% of range
    if body > 0 and uw >= 2 * body and lw < 0.10 * r:
        out.append("shooting_star")

    # Bullish Marubozu: bullish, both wicks < 5% of range
    if _is_bull(c) and uw < 0.05 * r and lw < 0.05 * r:
        out.append("bullish_marubozu")

    # Bearish Marubozu: bearish, both wicks < 5% of range
    if _is_bear(c) and uw < 0.05 * r and lw < 0.05 * r:
        out.append("bearish_marubozu")

    return out


def _engulfing(c0, c1) -> list[str]:
    out = []
    # Bullish Engulfing: c0 bull body fully contains c1 bear body
    if _is_bull(c0) and _is_bear(c1):
        if c0["open"] <= c1["close"] and c0["close"] >= c1["open"]:
            out.append("bullish_engulfing")
    # Bearish Engulfing: c0 bear body fully contains c1 bull body
    if _is_bear(c0) and _is_bull(c1):
        if c0["open"] >= c1["close"] and c0["close"] <= c1["open"]:
            out.append("bearish_engulfing")
    return out


def _three_candle(c0, c1, c2) -> list[str]:
    out = []
    mid = (c2["open"] + c2["close"]) / 2

    # Morning Star: bearish c2, doji c1, bullish c0 closing above c2 midpoint
    if _is_bear(c2) and _is_doji(c1) and _is_bull(c0) and c0["close"] > mid:
        out.append("morning_star")

    # Evening Star: bullish c2, doji c1, bearish c0 closing below c2 midpoint
    if _is_bull(c2) and _is_doji(c1) and _is_bear(c0) and c0["close"] < mid:
        out.append("evening_star")

    return out


# ── Public API ────────────────────────────────────────────────────────────────

def detect_patterns(df: pd.DataFrame) -> list[str]:
    """
    Scan the most recent 1–3 candles.
    Returns list of matched pattern name strings.
    """
    if len(df) < 3:
        return []

    c0, c1, c2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]

    patterns = _single(c0) + _engulfing(c0, c1) + _three_candle(c0, c1, c2)
    # Deduplicate while preserving order
    seen, unique = set(), []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def score_price_action(patterns: list[str], signal_direction: str) -> float:
    """
    +20 for one confirming pattern
    +30 (capped) for two or more
    -10 per contradicting pattern

    Result can be negative; the signal engine floors it at 0 before summing.
    """
    if signal_direction == "long":
        confirming    = [p for p in patterns if p in _BULLISH]
        contradicting = [p for p in patterns if p in _BEARISH]
    else:
        confirming    = [p for p in patterns if p in _BEARISH]
        contradicting = [p for p in patterns if p in _BULLISH]

    score = 0.0
    if len(confirming) == 1:
        score = 20.0
    elif len(confirming) >= 2:
        score = 30.0

    score -= 10.0 * len(contradicting)

    logger.debug(
        "PA score dir=%s confirming=%s contradicting=%s → %.0f",
        signal_direction, confirming, contradicting, score,
    )
    return score
