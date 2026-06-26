"""
Adaptive parameter tuning — adjusts per-pair signal thresholds based on recent trade outcomes.

Win rate tiers applied to the last ADAPTIVE_LOOKBACK_TRADES closed trades per pair:
  >= 60%   loosen  → cci ±10, band 0.35×ATR, macd 1-of-3
  40–60%   neutral → cci ±20, band 0.25×ATR, macd 1-of-3  (base / current defaults)
  30–40%   tighten → cci ±35, band 0.20×ATR, macd 2-of-3
  < 30%    strict  → cci ±50, band 0.15×ATR, macd 2-of-3

Re-fits only when at least ADAPTIVE_REFIT_EVERY_N new trades have closed since last fit,
preventing over-reaction to a single outcome.
"""
import json
import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_SAVE_PATH = Path(__file__).parent.parent / "data" / "adaptive_state.json"

BASE_PARAMS: dict = {
    "cci_threshold":    20,    # |CCI| required at touch (buy needs < -threshold, sell > +threshold)
    "touch_band_mult":  0.25,  # EMA touch band = this × ATR14
    "macd_bars_needed": 1,     # bars of correct MACD direction needed (of last 3)
}

_TIERS = [
    # (win_rate_min, win_rate_max_exclusive, params)
    (0.60, 1.01, {"cci_threshold": 10,  "touch_band_mult": 0.35, "macd_bars_needed": 1}),
    (0.40, 0.60, BASE_PARAMS),
    (0.30, 0.40, {"cci_threshold": 35,  "touch_band_mult": 0.20, "macd_bars_needed": 2}),
    (0.00, 0.30, {"cci_threshold": 50,  "touch_band_mult": 0.15, "macd_bars_needed": 2}),
]


class AdaptiveParams:
    """Per-pair adaptive signal parameter manager. Persisted to data/adaptive_state.json."""

    def __init__(self, save_path: Path = _SAVE_PATH):
        self._path = save_path
        self._state: dict = self._load()

    def _load(self) -> dict:
        try:
            if self._path.exists():
                return json.loads(self._path.read_text("utf-8"))
        except Exception as exc:
            logger.warning("AdaptiveParams._load failed: %s", exc)
        return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._state, indent=2), "utf-8")
        except Exception as exc:
            logger.warning("AdaptiveParams._save failed: %s", exc)

    def get(self, pair: str) -> dict:
        """Return current adaptive params for pair. Falls back to BASE_PARAMS until first fit."""
        if not getattr(config, "ADAPTIVE_PARAMS_ENABLED", True):
            return dict(BASE_PARAMS)
        return dict(self._state.get(pair, {}).get("params", BASE_PARAMS))

    def update(self, pair: str, closed_trades: list) -> bool:
        """
        Recalculate params for `pair` if enough new trades have closed since last fit.
        Returns True when params change.
        """
        if not getattr(config, "ADAPTIVE_PARAMS_ENABLED", True):
            return False

        lookback = getattr(config, "ADAPTIVE_LOOKBACK_TRADES", 20)
        every_n  = getattr(config, "ADAPTIVE_REFIT_EVERY_N",   5)

        pair_trades = [
            t for t in closed_trades
            if t.get("pair") == pair
            and t.get("close_reason") in ("sl", "tp1", "tp2", "tp3")
        ]
        n = len(pair_trades)
        if n < every_n:
            return False

        last_n = self._state.get(pair, {}).get("n_trades_at_last_fit", 0)
        if n - last_n < every_n:
            return False

        recent = pair_trades[-lookback:]
        wins   = sum(1 for t in recent if t.get("realised_pnl", 0) > 0)
        wr     = wins / len(recent)

        params = dict(BASE_PARAMS)
        for lo, hi, tier_params in _TIERS:
            if lo <= wr < hi:
                params = dict(tier_params)
                break

        prev_params = self._state.get(pair, {}).get("params", BASE_PARAMS)
        changed = params != prev_params

        self._state[pair] = {
            "params":               params,
            "win_rate":             round(wr, 4),
            "n_recent":             len(recent),
            "n_trades_at_last_fit": n,
        }
        self._save()

        logger.info(
            "AdaptiveParams %s  wr=%.0f%%  cci=±%d  band=%.2f×ATR  macd=%d-of-3%s",
            pair, wr * 100, params["cci_threshold"],
            params["touch_band_mult"], params["macd_bars_needed"],
            "  [CHANGED]" if changed else "",
        )
        return changed

    def summary(self) -> dict:
        """All pairs' current adaptive state — for dashboard display."""
        return {
            pair: {
                "win_rate_pct":     round(s.get("win_rate", 0) * 100, 1),
                "cci_threshold":    s["params"]["cci_threshold"],
                "touch_band_mult":  s["params"]["touch_band_mult"],
                "macd_bars_needed": s["params"]["macd_bars_needed"],
            }
            for pair, s in self._state.items()
        }


# Module-level singleton — import this wherever adaptive params are needed
adaptive_params = AdaptiveParams()
