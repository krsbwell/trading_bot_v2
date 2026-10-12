"""
Walk-Forward Optimizer — weekly parameter re-fitting per pair.

Searches over CCI period, MACD fast/slow/signal combos, and minimum confluence
score using the last WFO_TRAIN_BARS of primary-TF (M30) data. The best combination
(by composite profit-factor x win-rate) is saved to data/wfo_params.json and
read by the signal engine on every candle cycle.

Re-fits automatically every WFO_REFIT_DAYS (default 7). The weekly scheduler
in main.py calls run_all_pairs() in a background thread so the signal loop
is never blocked.

── Fit/holdout validation (2026-08-11) ────────────────────────────────────────
`run()` used to rank all 216 grid combos purely by in-sample composite score
on the whole training window and save whichever one scored highest, with only
a 5-trade floor — no out-of-sample check at all. That's overfitting by
construction: with 216 combos scored on the same data they're picked from,
some combo is almost guaranteed to look great by chance alone. Confirmed live:
GBP_CAD's saved fit (2026-07-30) claimed an 83% win rate off just 6 in-sample
trades; every real GBP_CAD trade placed under those params since has lost
except one. `run()` now splits the training window into an earlier FIT slice
and a later HOLDOUT slice (`WFO_HOLDOUT_FRAC`, default 0.3), ranks the top
`WFO_HOLDOUT_TOP_N` fit-set candidates, re-scores each on the untouched
holdout slice, and only saves the one that both clears `WFO_HOLDOUT_MIN_TRADES`
on holdout AND has holdout composite score > 0 (i.e. holdout PF > 0.5, since
composite = pf * (0.5 + wr) — see `_composite_score`). If nothing clears that
bar, the previous saved params (or config defaults, if never fitted) are left
in place rather than overwriting them with an unvalidated combo.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from itertools import product
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import config
from backtest.runner import run_backtest, confirm_tf_ratio

logger = logging.getLogger(__name__)

_SAVE_PATH = Path(__file__).parent.parent / "data" / "wfo_params.json"

# ── Dedicated, persistent WFO log (2026-10-12) ───────────────────────────────
# main.log is a 5MB x 20-backup RotatingFileHandler on the root logger, sized
# for day-to-day trading noise, not for surviving a week between WFO runs —
# confirmed 2026-10-12: at the 28-pair roster's current request volume, the
# entire ~100MB of retained main.log history covered less than one day, so
# Sunday 2026-10-11's run (if it logged at all) was already gone by Monday.
# This handler gives WFO's own messages a separate, slower-rotating home
# (daily rotation, 35 backups -> ~5 weeks) so a weekly job's output actually
# survives until someone looks at it.
#
# 2026-10-12 review follow-up (see bugs_wfo_logging_side_effects memory):
# this used to be installed unconditionally at module import time, right
# here. That meant merely `import engine.wfo_optimizer` -- including every
# test run that imports this module or anything that imports it, and any
# standalone script -- silently created logs/ and opened a handle on the
# real production logs/wfo.log, with no path override available (unlike
# _SAVE_PATH). On top of polluting test output, it created a genuine
# Windows multi-process hazard: the live bot process and a second process
# (a test run, a script) each holding their own TimedRotatingFileHandler on
# the identical path can collide during rotation (os.rename on a file the
# other process still has open raises WinError 32). Installation is now
# ONLY triggered by an explicit call -- main.py makes that call once,
# during its own logging initialization, before the WFO scheduler job can
# ever fire. Importing this module by itself no longer has this side
# effect at all (see test_importing_engine_wfo_optimizer_alone_does_not_install_handler).
_WFO_LOG_DIR  = Path(__file__).parent.parent / "logs"
_WFO_LOG_PATH = _WFO_LOG_DIR / "wfo.log"


def _install_wfo_file_handler(
    target_logger: logging.Logger, log_path: Path = _WFO_LOG_PATH,
) -> None:
    """Attach the dedicated WFO file handler to `target_logger`, unless one
    is already attached. Idempotent on purpose: this module's logger is a
    process-wide singleton (logging.getLogger caches by name), so calling
    this more than once in the same process -- e.g. a test calling it
    directly -- must not register the handler twice and double every log
    line to disk. The dedup check looks only for the marker below, not for
    a matching `log_path`, so a second call with a different path is still
    correctly treated as a no-op rather than adding a second file.

    `log_path` defaults to the real production logs/wfo.log, used by
    main.py's one production call site. Tests must always override it with
    a tmp_path-based file instead, so test runs never create, rotate, or
    write into the real file (see the tests in tests/test_wfo_optimizer.py
    following this module's "dedicated logs/wfo.log" marker comment).

    Attached ONLY to `target_logger`, not the root logger main.py
    configures -- this is an additional destination for WFO's own messages,
    not a replacement. `target_logger.propagate` is left exactly as the
    logging module defaults it (True), so every message still also reaches
    main.log/console exactly as it did before this handler existed.
    """
    for h in target_logger.handlers:
        if getattr(h, "_is_wfo_handler", False):
            return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        log_path, when="D", interval=1, backupCount=35, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
    ))
    handler._is_wfo_handler = True  # marker the dedup check above looks for
    target_logger.addHandler(handler)

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
        # Set by run() immediately after each _save() call, for run_all_pairs()
        # to read right afterward — see run()'s docstring and _save()'s return
        # value below. Not a public API; purely an internal handoff between
        # the two call sites in this class (confirmed the only two — _save()
        # has exactly one caller, run() has exactly one caller, both in this
        # file, 2026-10-09 investigation). None until the first run() call.
        self._last_save_ok: bool | None = None
        # Same handoff pattern as _last_save_ok above, set at each of run()'s
        # four failure-related `return None` sites so run_all_pairs() can
        # classify *why* a pair didn't validate, instead of lumping every
        # None result into one generic "validation_failed" bucket (that
        # conflated "insufficient data" with "failed out-of-sample
        # validation" — found 2026-10-09 review, see
        # bugs_wfo_batch_summary_reasons memory). Diagnostic only; does not
        # change run()'s return value or any decision logic.
        self._last_fail_reason: str | None = None

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            if self._path.exists():
                return json.loads(self._path.read_text("utf-8"))
        except Exception as exc:
            logger.warning("WFOOptimizer._load failed: %s", exc)
        return {}

    def _save(self) -> bool:
        """Write self._state to disk. Returns True on success, False on any
        write failure — the exception is still caught and logged exactly as
        before (unchanged behavior), this only ADDS a way for the caller to
        know the outcome instead of assuming success unconditionally. Fixed
        2026-10-09: previously the caller (run()) logged its "best params"
        success message regardless of whether this actually wrote to disk,
        because a write failure here was swallowed with no signal back —
        see run()'s success-log site for the fix."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._state, indent=2), "utf-8")
            return True
        except Exception as exc:
            logger.warning("WFOOptimizer._save failed: %s", exc)
            return False

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

    def _run_combo(self, pair: str, combo: dict, h1, h4, market: str) -> dict | None:
        """Run one backtest for one grid combo. Returns the raw result dict, or None on error."""
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
            return run_backtest(
                pair, h1, h4,
                min_score=combo["min_score"],
                market=market,
                adaptive=indicator_overrides,
            )
        except Exception as exc:
            logger.debug("WFO combo %s raised: %s", combo, exc)
            return None

    def run(self, pair: str, df_h1, df_h4, market: str = "forex") -> dict | None:
        """
        Grid-search over all parameter combinations on a FIT slice of the last
        WFO_TRAIN_BARS of data, then validate the top candidates on a held-out
        slice the grid search never saw. Only a combo that also holds up
        out-of-sample gets saved. Returns the winning param dict, or None if
        nothing validated (existing saved params, if any, are left untouched).

        self._last_save_ok is reset to None here, before any early return, so
        a caller reading it after this call never sees a stale value carried
        over from a different pair's earlier run() call that actually reached
        _save(). Only set to True/False further down, at the one point this
        function calls _save().

        self._last_fail_reason is reset to None the same way, then set to a
        specific string immediately before each of the four `return None`
        sites below — see each site's comment for the exact reason string.
        Diagnostic only, for run_all_pairs()'s batch summary; does not affect
        this function's return value.
        """
        self._last_save_ok = None
        self._last_fail_reason = None
        train_bars = getattr(config, "WFO_TRAIN_BARS", 1440)
        holdout_frac = getattr(config, "WFO_HOLDOUT_FRAC", 0.3)
        top_n        = getattr(config, "WFO_HOLDOUT_TOP_N", 8)
        min_hold_trd = getattr(config, "WFO_HOLDOUT_MIN_TRADES", 3)

        train_h1 = df_h1.tail(train_bars).copy()
        train_h4 = df_h4.tail(max(train_bars // confirm_tf_ratio(pair), 50)).copy()

        if len(train_h1) < 120:
            logger.warning("WFO %s: insufficient data (%d bars)", pair, len(train_h1))
            self._last_fail_reason = "insufficient_data"
            return None

        split = int(len(train_h1) * (1 - holdout_frac))
        fit_h1, hold_h1 = train_h1.iloc[:split], train_h1.iloc[split:]
        h4_split = int(len(train_h4) * (1 - holdout_frac))
        fit_h4, hold_h4 = train_h4.iloc[:h4_split], train_h4.iloc[h4_split:]

        if len(hold_h1) < 40:
            logger.warning("WFO %s: holdout slice too small (%d bars) — skipping refit",
                            pair, len(hold_h1))
            self._last_fail_reason = "insufficient_holdout"
            return None

        logger.info("WFO %s: searching %d combinations over %d fit bars (%d holdout) ...",
                    pair, len(_PARAM_COMBINATIONS), len(fit_h1), len(hold_h1))

        # ── Stage 1: rank all combos in-sample on the fit slice only ──────────
        fit_scored = []
        for combo in _PARAM_COMBINATIONS:
            result = self._run_combo(pair, combo, fit_h1, fit_h4, market)
            if result is None:
                continue
            s = _composite_score(result)
            if s > 0:
                fit_scored.append((s, combo, result))

        if not fit_scored:
            logger.warning("WFO %s: no combination found >= 5 trades in-sample", pair)
            self._last_fail_reason = "no_in_sample_candidate"
            return None

        fit_scored.sort(key=lambda t: t[0], reverse=True)

        # ── Stage 2: re-score the top N fit candidates on the untouched holdout ──
        best_hold_score  = -1.0
        best_params      = None
        best_hold_result = None
        best_fit_score   = None

        for fit_score, combo, _fit_result in fit_scored[:top_n]:
            hold_result = self._run_combo(pair, combo, hold_h1, hold_h4, market)
            if hold_result is None:
                continue
            if hold_result.get("total_trades", 0) < min_hold_trd:
                continue
            hs = _composite_score(hold_result)
            if hs > best_hold_score:
                best_hold_score  = hs
                best_params      = dict(combo)
                best_hold_result = hold_result
                best_fit_score   = fit_score

        if best_params is None or best_hold_score <= 0:
            logger.warning(
                "WFO %s: no in-sample candidate held up out-of-sample "
                "(need >= %d holdout trades and holdout PF > 0.5) — keeping existing params",
                pair, min_hold_trd,
            )
            self._last_fail_reason = "failed_oos"
            return None

        with self._lock:
            self._state[pair] = {
                "params":          best_params,
                "fitted_at":       datetime.now(timezone.utc).isoformat(),
                "train_bars":      len(train_h1),
                "score":           round(best_fit_score, 3),
                "win_rate":        best_hold_result.get("win_rate", 0),
                "total_trades":    best_hold_result.get("total_trades", 0),
                "total_pnl":       best_hold_result.get("total_pnl", 0),
                "holdout_score":   round(best_hold_score, 3),
                "holdout_trades":  best_hold_result.get("total_trades", 0),
                "holdout_bars":    len(hold_h1),
            }
            self._last_save_ok = self._save()

        # 2026-10-09: this used to log unconditionally, claiming "best" found
        # regardless of whether _save() actually persisted it — a disk-write
        # failure was caught and warned about inside _save() but never
        # stopped this message from implying success. Now it reports exactly
        # what happened: the validated params are always correct in-memory
        # (self._state[pair] above is set either way, so get_params() still
        # returns them for this process's lifetime), but a failed write means
        # they will NOT survive a restart, which is materially different from
        # genuine success and the log must say so, not paper over it.
        if not self._last_save_ok:
            logger.warning(
                "WFO %s: best params found (min_score=%d CCI=%d MACD=%d/%d/%d) "
                "but FAILED TO SAVE to %s — validated results are NOT persisted "
                "to disk and will be lost on restart (see the _save failed "
                "warning above for the write error)",
                pair, best_params["min_score"], best_params["CCI_PERIOD"],
                best_params["MACD_FAST"], best_params["MACD_SLOW"], best_params["MACD_SIGNAL"],
                self._path,
            )
            return best_params

        logger.info(
            "WFO %s: best  min_score=%d  CCI=%d  MACD=%d/%d/%d  "
            "cci_thr=%d  lookback=%d  adx_thr=%d  "
            "fit_score=%.2f  holdout_score=%.2f  holdout_WR=%.0f%%  holdout_trades=%d  holdout_PnL=$%.2f",
            pair,
            best_params["min_score"],  best_params["CCI_PERIOD"],
            best_params["MACD_FAST"],  best_params["MACD_SLOW"],  best_params["MACD_SIGNAL"],
            best_params["cci_threshold"], best_params["touch_lookback"],
            best_params["adx_threshold"],
            best_fit_score, best_hold_score,
            best_hold_result.get("win_rate", 0) * 100,
            best_hold_result.get("total_trades", 0),
            best_hold_result.get("total_pnl", 0),
        )
        return best_params

    def run_all_pairs(self, get_candles_fn, pairs: list, market: str = "forex") -> None:
        """
        Re-fit all pairs that need it. Designed to run in a background thread.
        get_candles_fn(pair, granularity, count) -> pd.DataFrame

        Logs a batch summary after the loop (2026-10-09 — see
        bugs_wfo_logging_gaps memory) so a run's outcome is auditable without
        reconstructing it from scattered per-pair lines. Counters are
        incremented at the exact existing control-flow point for each
        outcome — no new branches, no change to what gets skipped, retried,
        or saved. "Validated" and "saved" are counted separately per the
        self._last_save_ok distinction added to run()/_save() above: a
        pair whose params validated out-of-sample but failed to write to
        disk must not be counted the same as a genuine success.

        A `run()` result of None is further split by self._last_fail_reason
        (2026-10-09 review follow-up — bugs_wfo_batch_summary_reasons memory)
        into the four distinct causes run() can return None for, instead of
        one generic bucket that silently conflated "insufficient data" and
        "insufficient holdout" with a genuine failed out-of-sample
        validation. If _last_fail_reason is ever None or an unrecognized
        string at that point — which should never happen since run() sets it
        at every one of its `return None` sites, but diagnostic code must not
        assume its own invariants hold — it's counted as `unclassified`
        rather than guessed into one of the four named buckets.

        2026-10-12 update: an unexpected exception raised outside the
        per-pair try/except below (the only such call is should_refit() at
        the top of the loop) is now logged here before being re-raised —
        previously it propagated silently to crash_logging.py's
        threading.excepthook with no WFO-specific record at all. It still
        propagates exactly as before (not suppressed, not swallowed into a
        counter) and the batch-summary log below still does not run in that
        case, since the function still exits via the exception — this adds
        a log line, it does not change what happens afterward.
        """
        train_bars = getattr(config, "WFO_TRAIN_BARS", 720)
        considered  = len(pairs)
        logger.info("WFO batch starting: %d pairs to consider", considered)
        skipped_fresh = 0
        insufficient_data = 0
        exceptions = 0
        validated_and_saved = 0
        validated_but_save_failed = 0
        # Split out of the old single `validation_failed` bucket — one
        # counter per self._last_fail_reason string set inside run(), plus
        # `unclassified` as a safety net (see docstring above).
        fail_insufficient_data     = 0
        fail_insufficient_holdout  = 0
        fail_no_in_sample_candidate = 0
        fail_failed_oos            = 0
        fail_unclassified          = 0
        try:
            for pair in pairs:
                if not self.should_refit(pair):
                    logger.info("WFO %s: params still fresh — skipping", pair)
                    skipped_fresh += 1
                    continue
                try:
                    df_h1 = get_candles_fn(pair, config.primary_tf_for(pair), train_bars + 60)
                    df_h4 = get_candles_fn(pair, config.confirm_tf_for(pair),
                                            train_bars // confirm_tf_ratio(pair) + 20)
                    if df_h1 is None or len(df_h1) < 120:
                        logger.warning("WFO %s: candle fetch returned insufficient data", pair)
                        insufficient_data += 1
                        continue
                    result = self.run(pair, df_h1, df_h4, market=market)
                    if result is None:
                        reason = self._last_fail_reason
                        if reason == "insufficient_data":
                            fail_insufficient_data += 1
                        elif reason == "insufficient_holdout":
                            fail_insufficient_holdout += 1
                        elif reason == "no_in_sample_candidate":
                            fail_no_in_sample_candidate += 1
                        elif reason == "failed_oos":
                            fail_failed_oos += 1
                        else:
                            logger.error(
                                "WFO %s: run() returned None but _last_fail_reason=%r "
                                "is missing or unrecognized — counting as unclassified "
                                "rather than guessing a category",
                                pair, reason,
                            )
                            fail_unclassified += 1
                    elif self._last_save_ok:
                        validated_and_saved += 1
                    else:
                        validated_but_save_failed += 1
                except Exception as exc:
                    logger.error("WFO %s failed: %s", pair, exc, exc_info=True)
                    exceptions += 1
        except Exception as exc:
            # Only reachable from code in this loop OUTSIDE the per-pair
            # try/except above -- i.e. should_refit() itself raising. Log
            # it against the WFO logger (so it lands in wfo.log, not just
            # crash_logging's generic thread-level record) and re-raise
            # unchanged: no suppression, no new return value, no counter
            # incremented for the pair that triggered it or any pair after
            # it in the list -- behavior here is identical to before this
            # change except for this one added log line.
            logger.error(
                "WFO batch: unexpected exception outside per-pair handling "
                "(most likely should_refit()): %s", exc, exc_info=True,
            )
            raise

        attempted = considered - skipped_fresh
        logger.info(
            "WFO batch complete: %d considered, %d skipped (fresh), %d attempted "
            "— of those: %d validated+saved, %d validated but FAILED TO SAVE, "
            "%d insufficient training data, %d insufficient holdout data, "
            "%d no in-sample candidate, %d failed out-of-sample validation, "
            "%d unclassified failure, %d insufficient data (candle fetch), "
            "%d raised an exception",
            considered, skipped_fresh, attempted,
            validated_and_saved, validated_but_save_failed,
            fail_insufficient_data, fail_insufficient_holdout,
            fail_no_in_sample_candidate, fail_failed_oos,
            fail_unclassified, insufficient_data, exceptions,
        )

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
