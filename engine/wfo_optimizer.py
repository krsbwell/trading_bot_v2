"""
Walk-Forward Optimizer — weekly parameter re-fitting per pair.

Searches over CCI period, MACD fast/slow/signal combos, and minimum confluence
score using the last WFO_TRAIN_BARS of primary-TF (M30) data. The best combination
(by composite profit-factor x win-rate) is saved to data/wfo_params.json and
read by the signal engine on every candle cycle.

Re-fits automatically every WFO_REFIT_DAYS (default 7). The weekly scheduler
in main.py calls run_all_pairs() in a background thread so the signal loop
is never blocked.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import config
from backtest.runner import run_backtest, confirm_tf_ratio

logger = logging.getLogger(__name__)

_SAVE_PATH = Path(__file__).parent.parent / "data" / "wfo_params.json"

# ── Parameter search grid ─────────────────────────────────────────────────────
# 3 x 3 x 2 x 2 x 2 x 3 = 216 combinations per pair — ~5 min background job
_MIN_SCORES      = [50, 55, 62]
_CCI_PERIODS     = [14, 20, 28]
_MACD_COMBOS     = [
    (12, 26, 9),   # standard
    (8,  21, 5),   # faster response
]
_CCI_THRESHOLDS  = [15, 30]   # CCI oversold/overbought required at EMA touch (default 20)
_TOUCH_LOOKBACKS = [30, 50]   # M30 bars to search back for EMA touch (default 40)
_ADX_THRESHOLDS  = [22, 28, 33]  # ADX regime gate — below = ranging (trade); above = skip

_PARAM_COMBINATIONS = [
    {
        "min_score":      ms,
        "CCI_PERIOD":     cci,
        "MACD_FAST":      mf,
        "MACD_SLOW":      msl,
        "MACD_SIGNAL":    msig,
        "cci_threshold":  ct,
        "touch_lookback": tl,
        "adx_threshold":  adxt,
    }
    for ms, cci, (mf, msl, msig), ct, tl, adxt
    in product(_MIN_SCORES, _CCI_PERIODS, _MACD_COMBOS, _CCI_THRESHOLDS, _TOUCH_LOOKBACKS, _ADX_THRESHOLDS)
]


def _composite_score(result: dict) -> float:
    """
    Rank a backtest run. Returns -1 when < 5 trades (too few to be reliable).
    Composite = profit_factor x (0.5 + win_rate) — rewards both edge and frequency.
    """
    if result.get("error") or result.get("total_trades", 0) < 5:
        return -1.0
    trades  = result.get("trades", [])
    gross_p = sum(t["realised_pnl"] for t in trades if t["realised_pnl"] > 0)
    gross_l = abs(sum(t["realised_pnl"] for t in trades if t["realised_pnl"] < 0))
    pf      = (gross_p / gross_l) if gross_l > 0 else (gross_p or 0.0)
    wr      = result.get("win_rate", 0.0)
    return pf * (0.5 + wr)


class WFOOptimizer:
    """
    Per-pair walk-forward parameter optimizer.
    Use the module-level singleton `wfo_optimizer` rather than constructing directly.
    """

    def __init__(self, save_path: Path = _SAVE_PATH):
        self._path  = save_path
        self._lock  = threading.Lock()
        self._state: dict = self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            if self._path.exists():
                return json.loads(self._path.read_text("utf-8"))
        except Exception as exc:
            logger.warning("WFOOptimizer._load failed: %s", exc)
        return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._state, indent=2), "utf-8")
        except Exception as exc:
            logger.warning("WFOOptimizer._save failed: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def should_refit(self, pair: str) -> bool:
        """True when no params saved yet, or last fit was > WFO_REFIT_DAYS ago."""
        refit_days = getattr(config, "WFO_REFIT_DAYS", 7)
        last_str   = self._state.get(pair, {}).get("fitted_at")
        if not last_str:
            return True
        try:
            last = datetime.fromisoformat(last_str)
            return (datetime.now(timezone.utc) - last).days >= refit_days
        except Exception:
            return True

    def get_params(self, pair: str) -> dict:
        """
        Return current best params for pair. Returns empty dict if not yet fitted,
        which causes the signal engine to fall back to config defaults.
        """
        with self._lock:
            return dict(self._state.get(pair, {}).get("params", {}))

    def run(self, pair: str, df_h1, df_h4, market: str = "forex") -> dict | None:
        """
        Grid-search over all parameter combinations using the last WFO_TRAIN_BARS
        of data. Saves the best params to disk and returns the winning param dict,
        or None if no combination produced >= 5 trades.
        """
        train_bars = getattr(config, "WFO_TRAIN_BARS", 1440)
        train_h1   = df_h1.tail(train_bars).copy()
        train_h4   = df_h4.tail(max(train_bars // confirm_tf_ratio(pair), 50)).copy()

        if len(train_h1) < 120:
            logger.warning("WFO %s: insufficient data (%d bars)", pair, len(train_h1))
            return None

        logger.info("WFO %s: searching %d combinations over %d bars ...",
                    pair, len(_PARAM_COMBINATIONS), len(train_h1))

        best_score  = -1.0
        best_params = None
        best_result = None

        for combo in _PARAM_COMBINATIONS:
            indicator_overrides = {
                "CCI_PERIOD":     combo["CCI_PERIOD"],
                "MACD_FAST":      combo["MACD_FAST"],
                "MACD_SLOW":      combo["MACD_SLOW"],
                "MACD_SIGNAL":    combo["MACD_SIGNAL"],
                "cci_threshold":  combo["cci_threshold"],
                "touch_lookback": combo["touch_lookback"],
                "adx_threshold":  combo["adx_threshold"],
            }
            try:
                result = run_backtest(
                    pair, train_h1, train_h4,
                    min_score=combo["min_score"],
                    market=market,
                    adaptive=indicator_overrides,
                )
            except Exception as exc:
                logger.debug("WFO combo %s raised: %s", combo, exc)
                continue

            s = _composite_score(result)
            if s > best_score:
                best_score  = s
                best_params = dict(combo)
                best_result = result

        if best_params is None:
            logger.warning("WFO %s: no valid combination found (all < 5 trades)", pair)
            return None

        with self._lock:
            self._state[pair] = {
                "params":       best_params,
                "fitted_at":    datetime.now(timezone.utc).isoformat(),
                "train_bars":   len(train_h1),
                "score":        round(best_score, 3),
                "win_rate":     best_result.get("win_rate", 0),
                "total_trades": best_result.get("total_trades", 0),
                "total_pnl":    best_result.get("total_pnl", 0),
            }
            self._save()

        logger.info(
            "WFO %s: best  min_score=%d  CCI=%d  MACD=%d/%d/%d  "
            "cci_thr=%d  lookback=%d  adx_thr=%d  "
            "score=%.2f  WR=%.0f%%  trades=%d  PnL=$%.2f",
            pair,
            best_params["min_score"],  best_params["CCI_PERIOD"],
            best_params["MACD_FAST"],  best_params["MACD_SLOW"],  best_params["MACD_SIGNAL"],
            best_params["cci_threshold"], best_params["touch_lookback"],
            best_params["adx_threshold"],
            best_score,
            best_result.get("win_rate", 0) * 100,
            best_result.get("total_trades", 0),
            best_result.get("total_pnl", 0),
        )
        return best_params

    def run_all_pairs(self, get_candles_fn, pairs: list, market: str = "forex") -> None:
        """
        Re-fit all pairs that need it. Designed to run in a background thread.
        get_candles_fn(pair, granularity, count) -> pd.DataFrame
        """
        train_bars = getattr(config, "WFO_TRAIN_BARS", 720)
        for pair in pairs:
            if not self.should_refit(pair):
                logger.info("WFO %s: params still fresh — skipping", pair)
                continue
            try:
                df_h1 = get_candles_fn(pair, config.TIMEFRAMES["primary"], train_bars + 60)
                df_h4 = get_candles_fn(pair, config.confirm_tf_for(pair),
                                        train_bars // confirm_tf_ratio(pair) + 20)
                if df_h1 is None or len(df_h1) < 120:
                    logger.warning("WFO %s: candle fetch returned insufficient data", pair)
                    continue
                self.run(pair, df_h1, df_h4, market=market)
            except Exception as exc:
                logger.error("WFO %s failed: %s", pair, exc, exc_info=True)

    def summary(self) -> dict:
        """All pairs' fitted params — for dashboard display."""
        with self._lock:
            return {
                pair: {
                    "fitted_at":     s.get("fitted_at", ""),
                    "min_score":     s["params"].get("min_score"),
                    "CCI_PERIOD":    s["params"].get("CCI_PERIOD"),
                    "MACD":          (
                        f"{s['params'].get('MACD_FAST')}/"
                        f"{s['params'].get('MACD_SLOW')}/"
                        f"{s['params'].get('MACD_SIGNAL')}"
                    ),
                    "adx_threshold": s["params"].get("adx_threshold"),
                    "win_rate_pct":  round(s.get("win_rate", 0) * 100, 1),
                    "total_trades":  s.get("total_trades", 0),
                    "score":         s.get("score", 0),
                }
                for pair, s in self._state.items()
            }


# Module-level singleton
wfo_optimizer = WFOOptimizer()
