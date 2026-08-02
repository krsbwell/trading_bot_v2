import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import pandas as pd

import config
from engine.indicators import ema, atr as calc_atr, cci, macd_histogram
from engine.strategy_ema_cci_macd import get_best_emas
from engine.strategy_dispatch import resolve_strategy
from engine.adaptive_params import adaptive_params
from engine.wfo_optimizer import wfo_optimizer
from engine.strategy_market_structure import (
    detect_pivots, classify_structure, detect_bos_choch, get_sr_zones, score_structure,
)
from engine.strategy_price_action import detect_patterns, score_price_action
from engine.confluence_scorer import score_signal
from engine.signal_audit import log_signal as _audit
from learning.data_collector import _get_session
from learning.pattern_learner import PatternLearner
from risk.risk_manager import get_tp_levels

logger = logging.getLogger(__name__)

# Used only for the pre-score ML boost (config.ML_SCORE_BOOST_ENABLED) — cheap to
# construct (no state beyond a counter; predict_win_prob() loads the model fresh
# from disk on every call), kept separate from main.py's own PatternLearner
# instance which owns retraining.
_ml_model = PatternLearner()

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
        no_audit: bool = False,
        candles_override: Optional[tuple] = None,
    ) -> Optional[dict]:
        """
        Run the full signal pipeline for one pair.

        pair             : "EUR_USD" (Oanda)
        market           : "forex"
        ml_win_prob      : win probability from PatternLearner (None until 50 trades)
        candles_override : (df_primary, df_confirm) tuple — skips the API fetch and
                           uses supplied DataFrames instead. Used by _live_diagnostic_scan
                           to inject the in-progress bar without touching the
                           completed-only fetch path that drives trade decisions.

        Returns a signal dict when score >= MIN_CONFLUENCE_SCORE, else None.
        Signals scoring 50–69 are logged but return None (no trade fired).
        """
        _buy_fn, _sell_fn, _sl_fn, _diag_fn = resolve_strategy(pair)

        # Suppress audit writes for diagnostic-only calls (e.g. _diagnostic_scan in main.py)
        _do_audit = (lambda **kw: None) if no_audit else _audit

        # Fetch primary (M30) and confirmation (H1) candles — or use override for live bar
        if candles_override is not None:
            df_h1, df_h4 = candles_override
        else:
            try:
                df_h1 = self._get_candles(pair, config.TIMEFRAMES["primary"], 250)
                df_h4 = self._get_candles(pair, config.confirm_tf_for(pair),   250)
            except Exception as exc:
                logger.error("Candle fetch failed for %s: %s", pair, exc)
                return None

        if len(df_h1) < 50 or len(df_h4) < 50:
            logger.warning("Insufficient data for %s (%d H1, %d H4)", pair, len(df_h1), len(df_h4))
            return None

        # ── H4 / D trend for multi-TF display (non-blocking diagnostic) ─────────
        h4_trend = d_trend = "neutral"
        h4_gate_info = {"passed": None, "close": None, "ema": None}
        try:
            from engine.indicators import ema as _ema_fn
            short_p, _, _ = get_best_emas(pair, config.TIMEFRAMES["primary"], df_h1)
            h4_ema_val = float(_ema_fn(df_h4["close"], short_p).iloc[-1])
            h4_close   = float(df_h4["close"].iloc[-1])
            h4_bull    = h4_close > h4_ema_val
            h4_trend   = "bull" if h4_bull else "bear"
            h4_gate_info = {"passed": h4_bull, "close": h4_close, "ema": h4_ema_val}
        except Exception:
            pass

        try:
            gran_d = "D" if "_" in pair else "1Day"
            df_d   = self._get_candles(pair, gran_d, 30)
            if df_d is not None and len(df_d) >= 20:
                from engine.indicators import ema as _ema_fn
                d_ema_val = float(_ema_fn(df_d["close"], 20).iloc[-1])
                d_close   = float(df_d["close"].iloc[-1])
                d_trend   = "bull" if d_close > d_ema_val else "bear"
        except Exception:
            pass

        # ── EMA + CCI + MACD ──────────────────────────────────────────────────
        # Merge WFO indicator periods (outer layer) with adaptive thresholds (inner layer).
        # Adaptive keys win on any overlap, WFO provides CCI_PERIOD / MACD_* / min_score.
        _wfo       = wfo_optimizer.get_params(pair)
        _ap        = adaptive_params.get(pair)
        _combined  = {**_wfo, **_ap}
        buy_score  = _buy_fn(pair, df_h1, df_h4, adaptive=_combined)
        sell_score = _sell_fn(pair, df_h1, df_h4, adaptive=_combined)

        if buy_score == sell_score == 0:
            # Diagnose why: check which direction H4 aligned with, then why that direction scored 0
            _buy_d  = _diag_fn(pair, "long")
            _sell_d = _diag_fn(pair, "short")
            if h4_trend == "bull":
                _reject = "NO_TOUCH" if not _buy_d.get("c3") else "CONDITIONS_WEAK"
            elif h4_trend == "bear":
                _reject = "NO_TOUCH" if not _sell_d.get("c3") else "CONDITIONS_WEAK"
            else:
                _reject = "H4_NEUTRAL"
            _do_audit(pair=pair, timeframe=config.TIMEFRAMES["primary"],
                   direction="—", ema_score=0, structure_score=0,
                   pa_score=0, confluence_score=0,
                   h4_trend=h4_trend, d_trend=d_trend,
                   result="NO_SIGNAL", reject_reason=_reject)
            return {
                "pair":          pair,
                "market":        market,
                "direction":     "—",
                "score":         0,
                "ema_score":     0,
                "structure_score": 0,
                "pa_score":      0,
                "timeframe":     config.TIMEFRAMES["primary"],
                "h4_gate":       h4_gate_info,
                "h4_trend":      h4_trend,
                "d_trend":       d_trend,
                "no_signal":     True,
                "watching":      False,
                "reject_reason": _reject,
            }

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

        # ── Early ATR computation (needed for ML features below + volatility gate) ─
        _atr_series = calc_atr(df_h1["high"], df_h1["low"], df_h1["close"], 14)
        _atr_val    = float(_atr_series.iloc[-1])
        _pip_size   = 0.01 if ("JPY" in pair.upper() or "XAU" in pair.upper()) else 0.0001
        _atr_pips   = _atr_val / _pip_size

        # Fetch condition diagnostics set by check_buy/sell_signal (moved ahead of
        # score_signal() so its cci/macd values can feed the ML boost below)
        _diag = _diag_fn(pair, direction)

        # ── ML score boost (config.ML_SCORE_BOOST_ENABLED, off by default) ──────
        # Computed here — before score_signal() — so a high-confidence prediction
        # can help a marginal signal cross MIN_CONFLUENCE_SCORE, not just suppress
        # one after the fact (main.py's separate post-hoc block gate still applies
        # regardless of this flag). Only fires when the caller didn't already
        # supply a prediction (tests/other callers can still inject one directly).
        if ml_win_prob is None and getattr(config, "ML_SCORE_BOOST_ENABLED", False):
            try:
                _c  = df_h1.iloc[-1]
                _cr = _c["high"] - _c["low"]
                _ml_features = {
                    "confluence_score":    ema_score + struct_score + pa_score,
                    "ema_score":           ema_score,
                    "structure_score":     struct_score,
                    "pa_score":            pa_score,
                    "candle_body_ratio":   abs(_c["close"] - _c["open"]) / _cr if _cr > 0 else 0,
                    "upper_wick_ratio":    (_c["high"] - max(_c["open"], _c["close"])) / _cr if _cr > 0 else 0,
                    "lower_wick_ratio":    (min(_c["open"], _c["close"]) - _c["low"]) / _cr if _cr > 0 else 0,
                    "cci_at_signal":       _diag.get("cci_current") or 0,
                    "macd_hist_at_signal": _diag.get("macd_hist_val") or 0,
                    "was_at_sr_zone":      any(z["lower"] <= entry <= z["upper"] for z in sr_zones if z["tested"]),
                    "bos_confirmed":       bos_ok,
                    "atr_pips":            _atr_pips,
                    "hour_utc":            df_h1.index[-1].hour,
                    "direction":           direction,
                    "h4_trend":            h4_trend,
                    "d_trend":             d_trend,
                    "session":             _get_session(datetime.now(timezone.utc)),
                    "market_structure":    structure,
                }
                ml_win_prob = _ml_model.predict_win_prob(_ml_features)
            except Exception as exc:
                logger.debug("ML score-boost prediction failed for %s: %s", pair, exc)

        # ── Confluence ────────────────────────────────────────────────────────
        final_score = score_signal(ema_score, struct_score, pa_score, ml_win_prob)

        logger.info(
            "Signal %s %s dir=%s score=%d (EMA=%.0f Struct=%.0f PA=%.0f H4=%s D=%s ATR=%.1f pips)",
            pair, market, direction, final_score,
            ema_score, struct_score, pa_score, h4_trend, d_trend, _atr_pips,
        )

        if final_score < 40:
            _do_audit(pair=pair, timeframe=config.TIMEFRAMES["primary"],
                   direction=direction,
                   ema_score=ema_score, structure_score=struct_score, pa_score=pa_score,
                   confluence_score=final_score,
                   c1=_diag.get("c1"), c2_h4=_diag.get("c2"), c3_touch=_diag.get("c3"),
                   c4_cci_at_touch=_diag.get("c4"), c5_cci_recovery=_diag.get("c5"),
                   c6_macd=_diag.get("c6"),
                   cci_at_touch_val=_diag.get("cci_at_touch_val"),
                   cci_current=_diag.get("cci_current"),
                   macd_hist_val=_diag.get("macd_hist_val"),
                   h4_trend=h4_trend, d_trend=d_trend,
                   result="NO_SIGNAL", reject_reason=f"Score {final_score} < 40 discard threshold")
            return None

        # ── Compute SL/TP early — needed for chart overlay on WATCHING signals too ─
        _watch_sl = _sl_fn(pair, df_h1, direction)
        _watch_sl_pips = abs(entry - _watch_sl) / _pip_size
        _min_sl_pips = config.min_sl_pips_for(pair)
        if _watch_sl_pips < _min_sl_pips:
            _sl_dir  = -1 if direction == "long" else 1
            _watch_sl = round(entry + _sl_dir * _min_sl_pips * _pip_size, 5)
        _watch_tp = get_tp_levels(entry, _watch_sl, direction, pair)

        # Use WFO per-pair min_score when available, else fall back to global config
        _min_score = _wfo.get("min_score", config.MIN_CONFLUENCE_SCORE)
        if final_score < _min_score:
            status = "WATCHING" if final_score >= 50 else "SCANNING"
            logger.info("Signal %s scored %d — %s (no trade)", pair, final_score, status)
            _do_audit(pair=pair, timeframe=config.TIMEFRAMES["primary"],
                   direction=direction,
                   ema_score=ema_score, structure_score=struct_score, pa_score=pa_score,
                   confluence_score=final_score,
                   c1=_diag.get("c1"), c2_h4=_diag.get("c2"), c3_touch=_diag.get("c3"),
                   c4_cci_at_touch=_diag.get("c4"), c5_cci_recovery=_diag.get("c5"),
                   c6_macd=_diag.get("c6"),
                   cci_at_touch_val=_diag.get("cci_at_touch_val"),
                   cci_current=_diag.get("cci_current"),
                   macd_hist_val=_diag.get("macd_hist_val"),
                   h4_trend=h4_trend, d_trend=d_trend,
                   result=status,
                   reject_reason=f"Score {final_score} < MIN_CONFLUENCE_SCORE {config.MIN_CONFLUENCE_SCORE}")
            # Return partial signal — SL/TP included so chart overlay shows full setup
            return {
                "pair":             pair,
                "market":           market,
                "direction":        direction,
                "timeframe":        config.TIMEFRAMES["primary"],
                "score":            final_score,
                "ema_score":        ema_score,
                "structure_score":  struct_score,
                "pa_score":         pa_score,
                "entry":            float(df_h1["close"].iloc[-1]),
                "stop_loss":        _watch_sl,
                "tp_levels":        _watch_tp,
                "patterns":         patterns,
                "market_structure": structure,
                "bos_confirmed":    bos_ok,
                "h4_gate":          h4_gate_info,
                "h4_trend":         h4_trend,
                "d_trend":          d_trend,
                "watching":         True,
            }

        # ── ATR volatility gates ──────────────────────────────────────────────
        _atr_min_pips = config.atr_min_pips_for(pair)
        _atr_max_pips = config.atr_max_pips_for(pair)
        if _atr_pips < _atr_min_pips:
            reason = f"ATR_TOO_LOW: {_atr_pips:.1f} pips < min {_atr_min_pips}"
            logger.info("ATR gate blocked %s %s — %s", pair, direction, reason)
            _do_audit(pair=pair, timeframe=config.TIMEFRAMES["primary"],
                   direction=direction,
                   ema_score=ema_score, structure_score=struct_score, pa_score=pa_score,
                   confluence_score=final_score,
                   h4_trend=h4_trend, d_trend=d_trend,
                   result="BLOCKED", reject_reason=reason)
            return {
                "pair": pair, "market": market, "direction": direction,
                "timeframe": config.TIMEFRAMES["primary"],
                "score": final_score, "ema_score": ema_score,
                "structure_score": struct_score, "pa_score": pa_score,
                "entry": float(df_h1["close"].iloc[-1]),
                "stop_loss": None, "tp_levels": None, "patterns": [],
                "h4_gate": h4_gate_info, "h4_trend": h4_trend, "d_trend": d_trend,
                "watching": True, "gate_blocked": "ATR_TOO_LOW",
            }

        if _atr_pips > _atr_max_pips:
            reason = f"ATR_TOO_HIGH: {_atr_pips:.1f} pips > max {_atr_max_pips}"
            logger.info("ATR gate blocked %s %s — %s", pair, direction, reason)
            _do_audit(pair=pair, timeframe=config.TIMEFRAMES["primary"],
                   direction=direction,
                   ema_score=ema_score, structure_score=struct_score, pa_score=pa_score,
                   confluence_score=final_score,
                   h4_trend=h4_trend, d_trend=d_trend,
                   result="BLOCKED", reject_reason=reason)
            return {
                "pair": pair, "market": market, "direction": direction,
                "timeframe": config.TIMEFRAMES["primary"],
                "score": final_score, "ema_score": ema_score,
                "structure_score": struct_score, "pa_score": pa_score,
                "entry": float(df_h1["close"].iloc[-1]),
                "stop_loss": None, "tp_levels": None, "patterns": [],
                "h4_gate": h4_gate_info, "h4_trend": h4_trend, "d_trend": d_trend,
                "watching": True, "gate_blocked": "ATR_TOO_HIGH",
            }

        # ── Audit: triggered signal ───────────────────────────────────────────
        _do_audit(pair=pair, timeframe=config.TIMEFRAMES["primary"],
               direction=direction,
               ema_score=ema_score, structure_score=struct_score, pa_score=pa_score,
               confluence_score=final_score,
               c1=_diag.get("c1"), c2_h4=_diag.get("c2"), c3_touch=_diag.get("c3"),
               c4_cci_at_touch=_diag.get("c4"), c5_cci_recovery=_diag.get("c5"),
               c6_macd=_diag.get("c6"),
               cci_at_touch_val=_diag.get("cci_at_touch_val"),
               cci_current=_diag.get("cci_current"),
               macd_hist_val=_diag.get("macd_hist_val"),
               h4_trend=h4_trend, d_trend=d_trend,
               result="TRIGGERED")

        # ── Build full signal dict ────────────────────────────────────────────
        # SL/TP already computed above (shared with WATCHING path)
        stop_loss = _watch_sl
        tp_levels = _watch_tp

        # Indicator values for the learning engine log
        cci_s    = cci(df_h1["high"], df_h1["low"], df_h1["close"], config.CCI_PERIOD)
        mhist    = macd_histogram(df_h1["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
        c        = df_h1.iloc[-1]
        cr       = c["high"] - c["low"]

        short, mid, long_ = get_best_emas(pair, config.TIMEFRAMES["primary"], df_h1)
        at_sr = any(z["lower"] <= entry <= z["upper"] for z in sr_zones if z["tested"])

        return {
            "pair":                pair,
            "market":              market,
            "direction":           direction,
            "timeframe":           config.TIMEFRAMES["primary"],
            "h4_gate":             h4_gate_info,
            "h4_trend":            h4_trend,
            "d_trend":             d_trend,
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
            "atr":                 _atr_val,
            "atr_pips":            _atr_pips,
            "sr_zones":            sr_zones,
        }
