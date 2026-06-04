import logging

import numpy as np
import pandas as pd

from engine.indicators import atr as calc_atr

logger = logging.getLogger(__name__)


# ── Pivot detection ───────────────────────────────────────────────────────────

def detect_pivots(df: pd.DataFrame, n_keep: int = 6) -> dict:
    """
    Scan the last 100 candles for swing highs and lows.

    Pivot High: high[i] > high[i-1] AND high[i] > high[i+1]
    Pivot Low : low[i]  < low[i-1]  AND low[i]  < low[i+1]

    Returns {"highs": [...], "lows": [...]} — most recent last, up to n_keep each.
    """
    scan = df.tail(100)
    highs_arr = scan["high"].values
    lows_arr  = scan["low"].values

    pivot_highs, pivot_lows = [], []
    for i in range(1, len(scan) - 1):
        if highs_arr[i] > highs_arr[i - 1] and highs_arr[i] > highs_arr[i + 1]:
            pivot_highs.append(float(highs_arr[i]))
        if lows_arr[i] < lows_arr[i - 1] and lows_arr[i] < lows_arr[i + 1]:
            pivot_lows.append(float(lows_arr[i]))

    return {
        "highs": pivot_highs[-n_keep:],
        "lows":  pivot_lows[-n_keep:],
    }


# ── Structure classification ──────────────────────────────────────────────────

def classify_structure(pivots: dict) -> str:
    """
    "bullish" : Higher Highs AND Higher Lows
    "bearish" : Lower Highs  AND Lower Lows
    "ranging" : no clear sequence
    """
    highs = pivots.get("highs", [])
    lows  = pivots.get("lows",  [])

    if len(highs) < 2 or len(lows) < 2:
        return "ranging"

    hh = all(highs[i] > highs[i - 1] for i in range(1, len(highs)))
    hl = all(lows[i]  > lows[i - 1]  for i in range(1, len(lows)))
    lh = all(highs[i] < highs[i - 1] for i in range(1, len(highs)))
    ll = all(lows[i]  < lows[i - 1]  for i in range(1, len(lows)))

    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "ranging"


# ── Break of Structure / Change of Character ──────────────────────────────────

def detect_bos_choch(df: pd.DataFrame, pivots: dict, structure: str) -> dict:
    """
    Break of Structure (BoS):
      Bullish BoS: any close in last 5 candles > most recent swing high
      Bearish BoS: any close in last 5 candles < most recent swing low

    Change of Character (ChoCH): first BoS against prevailing structure.

    Returns:
        bos             : bool
        bos_direction   : "bullish" | "bearish" | None
        choch           : bool
    """
    if not pivots["highs"] or not pivots["lows"]:
        return {"bos": False, "bos_direction": None, "choch": False}

    last5       = df["close"].tail(5)
    recent_high = pivots["highs"][-1]
    recent_low  = pivots["lows"][-1]

    bull_bos = bool((last5 > recent_high).any())
    bear_bos = bool((last5 < recent_low).any())

    bos_direction = "bullish" if bull_bos else ("bearish" if bear_bos else None)
    choch = (structure == "bullish" and bear_bos) or (structure == "bearish" and bull_bos)

    return {
        "bos":           bull_bos or bear_bos,
        "bos_direction": bos_direction,
        "choch":         choch,
    }


# ── Support & Resistance zones ────────────────────────────────────────────────

def get_sr_zones(df: pd.DataFrame, pivots: dict) -> list[dict]:
    """
    Auto-generate zones from the last 3 pivot highs (resistance)
    and last 3 pivot lows (support). Width = ±0.5 × ATR14.
    A zone is "tested" when 2+ candles touched it.
    """
    atr_series = calc_atr(df["high"], df["low"], df["close"], 14)
    atr_val    = float(atr_series.dropna().iloc[-1]) if not atr_series.dropna().empty else 1e-4
    half_w     = 0.5 * atr_val

    zones = []
    for price in (pivots["highs"][-3:] if len(pivots["highs"]) >= 3 else pivots["highs"]):
        touches = _count_touches(df, price, half_w)
        zones.append(_make_zone(price, half_w, "resistance", touches))

    for price in (pivots["lows"][-3:] if len(pivots["lows"]) >= 3 else pivots["lows"]):
        touches = _count_touches(df, price, half_w)
        zones.append(_make_zone(price, half_w, "support", touches))

    return zones


def _make_zone(price: float, half_w: float, zone_type: str, touches: int) -> dict:
    return {
        "price":   price,
        "upper":   price + half_w,
        "lower":   price - half_w,
        "type":    zone_type,
        "touches": touches,
        "tested":  touches >= 2,
    }


def _count_touches(df: pd.DataFrame, price: float, half_w: float) -> int:
    """Count candles whose open/close range overlapped this zone."""
    body_hi = df[["open", "close"]].max(axis=1)
    body_lo = df[["open", "close"]].min(axis=1)
    return int(((body_hi >= price - half_w) & (body_lo <= price + half_w)).sum())


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_structure(
    structure: str,
    signal_direction: str,
    entry_price: float,
    sr_zones: list[dict],
    bos_confirmed: bool,
) -> float:
    """
    Returns 0–45:
      +20  structure direction matches signal
      +15  entry within a tested S/R zone
      +10  BoS confirmed in signal direction
    """
    score = 0.0

    if (structure == "bullish" and signal_direction == "long") or \
       (structure == "bearish" and signal_direction == "short"):
        score += 20

    if any(z["tested"] and z["lower"] <= entry_price <= z["upper"] for z in sr_zones):
        score += 15

    if bos_confirmed:
        score += 10

    return score
