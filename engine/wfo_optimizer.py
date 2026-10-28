"""
Walk-Forward Optimizer — weekly parameter re-fitting per pair.

Searches over CCI period, MACD fast/slow/signal combos, and minimum confluence
score using the last WFO_TRAIN_BARS of primary-TF (M30) data. The best combination
(by composite profit-factor x win-rate) is saved to data/wfo_params.json and
read by the signal engine on every candle cycle.

Re-fits once per scheduled weekly cycle (WFO_REFIT_CYCLES, default 1; Phase 5D-5). The weekly scheduler
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
holdout slice, and only saves a candidate that is ELIGIBLE on holdout: at least
`WFO_HOLDOUT_MIN_TRADES` (5) completed trades AND holdout profit factor
>= `WFO_HOLDOUT_MIN_PF` (1.0) -- two explicit checks made before ranking (Phase
5D-2, 2026-10-27; see `_holdout_eligibility`). Among eligible candidates the
highest composite (pf * (0.5 + wr), `_composite_value`) wins. If nothing is
eligible, the previous saved params (or config defaults, if never fitted) are
left in place rather than overwriting them with an unvalidated combo.
"""
import json
import logging
import math
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from itertools import product
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from oandapyV20.exceptions import V20Error

import config
from backtest.runner import run_backtest, confirm_tf_ratio, confirm_candles_closed_at

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


# In-sample floor used by _composite_score (Stage 1 fit ranking). UNCHANGED since
# before Phase 5D-2: in-sample behaviour must not move. Holdout does NOT use it.
_FIT_MIN_TRADES = 5


def _composite_score(result: dict) -> float:
    """
    Rank a backtest run. Returns -1 when < 5 trades (too few to be reliable).
    Composite = profit_factor x (0.5 + win_rate) — rewards both edge and frequency.

    IN-SAMPLE (Stage 1) only: the hidden five-trade floor lives here, so holdout
    eligibility must not be derived from this function -- see _holdout_eligibility.
    """
    if result.get("error") or result.get("total_trades", 0) < _FIT_MIN_TRADES:
        return -1.0
    trades  = result.get("trades", [])
    gross_p = sum(t["realised_pnl"] for t in trades if t["realised_pnl"] > 0)
    gross_l = abs(sum(t["realised_pnl"] for t in trades if t["realised_pnl"] < 0))
    pf      = (gross_p / gross_l) if gross_l > 0 else (gross_p or 0.0)
    wr      = result.get("win_rate", 0.0)
    return pf * (0.5 + wr)


# ── Holdout validation policy (2026-10-27, Phase 5D-2) ───────────────────────
# Before: holdout needed `total_trades >= WFO_HOLDOUT_MIN_TRADES (3)` and then
# `_composite_score > 0`. That score hides a second five-trade floor (3-4 trade
# candidates were silently dropped) and `> 0` only means "at least one winner":
# a 5-trade PF-0.36 set scored +0.25 and would have been SAVED; the "PF > 0.5"
# in the old warning text was never enforced.
# Now: two explicit, independent eligibility checks, evaluated BEFORE ranking.
#   trades_ok = completed holdout trades (`total_trades`) >= WFO_HOLDOUT_MIN_TRADES
#   pf_ok     = holdout PF >= WFO_HOLDOUT_MIN_PF  (_holdout_pf, below)
# Only eligible candidates are ranked (by _composite_value, same formula as
# _composite_score minus the floor). entry_signals / addon_entries / forced_closes
# are reported next to every verdict but gate nothing.
_PF_TOLERANCE = 1e-9   # float-noise guard for the PF boundary ONLY (PF 0.9999999999999998 == 1.0)


def _composite_value(result: dict) -> float:
    """pf * (0.5 + win_rate), exactly _composite_score's arithmetic (including its
    legacy pf for no-loss sets = gross profit in dollars, deliberately preserved so
    candidate ranking is unchanged) but WITHOUT the in-sample trade floor. Used to
    rank candidates that already passed explicit holdout eligibility."""
    if result.get("error"):
        return 0.0
    trades  = result.get("trades", []) or []
    gross_p = sum(t["realised_pnl"] for t in trades if t["realised_pnl"] > 0)
    gross_l = abs(sum(t["realised_pnl"] for t in trades if t["realised_pnl"] < 0))
    pf      = (gross_p / gross_l) if gross_l > 0 else (gross_p or 0.0)
    return pf * (0.5 + result.get("win_rate", 0.0))


def _holdout_pf(result: dict) -> float:
    """Profit factor for holdout ELIGIBILITY (not ranking, not the legacy display PF):
        gross_profit / gross_loss              when there are losses;
        +inf                                   no losses, some profit;
        NaN (undefined => never eligible)      no profit AND no loss, no trades, or
                                               any missing / malformed / non-finite
                                               trade P&L -- invalid input is rejected,
                                               never treated as profitable.
    """
    try:
        trades = result.get("trades")
        if not isinstance(trades, (list, tuple)):
            return math.nan
        gross_p = gross_l = 0.0
        for t in trades:
            pnl = float(t["realised_pnl"])
            if not math.isfinite(pnl):
                return math.nan
            if pnl > 0:
                gross_p += pnl
            elif pnl < 0:
                gross_l -= pnl
        if gross_l > 0:
            return gross_p / gross_l
        return math.inf if gross_p > 0 else math.nan
    except Exception:
        return math.nan


def _trades_meet(n, min_trades) -> bool:
    return (isinstance(n, (int, float)) and not isinstance(n, bool)
            and n == n and n >= min_trades)


def _pf_meets(pf, min_pf) -> bool:
    return pf == math.inf or (pf == pf and pf >= min_pf - _PF_TOLERANCE)   # NaN -> False


def _policy_thresholds(min_trades=None, min_pf=None):
    """(min completed holdout trades, min holdout PF) from config unless given."""
    if min_trades is None:
        min_trades = getattr(config, "WFO_HOLDOUT_MIN_TRADES", 5)
    if min_pf is None:
        min_pf = getattr(config, "WFO_HOLDOUT_MIN_PF", 1.0)
    return max(1, int(min_trades)), max(0.0, float(min_pf))


def _holdout_eligibility(result, min_trades: int, min_pf: float) -> tuple:
    """(trades_ok, pf_ok) for one holdout result. Fails closed: any error, a
    non-dict, or an error result is (False, False) -- a bug can make a candidate
    ineligible, never eligible."""
    try:
        if not isinstance(result, dict) or result.get("error"):
            return (False, False)
        return (bool(_trades_meet(result.get("total_trades", 0), min_trades)),
                bool(_pf_meets(_holdout_pf(result), min_pf)))
    except Exception:
        return (False, False)


def _result_pf(result: dict) -> float:
    """Legacy profit factor (no 5-trade floor): gross profit in DOLLARS when there
    are no losses. Kept for in-sample display / ranking parity only -- holdout
    eligibility uses _holdout_pf."""
    trades  = result.get("trades", []) or []
    gross_p = sum(t["realised_pnl"] for t in trades if t["realised_pnl"] > 0)
    gross_l = abs(sum(t["realised_pnl"] for t in trades if t["realised_pnl"] < 0))
    return (gross_p / gross_l) if gross_l > 0 else (gross_p or 0.0)


def _verdict_from_flags(flags: list) -> str:
    """flags: [(trades_ok, pf_ok)] for holdout runs that returned a result.
    Why nothing was eligible:
      no_holdout_results  every holdout backtest errored
      both                no candidate has enough trades AND none has enough PF
      too_few_trades      some candidates have enough PF, none has enough trades
      low_pf              some candidates have enough trades, none has enough PF
      mixed               trades and PF are each satisfied, but never by the same candidate
    ("eligible" if one candidate satisfies both -- not a rejection.)"""
    if not flags:
        return "no_holdout_results"
    if any(t and p for t, p in flags):
        return "eligible"
    any_t, any_p = any(t for t, _ in flags), any(p for _, p in flags)
    if not any_t and not any_p:
        return "both"
    if not any_t:
        return "too_few_trades"
    if not any_p:
        return "low_pf"
    return "mixed"


def _classify_oos_rejection(cands: list, min_trades=None, min_pf=None) -> str:
    """cands: [(trades, holdout_pf, composite_value)] for holdout runs that returned a result."""
    mt, mp = _policy_thresholds(min_trades, min_pf)
    return _verdict_from_flags([(_trades_meet(t, mt), _pf_meets(pf, mp)) for t, pf, _v in cands])


def _min_med_max(values: list) -> str:
    values = [v for v in values if v == v]       # drop NaN (undefined PF)
    if not values:
        return "n/a"
    s = sorted(values)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return f"{s[0]:.4g}/{med:.4g}/{s[-1]:.4g}"


def _format_oos_diagnostic(pair, tfs, best_fit, fit_none, hold_none, cands,
                           min_trades, min_pf, extra=None) -> str:
    trades = [t for t, _pf, _v in cands]
    pfs    = [pf for _t, pf, _v in cands]
    flags  = [(_trades_meet(t, min_trades), _pf_meets(pf, min_pf)) for t, pf, _v in cands]
    n_t    = sum(1 for t, _ in flags if t)
    n_p    = sum(1 for _, p in flags if p)
    n_el   = sum(1 for t, p in flags if t and p)
    f_trd  = sum(1 for t, p in flags if not t and p)        # fails the trade minimum only
    f_pf   = sum(1 for t, p in flags if t and not p)        # fails the PF minimum only
    f_both = sum(1 for t, p in flags if not t and not p)
    fit_tr, fit_pf = best_fit if best_fit else ("n/a", "n/a")
    return (
        f"WFO {pair} OOS-reject diagnostic: tf={tfs[0]}/{tfs[1]} strategy=EMA-Bounce | "
        f"verdict={_verdict_from_flags(flags)} | "
        f"policy: min_trades={min_trades} min_pf={min_pf:g} | "
        f"best in-sample: trades={fit_tr} PF={fit_pf} | "
        f"holdout candidates evaluated={len(cands)} (None={hold_none}) | "
        f"trades_ok={n_t} pf_ok={n_p} eligible={n_el} | "
        f"failed: trades-only={f_trd} pf-only={f_pf} both={f_both} | "
        f"holdout trades min/med/max={_min_med_max(trades)} | "
        f"holdout PF min/med/max={_min_med_max(pfs)} | "
        f"in-sample combos returning None: {fit_none}"
        + _format_entry_breakdown(extra)
    )


def _format_entry_breakdown(extra) -> str:
    """Phase 5D-1B: independent entries vs pyramid add-ons vs end-of-data forced
    closes across the holdout candidates. `extra` = [(entry_signals,
    addon_entries, forced_closes)], values None for results that lack them.
    Display only -- never read back by selection."""
    if extra is None:
        return ""
    cols = []
    for idx, name in enumerate(("entry_signals", "addon_entries", "forced_closes")):
        vals = [e[idx] for e in extra if e[idx] is not None]
        cols.append(f"{name} min/med/max={_min_med_max(vals)}")
    return " | holdout " + ", ".join(cols)


# ── Transient candle-fetch retries (2026-10-27, Phase 5D-3) ──────────────────
# The 10-25 run lost EUR_CAD and GBP_JPY (10-18: GBP_AUD) to a single Cloudflare
# 504 / connection reset each, seconds after the pair started -- a whole pair
# forfeited for a one-off network blip. Retries live HERE, around the two
# get_candles_fn calls in run_all_pairs() only: read-only GETs, no order path,
# and the shared OANDA connector (live scans, execution) is untouched. A pair's
# optimisation is never retried or restarted -- only the failed fetch is.
_FETCH_MAX_ATTEMPTS    = 3                # total attempts per fetch, never more
_FETCH_BACKOFF_SECS    = (5.0, 20.0)      # waits before attempt 2 and attempt 3
_FETCH_JITTER_FRAC     = 0.2              # each wait scaled by U(0.8, 1.2)
_RETRYABLE_HTTP_STATUS = frozenset({502, 503, 504})


def _retry_sleep(seconds: float) -> None:
    time.sleep(seconds)          # blocks only the wfo-refit daemon thread


def _retry_jitter(lo: float, hi: float) -> float:
    return random.uniform(lo, hi)


def _exc_chain(exc: BaseException, limit: int = 12) -> list:
    """exc plus everything reachable via __cause__/__context__/args (requests
    wraps urllib3's ProtocolError, which wraps the raw ConnectionResetError
    only in .args). Cycle-safe and bounded."""
    seen, out, stack = set(), [], [exc]
    while stack and len(out) < limit:
        e = stack.pop(0)
        if not isinstance(e, BaseException) or id(e) in seen:
            continue
        seen.add(id(e))
        out.append(e)
        stack.extend([e.__cause__, e.__context__, *e.args])
    return out


def _transient_fetch_reason(exc: BaseException) -> str | None:
    """Short reason string if `exc` is an APPROVED transient fetch failure
    (HTTP 502/503/504, connection reset, timeout), else None -- including for
    auth (401/403), invalid requests (400/404), 429, 500, refused
    connections, DNS and SSL errors, which are never retried."""
    if isinstance(exc, V20Error):
        try:
            code = int(exc.code)
        except (TypeError, ValueError):
            return None
        return f"HTTP {code}" if code in _RETRYABLE_HTTP_STATUS else None
    chain = _exc_chain(exc)
    if any(isinstance(e, ConnectionResetError) for e in chain):
        return "connection reset"
    if any(isinstance(e, (requests.exceptions.Timeout, TimeoutError)) for e in chain):
        return "timeout"
    return None


def _fetch_with_retry(get_candles_fn, pair: str, granularity: str, count: int):
    """get_candles_fn(pair, granularity, count) with bounded retry on transient
    errors. Non-retryable errors and the final failed attempt re-raise the
    ORIGINAL exception object, so run_all_pairs()'s existing handler (error log
    + 'raised an exception' counter) behaves exactly as before."""
    for attempt in range(1, _FETCH_MAX_ATTEMPTS + 1):
        try:
            result = get_candles_fn(pair, granularity, count)
        except Exception as exc:
            reason = _transient_fetch_reason(exc)
            if reason is None:
                raise
            if attempt >= _FETCH_MAX_ATTEMPTS:
                logger.warning("WFO %s: %s candle fetch giving up after %d attempts (last: %s)",
                               pair, granularity, attempt, reason)
                raise
            delay = _FETCH_BACKOFF_SECS[attempt - 1] * _retry_jitter(
                1 - _FETCH_JITTER_FRAC, 1 + _FETCH_JITTER_FRAC)
            logger.warning("WFO %s: %s candle fetch failed (attempt %d/%d, %s) — retrying in %.1fs",
                           pair, granularity, attempt, _FETCH_MAX_ATTEMPTS, reason, delay)
            _retry_sleep(delay)
            continue
        if attempt > 1:
            logger.info("WFO %s: %s candle fetch recovered on attempt %d/%d",
                        pair, granularity, attempt, _FETCH_MAX_ATTEMPTS)
        return result


# ── Weekly-cycle freshness (Phase 5D-5, 2026-10-27) ──────────────────────────
# A pair is eligible again once its fitted_at is earlier than the START of the
# current scheduled cycle: the most recent WFO_CYCLE_* instant (default Sunday
# 02:00) in WFO_SCHEDULE_TZ. fitted_at is written when a pair is SAVED, i.e.
# after the trigger, so any elapsed-time rule (".days >= 7", >= 168h, >= 6.5d)
# skips pairs saved late in the previous batch; the cycle boundary does not
# care how long a batch took, and the zone-aware arithmetic absorbs DST weeks.

_FUTURE_SKEW = timedelta(days=1)


def current_cycle_start(now: datetime, tz=None, cycles_back: int = 0) -> datetime:
    """Start (aware datetime) of the scheduled cycle containing `now`, or of the
    cycle `cycles_back` cycles earlier. `now` must be timezone-aware."""
    if tz is None:
        tz = ZoneInfo(config.WFO_SCHEDULE_TZ)
    dow, hour, minute = (int(config.WFO_CYCLE_DAY_OF_WEEK), int(config.WFO_CYCLE_HOUR),
                         int(config.WFO_CYCLE_MINUTE))
    local = now.astimezone(tz)
    day = local.date() - timedelta(days=(local.weekday() - dow) % 7)
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    if start > local:                       # today is the cycle day but before the trigger time
        day -= timedelta(days=7)
    day -= timedelta(days=7 * cycles_back)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def _refit_cycles() -> int:
    """WFO_REFIT_CYCLES as a validated integer >= 1. Missing/zero/negative/non-integer/
    bool/NaN/inf -> 1 (the documented weekly default) with a warning."""
    raw = getattr(config, "WFO_REFIT_CYCLES", 1)
    ok = (not isinstance(raw, bool)
          and isinstance(raw, (int, float))
          and math.isfinite(raw) and raw == int(raw) and raw >= 1)
    if not ok:
        logger.warning("WFO_REFIT_CYCLES=%r is invalid (need an integer >= 1) — using 1", raw)
        return 1
    return int(raw)


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
        self._last_fail_detail: str | None = None   # finer-grained, additive (Phase 5D-2)
        # Phase 5D-5: one WFO batch at a time, whichever entry point (scheduler job or
        # dashboard button) starts it. In-memory, non-reentrant, never taken over.
        self._batch_guard = threading.Lock()
        self._batch_started_at: datetime | None = None

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

    def should_refit(self, pair: str, now: datetime | None = None) -> bool:
        """Weekly-cycle freshness (Phase 5D-5): True when no params saved yet, or the last
        fit is older than the start of the current scheduled cycle (WFO_REFIT_CYCLES - 1
        cycles earlier when > 1). A missing, unparseable or timezone-less fitted_at, a
        fitted_at more than a day in the future (clock skew), or any error computing the
        boundary all fail toward True (eligible). `now` is injectable for tests."""
        last_str = self._state.get(pair, {}).get("fitted_at")
        if not last_str:
            return True
        try:
            last = datetime.fromisoformat(last_str)
            if last.tzinfo is None:
                return True
            now = now if now is not None else datetime.now(timezone.utc)
            if last > now + _FUTURE_SKEW:
                logger.warning("WFO %s: fitted_at %s is in the future (clock skew?) — treating as stale",
                               pair, last_str)
                return True
            cutoff = current_cycle_start(now, cycles_back=_refit_cycles() - 1)
            return last.astimezone(timezone.utc) < cutoff.astimezone(timezone.utc)
        except Exception as exc:
            logger.warning("WFO %s: freshness check failed (%s) — treating as stale", pair, exc)
            return True

    def get_params(self, pair: str) -> dict:
        """
        Return current best params for pair. Returns empty dict if not yet fitted,
        which causes the signal engine to fall back to config defaults.
        """
        with self._lock:
            return dict(self._state.get(pair, {}).get("params", {}))

    def _run_combo(self, pair: str, combo: dict, h1, h4, market: str,
                   eval_start: int | None = None) -> dict | None:
        """Run one backtest for one grid combo. Returns the raw result dict, or None on error.

        eval_start (holdout only): index of the first scored bar -- earlier bars in
        h1 are warm-up context. Omitted from the call when None so the fit path is
        exactly what it was before Phase 5D-1B."""
        indicator_overrides = {
            "CCI_PERIOD":     combo["CCI_PERIOD"],
            "MACD_FAST":      combo["MACD_FAST"],
            "MACD_SLOW":      combo["MACD_SLOW"],
            "MACD_SIGNAL":    combo["MACD_SIGNAL"],
            "cci_threshold":  combo["cci_threshold"],
            "touch_lookback": combo["touch_lookback"],
            "adx_threshold":  combo["adx_threshold"],
        }
        extra = {} if eval_start is None else {"eval_start": eval_start}
        try:
            return run_backtest(
                pair, h1, h4,
                min_score=combo["min_score"],
                market=market,
                adaptive=indicator_overrides,
                **extra,
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
        self._last_fail_detail = None
        train_bars = getattr(config, "WFO_TRAIN_BARS", 1440)
        holdout_frac = getattr(config, "WFO_HOLDOUT_FRAC", 0.3)
        top_n        = getattr(config, "WFO_HOLDOUT_TOP_N", 8)
        min_hold_trd, min_hold_pf = _policy_thresholds()

        train_h1 = df_h1.tail(train_bars).copy()
        train_h4 = df_h4.tail(max(train_bars // confirm_tf_ratio(pair), 50)).copy()

        if len(train_h1) < 120:
            logger.warning("WFO %s: insufficient data (%d bars)", pair, len(train_h1))
            self._last_fail_reason = "insufficient_data"
            return None

        split = int(len(train_h1) * (1 - holdout_frac))
        fit_h1, hold_h1 = train_h1.iloc[:split], train_h1.iloc[split:]
        h4_split = int(len(train_h4) * (1 - holdout_frac))
        fit_h4 = train_h4.iloc[:h4_split]

        if len(hold_h1) < 40:
            logger.warning("WFO %s: holdout slice too small (%d bars) — skipping refit",
                            pair, len(hold_h1))
            self._last_fail_reason = "insufficient_holdout"
            self._last_fail_detail = "holdout_too_small"
            return None

        # Phase 5D-1B context guard (WFO-only, not a signal threshold): the
        # holdout is scored on the whole window from index `split`, so the
        # confirm TF must really be warmed up at that bar. Counted from actual
        # candle timestamps (same rule as run_backtest), not from slice sizes.
        # Checked before the 216-combo grid search so a failure costs nothing.
        min_ctx = getattr(config, "WFO_CONTEXT_CONFIRM_CANDLES", 100)
        if min_ctx > 0:
            n_ctx = confirm_candles_closed_at(train_h1, train_h4, split)
            if n_ctx < min_ctx:
                logger.warning(
                    "WFO %s: only %d closed confirm candles before the first holdout "
                    "bar (need >= %d) — skipping refit rather than scoring a "
                    "cold-started holdout", pair, n_ctx, min_ctx,
                )
                self._last_fail_reason = "insufficient_holdout"
                self._last_fail_detail = "insufficient_context"
                return None

        logger.info("WFO %s: searching %d combinations over %d fit bars (%d holdout) ...",
                    pair, len(_PARAM_COMBINATIONS), len(fit_h1), len(hold_h1))

        # ── Stage 1: rank all combos in-sample on the fit slice only ──────────
        fit_scored = []
        diag_fit_none = 0   # diagnostic only: combos whose backtest raised
        for combo in _PARAM_COMBINATIONS:
            result = self._run_combo(pair, combo, fit_h1, fit_h4, market)
            if result is None:
                diag_fit_none += 1
                continue
            s = _composite_score(result)
            if s > 0:
                fit_scored.append((s, combo, result))

        if not fit_scored:
            logger.warning("WFO %s: no combination found >= 5 trades in-sample", pair)
            try:
                logger.info("WFO %s no-in-sample-candidate diagnostic: in-sample combos "
                            "returning None: %d of %d", pair, diag_fit_none,
                            len(_PARAM_COMBINATIONS))
            except Exception:
                pass
            self._last_fail_reason = "no_in_sample_candidate"
            return None

        fit_scored.sort(key=lambda t: t[0], reverse=True)

        # ── Stage 2: re-score the top N fit candidates on the untouched holdout ──
        # Phase 5D-2: eligibility is decided BEFORE ranking, by two explicit checks
        # (completed trades >= min_hold_trd AND holdout PF >= min_hold_pf). Only
        # eligible candidates are ranked, so an ineligible candidate -- however
        # high its composite -- can never displace an eligible one.
        eligible = []            # (value, fit_rank, combo, holdout_result, fit_score)
        hold_flags = []          # (trades_ok, pf_ok) per holdout result -> rejection verdict
        diag_hold = []       # diagnostic only: (trades, holdout_pf, composite_value) per result
        diag_hold_entries = []   # diagnostic only: (entry_signals, addon_entries, forced_closes)
        diag_hold_none = 0

        for fit_rank, (fit_score, combo, _fit_result) in enumerate(fit_scored[:top_n]):
            # Whole-window frames + eval_start: the fit period is warm-up context
            # only; nothing before `split` is entered or scored (Phase 5D-1B).
            hold_result = self._run_combo(pair, combo, train_h1, train_h4, market,
                                          eval_start=split)
            if hold_result is None:
                diag_hold_none += 1
                continue
            try:
                diag_hold.append((hold_result.get("total_trades", 0),
                                  _holdout_pf(hold_result),
                                  _composite_value(hold_result)))
                diag_hold_entries.append((hold_result.get("entry_signals"),
                                          hold_result.get("addon_entries"),
                                          hold_result.get("forced_closes")))
            except Exception:
                pass
            trades_ok, pf_ok = _holdout_eligibility(hold_result, min_hold_trd, min_hold_pf)
            hold_flags.append((trades_ok, pf_ok))
            if not (trades_ok and pf_ok):
                continue
            try:
                value = _composite_value(hold_result)
            except Exception:
                continue                      # cannot be ranked -> does not compete
            if value != value:                # NaN would poison max()
                continue
            eligible.append((value, fit_rank, combo, hold_result, fit_score))

        # Highest composite wins; a tie goes to the EARLIER fit rank (== the old strict `>`).
        winner = max(eligible, key=lambda e: (e[0], -e[1])) if eligible else None
        recheck_failed = False
        if winner is not None and not all(
                _holdout_eligibility(winner[3], min_hold_trd, min_hold_pf)):
            # Defence in depth: acceptance independently re-verifies eligibility
            # before any state or disk write, whatever selection concluded.
            winner = None
            recheck_failed = True

        if winner is None:
            logger.warning(
                "WFO %s: no in-sample candidate held up out-of-sample "
                "(need >= %d holdout trades and holdout PF >= %.2f) — keeping existing params",
                pair, min_hold_trd, min_hold_pf,
            )
            try:
                _top = fit_scored[0][2]
                logger.info(_format_oos_diagnostic(
                    pair,
                    (config.primary_tf_for(pair), config.confirm_tf_for(pair)),
                    (_top.get("total_trades", 0), round(_result_pf(_top), 2)),
                    diag_fit_none, diag_hold_none, diag_hold, min_hold_trd, min_hold_pf,
                    extra=diag_hold_entries,
                ))
            except Exception:
                pass
            self._last_fail_reason = "failed_oos"
            try:
                self._last_fail_detail = ("acceptance_recheck_failed" if recheck_failed
                                          else _verdict_from_flags(hold_flags))
            except Exception:
                self._last_fail_detail = None
            return None

        best_hold_score, _rank, _combo, best_hold_result, best_fit_score = winner
        best_params = dict(_combo)

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

    def batch_running(self) -> bool:
        """True while a batch holds the guard. Advisory only (a UI hint): the lock taken
        inside run_all_pairs() is the sole authority on whether a batch may start."""
        return self._batch_guard.locked()

    def run_all_pairs(self, get_candles_fn, pairs: list, market: str = "forex",
                      on_start=None) -> bool:
        """Run one WFO batch unless another is already running (Phase 5D-5).

        Returns True if THIS call executed the batch (to completion -- it says nothing
        about how many pairs validated or saved; zero saves is still True) and False if
        it was skipped because another batch held the guard. Exceptions propagate exactly
        as before and the guard is released on every exit path. `on_start`, if given, is
        called once, inside the guard, only when the batch actually starts (a failure in
        it is logged and ignored)."""
        if not self._batch_guard.acquire(blocking=False):
            started = self._batch_started_at
            running = (f"running for {datetime.now(timezone.utc) - started}"
                       if started else "start time unknown")
            logger.warning(
                "WFO batch skipped: another batch is already running (%s) — "
                "no additional batch was started", running)
            return False
        try:
            self._batch_started_at = datetime.now(timezone.utc)
            self._warn_on_schedule_tz_mismatch()
            if on_start is not None:
                try:
                    on_start()
                except Exception as exc:
                    logger.warning("WFO on_start hook failed (batch continues): %s", exc)
            self._run_all_pairs_body(get_candles_fn, pairs, market)
            return True
        finally:
            self._batch_started_at = None
            self._batch_guard.release()

    @staticmethod
    def _warn_on_schedule_tz_mismatch() -> None:
        """The cron fires in the machine zone; freshness uses WFO_SCHEDULE_TZ. Warn if they differ."""
        try:
            import tzlocal
            local = tzlocal.get_localzone_name()
            if local != config.WFO_SCHEDULE_TZ:
                logger.warning(
                    "WFO_SCHEDULE_TZ=%s but the machine/cron zone is %s — weekly-cycle "
                    "freshness boundaries will not line up with the scheduled run",
                    config.WFO_SCHEDULE_TZ, local)
        except Exception:
            pass

    def _run_all_pairs_body(self, get_candles_fn, pairs: list, market: str = "forex") -> None:
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
        oos_detail = {}            # Phase 5D-2, additive: failed_oos sub-reasons
        insufficient_context = 0   # Phase 5D-2, additive: subset of insufficient holdout
        try:
            for pair in pairs:
                if not self.should_refit(pair):
                    logger.info("WFO %s: params still fresh — skipping", pair)
                    skipped_fresh += 1
                    continue
                try:
                    df_h1 = _fetch_with_retry(get_candles_fn, pair, config.primary_tf_for(pair),
                                              train_bars + 60)
                    df_h4 = _fetch_with_retry(get_candles_fn, pair, config.confirm_tf_for(pair),
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
                            if self._last_fail_detail == "insufficient_context":
                                insufficient_context += 1
                        elif reason == "no_in_sample_candidate":
                            fail_no_in_sample_candidate += 1
                        elif reason == "failed_oos":
                            fail_failed_oos += 1
                            oos_detail[self._last_fail_detail] = oos_detail.get(self._last_fail_detail, 0) + 1
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
        try:
            _known = ("too_few_trades", "low_pf", "both", "mixed", "no_holdout_results")
            _other = sum(v for k, v in oos_detail.items() if k not in _known)
            logger.info(
                "WFO batch OOS breakdown: too_few_trades=%d low_pf=%d both=%d mixed=%d "
                "no_holdout_results=%d%s | insufficient_context=%d",
                oos_detail.get("too_few_trades", 0), oos_detail.get("low_pf", 0),
                oos_detail.get("both", 0), oos_detail.get("mixed", 0),
                oos_detail.get("no_holdout_results", 0),
                f" other={_other}" if _other else "", insufficient_context,
            )
        except Exception:
            pass

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
