import logging
from typing import Callable, Optional

import pandas as pd

import config
from engine.indicators import ema, atr as calc_atr, cci, macd_histogram
from engine.strategy_ema_cci_macd import (
    check_buy_signal, check_sell_signal, get_best_emas, get_stop_loss,
)
from engine.strategy_market_structure import (
    detect_pivots, classify_structure, detect_bos_choch, get_sr_zones, score_structure,
)
from engine.strategy_price_action import detect_patterns, score_price_action
from engine.confluence_scorer import score_signal
from risk.risk_manager import get_tp_levels

logger = logging.getLogger(__name__)

# get_candles signature: (instrument, granularity, count) → pd.DataFrame
GetCandlesFn = Callable[[str, str, int], pd.DataFrame]


class SignalEngine:
    """
    Orchestrates all three sub-strategies for one pair per candle close.

    Usage:
        engine = SignalEngine(get_candles_fn=oanda.get_candles)
        signal = engine.run("EUR_USD", "forex")
    """

    def __init__(self, get_candles_fn: GetCandlesFn):
        self._get_candles = get_candles_fn

    def run(
        self,
        pair: str,
        market: str,
        ml_win_prob: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Run the full signal pipeline for one pair.

        pair        : "EUR_USD" (Oanda) or "BTC/USD" (Alpaca)
        market      : "forex" or "crypto"
        ml_win_prob : win probability from PatternLearner (None until 50 trades)

        Returns a signal dict when score >= MIN_CONFLUENCE_SCORE, else None.
        Signals scoring 50–69 are logged but return None (no trade fired).
        """
        # Fetch primary (H1) and confirmation (H4) candles
        # 250 candles gives the EMA auto-fit 200 candles + extra buffer
        try:
            df_h1 = self._get_candles(pair, config.TIMEFRAMES["primary"], 250)
            df_h4 = self._get_candles(pair, config.TIMEFRAMES["confirm"],  250)
        except Exception as exc:
            logger.error("Candle fetch failed for %s: %s", pair, exc)
            return None

        if len(df_h1) < 50 or len(df_h4) < 50:
            logger.warning("Insufficient data for %s (%d H1, %d H4)", pair, len(df_h1), len(df_h4))
            return None

        # ── EMA + CCI + MACD ──────────────────────────────────────────────────
        buy_score  = check_buy_signal(pair, df_h1, df_h4)
        sell_score = check_sell_signal(pair, df_h1, df_h4)

        if buy_score == sell_score == 0:
            logger.info("No signal for %s — both BUY and SELL scored 0 (H4 gate or EMA invalid)", pair)
            return None

        if buy_score >= sell_score:
            direction  = "long"
            ema_score  = buy_score
        else:
            direction  = "short"
            ema_score  = sell_score

        # ── Market structure ──────────────────────────────────────────────────
        pivots_h1    = detect_pivots(df_h1)
        structure    = classify_structure(pivots_h1)
        pivots_h4    = detect_pivots(df_h4)
        structure_h4 = classify_structure(pivots_h4)
        bos_h4       = detect_bos_choch(df_h4, pivots_h4, structure_h4)
        sr_zones     = get_sr_zones(df_h1, pivots_h1)

        entry       = float(df_h1["close"].iloc[-1])
        bos_ok      = bos_h4["bos"] and bos_h4["bos_direction"] == direction
        struct_score = score_structure(structure, direction, entry, sr_zones, bos_ok)

        # ── Price action ──────────────────────────────────────────────────────
        patterns = detect_patterns(df_h1)
        pa_score = max(score_price_action(patterns, direction), 0.0)

        # ── Confluence ────────────────────────────────────────────────────────
        final_score = score_signal(ema_score, struct_score, pa_score, ml_win_prob)

        logger.info(
            "Signal %s %s dir=%s score=%d (EMA=%.0f Struct=%.0f PA=%.0f)",
            pair, market, direction, final_score, ema_score, struct_score, pa_score,
        )

        if final_score < 35:
            return None

        if final_score < config.MIN_CONFLUENCE_SCORE:
            status = "WATCHING" if final_score >= 50 else "SCANNING"
            logger.info("Signal %s scored %d — %s (no trade)", pair, final_score, status)
            # Return a partial signal so the dashboard shows score + direction
            return {
                "pair":             pair,
                "market":           market,
                "direction":        direction,
                "score":            final_score,
                "ema_score":        ema_score,
                "structure_score":  struct_score,
                "pa_score":         pa_score,
                "entry":            float(df_h1["close"].iloc[-1]),
                "stop_loss":        None,
                "tp_levels":        None,
                "patterns":         patterns,
                "market_structure": structure,
                "bos_confirmed":    bos_ok,
                "watching":         True,
            }

        # ── Build full signal dict ────────────────────────────────────────────
        stop_loss = get_stop_loss(pair, df_h1, direction)
        tp_levels = get_tp_levels(entry, stop_loss, direction)

        # Indicator values for the learning engine log
        cci_s    = cci(df_h1["high"], df_h1["low"], df_h1["close"], config.CCI_PERIOD)
        mhist    = macd_histogram(df_h1["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
        atr_s    = calc_atr(df_h1["high"], df_h1["low"], df_h1["close"], 14)
        c        = df_h1.iloc[-1]
        cr       = c["high"] - c["low"]

        short, mid, long_ = get_best_emas(pair, config.TIMEFRAMES["primary"], df_h1)
        at_sr = any(z["lower"] <= entry <= z["upper"] for z in sr_zones if z["tested"])

        return {
            "pair":                pair,
            "market":              market,
            "direction":           direction,
            "entry":               entry,
            "stop_loss":           stop_loss,
            "tp_levels":           tp_levels,
            "score":               final_score,
            "ema_score":           ema_score,
            "structure_score":     struct_score,
            "pa_score":            pa_score,
            "patterns":            patterns,
            "ema_periods":         (short, mid, long_),
            "cci_at_signal":       float(cci_s.iloc[-1]),
            "macd_hist_at_signal": float(mhist.iloc[-1]),
            "market_structure":    structure,
            "was_at_sr_zone":      at_sr,
            "bos_confirmed":       bos_ok,
            "ml_win_prob":         ml_win_prob,
            "candle_body_ratio":   abs(c["close"] - c["open"]) / cr if cr > 0 else 0,
            "upper_wick_ratio":    (c["high"] - max(c["open"], c["close"])) / cr if cr > 0 else 0,
            "lower_wick_ratio":    (min(c["open"], c["close"]) - c["low"]) / cr if cr > 0 else 0,
            "atr":                 float(atr_s.iloc[-1]),
            "sr_zones":            sr_zones,
        }
