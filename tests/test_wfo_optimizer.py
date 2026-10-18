"""
Tests for engine/wfo_optimizer.py's fit/holdout validation (added 2026-08-11).

Before this fix, WFOOptimizer.run() ranked all 216 grid combos purely by
in-sample composite score and saved whichever scored highest, with only a
5-trade floor — no out-of-sample check. That's overfitting by construction.
Confirmed live: GBP_CAD's saved fit (2026-07-30) claimed an 83% win rate off
just 6 in-sample trades; every real GBP_CAD trade placed under those params
since has lost except one. These tests verify the new fit/holdout split
actually rejects an in-sample-only winner and only saves a combo that also
holds up on data the grid search never scored against.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pandas as pd
import pytest

import config
from engine.wfo_optimizer import WFOOptimizer

_PROJECT_ROOT = Path(__file__).parent.parent
# The REAL production WFO log -- used only to fingerprint (size, mtime)
# around subprocess-isolated import checks below, never written to
# directly by any test in this file.
_WFO_LOG_FOR_TESTS = _PROJECT_ROOT / "logs" / "wfo.log"


def _wfo(tmp_path):
    return WFOOptimizer(save_path=tmp_path / "test_wfo_params.json")


def _df(n):
    return pd.DataFrame({"close": range(n)},
                         index=pd.date_range("2026-01-01", periods=n, freq="30min"))


@pytest.fixture(autouse=True)
def _small_train_window(monkeypatch):
    """Keep the grid search fast and the fit/holdout split easy to reason about:
    train_bars=200, holdout_frac=0.3 -> fit=140 bars, holdout=60 bars."""
    monkeypatch.setattr(config, "WFO_TRAIN_BARS", 200)
    monkeypatch.setattr(config, "WFO_HOLDOUT_FRAC", 0.3)
    monkeypatch.setattr(config, "WFO_HOLDOUT_TOP_N", 8)
    monkeypatch.setattr(config, "WFO_HOLDOUT_MIN_TRADES", 3)


def _mk_result(pnls):
    wins  = sum(1 for p in pnls if p > 0)
    total = len(pnls)
    return {
        "total_trades": total,
        "trades":       [{"realised_pnl": p} for p in pnls],
        "win_rate":     (wins / total) if total else 0.0,
        "total_pnl":    sum(pnls),
    }


def _fake_run_backtest_factory(fit_bars=140):
    """Distinguishes the fit slice from the holdout slice by row count (fit=140,
    holdout=60 per the fixture above), and picks out exactly two of the 216 grid
    combos by their full parameter set (not just min_score, since ~72 combos
    share each min_score value and would otherwise tie for the top-N ranking):
    combo A (min_score=50, the "first" combo in the grid) looks great in-sample
    but collapses to too few trades out-of-sample (the GBP_CAD-shaped bug);
    combo B (identical except min_score=55) is modest in-sample but genuinely
    holds up out-of-sample. Every other combo returns <5 trades so it can't
    crowd combo A/B out of the top-N fit ranking."""
    def _fake(pair, h1, h4, min_score=None, market=None, adaptive=None):
        is_holdout = len(h1) < fit_bars
        is_first_grid_slot = (
            adaptive["CCI_PERIOD"] == 14 and adaptive["MACD_FAST"] == 12
            and adaptive["cci_threshold"] == 15 and adaptive["touch_lookback"] == 30
            and adaptive["adx_threshold"] == 22
        )
        if is_first_grid_slot and min_score == 50:
            return _mk_result([5.0] * 18 + [-5.0] * 2) if not is_holdout \
                else _mk_result([-5.0, 5.0])  # only 2 holdout trades < min_hold_trd=3
        if is_first_grid_slot and min_score == 55:
            return _mk_result([5.0] * 6 + [-5.0] * 6) if not is_holdout \
                else _mk_result([5.0] * 4 + [-5.0] * 2)
        return _mk_result([-5.0])  # 1 trade, excluded at fit stage (<5 floor)
    return _fake


def test_in_sample_winner_is_rejected_without_oos_holdup(tmp_path, monkeypatch):
    """The min_score=50 combo wins in-sample (20 trades, 90% WR) but craters to
    2 holdout trades — must NOT be selected as the saved params."""
    import engine.wfo_optimizer as mod
    monkeypatch.setattr(mod, "run_backtest", _fake_run_backtest_factory())

    wfo = _wfo(tmp_path)
    result = wfo.run("EUR_JPY", _df(300), _df(300))

    assert result is not None
    assert result["min_score"] != 50, "overfit in-sample winner must be rejected"
    assert result["min_score"] == 55, "the combo that actually held up OOS should win"


def test_saved_params_reflect_holdout_stats_not_in_sample(tmp_path, monkeypatch):
    """Regression guard for the exact GBP_CAD bug: saved win_rate/total_trades
    must come from the holdout evaluation, not the (much prettier) in-sample one."""
    import engine.wfo_optimizer as mod
    monkeypatch.setattr(mod, "run_backtest", _fake_run_backtest_factory())

    wfo = _wfo(tmp_path)
    wfo.run("EUR_JPY", _df(300), _df(300))

    saved = wfo.get_params("EUR_JPY")
    assert saved["min_score"] == 55
    state = wfo._state["EUR_JPY"]
    assert state["total_trades"] == 6          # holdout trade count, not the fit-stage 12
    assert state["win_rate"] == pytest.approx(4 / 6)  # holdout WR, not the fit-stage 50%


def test_nothing_validates_leaves_existing_params_untouched(tmp_path, monkeypatch):
    """If every candidate fails holdout, run() must return None and must NOT
    clobber whatever was already saved for that pair."""
    import engine.wfo_optimizer as mod

    def _always_fails_holdout(pair, h1, h4, min_score=None, market=None, adaptive=None):
        is_holdout = len(h1) < 140
        if not is_holdout:
            return _mk_result([5.0] * 10 + [-5.0] * 2)  # looks fine in-sample
        return _mk_result([-5.0, 5.0])  # always < 3 holdout trades

    monkeypatch.setattr(mod, "run_backtest", _always_fails_holdout)

    wfo = _wfo(tmp_path)
    wfo._state["GBP_CAD"] = {"params": {"min_score": 999}, "fitted_at": "sentinel"}

    result = wfo.run("GBP_CAD", _df(300), _df(300))

    assert result is None
    assert wfo._state["GBP_CAD"]["params"]["min_score"] == 999, \
        "existing saved params must survive a failed refit, not be overwritten with nothing"


def test_holdout_slice_too_small_skips_refit_safely(tmp_path, monkeypatch):
    """A pair with too little history to form a meaningful holdout slice should
    decline to refit rather than validate against noise."""
    import engine.wfo_optimizer as mod
    monkeypatch.setattr(mod, "run_backtest", _fake_run_backtest_factory())
    monkeypatch.setattr(config, "WFO_TRAIN_BARS", 130)  # holdout = 0.3*130 = 39 < 40 floor

    wfo = _wfo(tmp_path)
    result = wfo.run("EUR_JPY", _df(300), _df(300))

    assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 2026-10-09 — logging additive changes (see bugs_wfo_logging_gaps memory):
# Change 1 (main.py job-fired log) has no dedicated test here — nothing in
# main.py's scheduler/_wfo_job wiring is unit-tested anywhere in this repo
# (confirmed during investigation), and building that scaffolding is out of
# scope for an additive logging change. Changes 2/3 (this file) are covered
# below.
# ═══════════════════════════════════════════════════════════════════════════════

def test_save_returns_true_on_success(tmp_path):
    wfo = _wfo(tmp_path)
    wfo._state["EUR_USD"] = {"params": {"min_score": 55}, "fitted_at": "x"}
    assert wfo._save() is True
    assert wfo._path.exists()


def test_save_returns_false_on_write_failure(tmp_path, monkeypatch):
    """The exact gap this change fixes: a write failure must be visible to
    the caller, not just logged and swallowed."""
    from pathlib import Path

    def _boom(self, *a, **kw):
        raise OSError("disk full (simulated)")
    monkeypatch.setattr(Path, "write_text", _boom)

    wfo = _wfo(tmp_path)
    wfo._state["EUR_USD"] = {"params": {"min_score": 55}, "fitted_at": "x"}
    assert wfo._save() is False


def test_run_reports_save_failure_instead_of_claiming_success(tmp_path, monkeypatch, caplog):
    """Regression for the exact bug found 2026-10-09: before this fix, a
    disk-write failure inside _save() was caught and warned about, but run()
    still logged its normal "best params found" success message right after
    -- the log would claim success even though nothing was persisted. Now
    the success path only logs normally when the save genuinely succeeded;
    a failed save gets its own distinct warning instead."""
    import logging
    import engine.wfo_optimizer as mod
    from pathlib import Path

    monkeypatch.setattr(mod, "run_backtest", _fake_run_backtest_factory())

    def _boom(self, *a, **kw):
        raise OSError("disk full (simulated)")
    monkeypatch.setattr(Path, "write_text", _boom)

    wfo = _wfo(tmp_path)
    with caplog.at_level(logging.WARNING):
        result = wfo.run("EUR_JPY", _df(300), _df(300))

    # Validation still succeeded -- params are correct in-memory, same
    # contract as before this change (run() still returns best_params).
    assert result is not None
    assert result["min_score"] == 55
    assert wfo._last_save_ok is False
    assert any("FAILED TO SAVE" in r.message for r in caplog.records)
    assert not any("WFO EUR_JPY: best" in r.message and "FAILED" not in r.message
                   for r in caplog.records), \
        "the normal unconditional success message must not also print"


def test_last_save_ok_resets_between_pairs_not_stale(tmp_path, monkeypatch):
    """A pair whose validation fails before ever reaching _save() must show
    _last_save_ok as None, not a stale True/False left over from a
    different pair's earlier successful/failed save in the same process."""
    import engine.wfo_optimizer as mod
    monkeypatch.setattr(mod, "run_backtest", _fake_run_backtest_factory())

    wfo = _wfo(tmp_path)
    ok_result = wfo.run("EUR_JPY", _df(300), _df(300))
    assert ok_result is not None
    assert wfo._last_save_ok is True   # genuinely saved, real tmp_path file

    # Second pair: every combo returns too few trades -> validation never
    # even reaches the save step.
    def _always_too_few(pair, h1, h4, min_score=None, market=None, adaptive=None):
        return _mk_result([-5.0])
    monkeypatch.setattr(mod, "run_backtest", _always_too_few)
    none_result = wfo.run("GBP_USD", _df(300), _df(300))
    assert none_result is None
    assert wfo._last_save_ok is None   # not True, not stale from EUR_JPY


def test_run_all_pairs_batch_summary_counts_are_accurate(tmp_path, monkeypatch, caplog):
    """End-to-end: 5 pairs, 5 different outcomes (fresh-skip, no data,
    per-pair exception, validation-rejected, genuine validated+saved
    success), verify the final summary line's counters match exactly --
    read straight from the log output the way an operator actually would,
    not derived by inference from internals."""
    import logging
    import datetime as dt
    import engine.wfo_optimizer as mod

    wfo = _wfo(tmp_path)
    wfo._state["FRESH"] = {
        "params": {"min_score": 1},
        "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    def _get_candles(pair, granularity, count):
        # BOOM raises here, not from run_backtest: a combo-level exception
        # inside run() is caught per-combo by _run_combo() (it just scores
        # that one combo as a miss, not a batch-level failure) -- the only
        # point inside run_all_pairs()'s own try/except that can produce a
        # genuine, uncaught-by-anything-else exception is candle retrieval.
        if pair == "BOOM":
            raise RuntimeError("simulated per-pair crash")
        if pair == "NODATA":
            return None
        return _df(300)

    def _run_backtest_dispatch(pair, h1, h4, min_score=None, market=None, adaptive=None):
        if pair == "REJECTED":
            return _mk_result([-5.0])   # 1 trade, excluded at the <5-trade floor
        return _fake_run_backtest_factory()(pair, h1, h4, min_score=min_score,
                                             market=market, adaptive=adaptive)
    monkeypatch.setattr(mod, "run_backtest", _run_backtest_dispatch)

    with caplog.at_level(logging.INFO):
        wfo.run_all_pairs(_get_candles, ["FRESH", "NODATA", "BOOM", "REJECTED", "EUR_JPY"])

    summary_lines = [r.message for r in caplog.records if "WFO batch complete" in r.message]
    assert len(summary_lines) == 1
    line = summary_lines[0]
    assert "5 considered" in line
    assert "1 skipped (fresh)" in line
    assert "4 attempted" in line
    assert "1 validated+saved" in line     # EUR_JPY
    # 2026-10-09 follow-up: REJECTED returns 1 trade (<5 floor) for every
    # combo, on both fit and holdout slices -- fit_scored ends up empty, so
    # this is specifically a "no in-sample candidate" failure, not a generic
    # "failed validation" (that bucket was split into 4 reasons -- see
    # _last_fail_reason in engine/wfo_optimizer.py). Updated to match the
    # new, more precise wording; the outcome REJECTED represents (rejected
    # before ever reaching out-of-sample scoring) hasn't changed.
    assert "1 no in-sample candidate" in line  # REJECTED
    assert "1 insufficient data" in line   # NODATA (candle fetch)
    assert "1 raised an exception" in line # BOOM


# ═══════════════════════════════════════════════════════════════════════════════
# 2026-10-09 follow-up — _last_fail_reason (see bugs_wfo_batch_summary_reasons
# memory): splits the old single "validation_failed" bucket into the four
# distinct causes run() can return None for, so a batch summary doesn't
# conflate "insufficient data" with a genuine failed out-of-sample
# validation. Each reason is tested directly against run() below, then
# end-to-end through run_all_pairs()'s counters and log line.
# ═══════════════════════════════════════════════════════════════════════════════

def test_fail_reason_insufficient_data(tmp_path):
    """train_h1 ends up < 120 bars -> reason must be 'insufficient_data',
    set before the very first return None in run()."""
    wfo = _wfo(tmp_path)
    result = wfo.run("EUR_JPY", _df(50), _df(50))
    assert result is None
    assert wfo._last_fail_reason == "insufficient_data"


def test_fail_reason_insufficient_holdout(tmp_path):
    """train_h1 has 130 bars (clears the >=120 floor) but the 30% holdout
    slice is only 39 bars (<40) -> reason must be 'insufficient_holdout',
    not the generic 'insufficient_data' from the floor check above it."""
    wfo = _wfo(tmp_path)
    result = wfo.run("EUR_JPY", _df(130), _df(130))
    assert result is None
    assert wfo._last_fail_reason == "insufficient_holdout"


def test_fail_reason_no_in_sample_candidate(tmp_path, monkeypatch):
    """Every combo returns <5 trades on the fit slice -> fit_scored stays
    empty -> reason must be 'no_in_sample_candidate' (rejected before ever
    reaching out-of-sample scoring, distinct from failing that scoring)."""
    import engine.wfo_optimizer as mod
    monkeypatch.setattr(mod, "run_backtest", lambda *a, **kw: _mk_result([-5.0]))

    wfo = _wfo(tmp_path)
    result = wfo.run("EUR_JPY", _df(300), _df(300))
    assert result is None
    assert wfo._last_fail_reason == "no_in_sample_candidate"


def test_fail_reason_failed_oos(tmp_path, monkeypatch):
    """Every combo looks fine in-sample (populates fit_scored) but every one
    of the top-N candidates comes up short out-of-sample (< min_hold_trd) ->
    reason must be 'failed_oos', distinct from never having a candidate at
    all."""
    import engine.wfo_optimizer as mod

    def _good_fit_bad_holdout(pair, h1, h4, min_score=None, market=None, adaptive=None):
        is_holdout = len(h1) < 140  # fit=140 bars per the _small_train_window fixture
        if not is_holdout:
            return _mk_result([5.0] * 6 + [-5.0] * 2)   # 8 trades, score > 0
        return _mk_result([-5.0])                       # 1 holdout trade < min_hold_trd=3
    monkeypatch.setattr(mod, "run_backtest", _good_fit_bad_holdout)

    wfo = _wfo(tmp_path)
    result = wfo.run("EUR_JPY", _df(300), _df(300))
    assert result is None
    assert wfo._last_fail_reason == "failed_oos"


def test_run_all_pairs_classifies_fail_reasons_and_reconciles(tmp_path, monkeypatch, caplog):
    """End-to-end: one pair per *reachable* fail reason, verify the batch
    summary's counters each show exactly 1, and that they reconcile against
    `attempted` (no pair silently dropped or double-counted).

    Only 3 of the 4 reasons are reachable through run_all_pairs() with a
    dedicated pair here -- 'insufficient_data' (run()'s own len(train_h1)<120
    check) can never fire via this path: run_all_pairs() has its own
    candle-length pre-check at the identical 120-bar threshold (line ~374,
    `if df_h1 is None or len(df_h1) < 120`), which always filters a
    too-short fetch into the pre-existing `insufficient_data` (candle-fetch)
    counter *before* run() is ever called. Confirmed by the first version of
    this test, which tried a 50-bar TOOSHORT pair expecting run()'s reason
    and got the candle-fetch counter instead. fail_insufficient_data is
    therefore asserted at 0 here (reachable only via a direct run() call --
    see test_fail_reason_insufficient_data above)."""
    import logging
    import engine.wfo_optimizer as mod

    def _get_candles(pair, granularity, count):
        if pair == "SHORTHOLDOUT":
            return _df(130)       # -> insufficient_holdout
        return _df(300)           # NOGOODFIT / BADOOS

    def _run_backtest_dispatch(pair, h1, h4, min_score=None, market=None, adaptive=None):
        if pair == "NOGOODFIT":
            return _mk_result([-5.0])                       # -> no_in_sample_candidate
        if pair == "BADOOS":
            is_holdout = len(h1) < 140
            if not is_holdout:
                return _mk_result([5.0] * 6 + [-5.0] * 2)
            return _mk_result([-5.0])                        # -> failed_oos
        raise AssertionError(f"run_backtest should not be reached for {pair}")
    monkeypatch.setattr(mod, "run_backtest", _run_backtest_dispatch)

    wfo = _wfo(tmp_path)
    with caplog.at_level(logging.INFO):
        wfo.run_all_pairs(_get_candles, ["SHORTHOLDOUT", "NOGOODFIT", "BADOOS"])

    summary_lines = [r.message for r in caplog.records if "WFO batch complete" in r.message]
    assert len(summary_lines) == 1
    line = summary_lines[0]
    assert "3 considered" in line
    assert "0 skipped (fresh)" in line
    assert "3 attempted" in line
    assert "0 insufficient training data" in line   # unreachable via this path, see docstring
    assert "1 insufficient holdout data" in line
    assert "1 no in-sample candidate" in line
    assert "1 failed out-of-sample validation" in line
    assert "0 unclassified failure" in line
    assert "0 validated+saved" in line
    assert "0 validated but FAILED TO SAVE" in line
    assert "0 raised an exception" in line
    # Reconciliation: the three attempted pairs land in exactly one bucket
    # each (0+1+1+1+0 = 3 = attempted), none dropped, none double-counted.


def test_run_all_pairs_unclassified_fallback_when_reason_missing(tmp_path, monkeypatch, caplog):
    """Safety net: if run() ever returned None without setting
    _last_fail_reason (an invariant run() itself is supposed to uphold at
    every one of its four return-None sites, but diagnostic bookkeeping must
    not assume its own invariant holds), run_all_pairs() must not silently
    guess a category -- it logs an explicit error and counts the pair as
    'unclassified' instead of mis-filing it under one of the four named
    reasons."""
    import logging
    wfo = _wfo(tmp_path)

    def _fake_run(pair, h1, h4, market="forex"):
        return None  # deliberately leaves self._last_fail_reason untouched (stays None)
    monkeypatch.setattr(wfo, "run", _fake_run)

    def _get_candles(pair, granularity, count):
        return _df(300)

    with caplog.at_level(logging.INFO):
        wfo.run_all_pairs(_get_candles, ["MYSTERY"])

    assert any(
        r.levelname == "ERROR" and "unrecognized" in r.message
        for r in caplog.records
    )
    summary = next(r.message for r in caplog.records if "WFO batch complete" in r.message)
    assert "1 unclassified failure" in summary


# ═══════════════════════════════════════════════════════════════════════════════
# 2026-10-12 — dedicated logs/wfo.log (see bugs_wfo_dedicated_logfile memory):
# main.log's RotatingFileHandler rotates by size and, at current 28-pair
# request volume, was confirmed to retain under a day of history -- nowhere
# near enough to survive the week between WFO runs. engine.wfo_optimizer's
# logger now also writes to a separate, daily-rotating, 35-backup
# logs/wfo.log via _install_wfo_file_handler(), attached once at import time.
# Also covers: a "WFO batch starting" line added at the top of
# run_all_pairs(), and should_refit() exceptions (which sit outside the
# per-pair try/except) now being logged before they re-raise.
# ═══════════════════════════════════════════════════════════════════════════════

# 2026-10-12 correction (approved review follow-up, see
# bugs_wfo_logging_side_effects memory): _install_wfo_file_handler() is no
# longer called automatically at module import -- only main.py calls it
# explicitly now, from inside main()'s own body (not module scope), so it
# only fires when the bot process actually starts, never from a plain
# `import main` (as tests/test_scan_reliability.py and
# tests/test_trade_gate_parity.py both do). Every test below that exercises
# the installer must call it explicitly too, and MUST always pass a
# tmp_path-based `log_path` override -- never the default -- so no test run
# ever creates, rotates, or writes into the real logs/wfo.log.
#
# engine.wfo_optimizer's logger (mod.logger) is a process-wide singleton
# that persists for the life of the test session. Any test that installs a
# handler directly onto it must remove that handler again afterward, or it
# leaks into every test that runs later in the same process.
# _clean_wfo_logger snapshots and restores mod.logger.handlers around each
# test that uses it.

@pytest.fixture
def _clean_wfo_logger():
    import engine.wfo_optimizer as mod
    snapshot = list(mod.logger.handlers)
    yield mod.logger
    mod.logger.handlers = snapshot


# 2026-10-12 correction #2: the original in-process version of this check
# (assert mod.logger has no WFO handler right after import) passed in
# isolation but FAILED when the full suite ran test_scan_reliability.py or
# test_trade_gate_parity.py first -- those files `import main`, and since
# main.py's install call used to also sit at module scope, that import
# permanently attached a real handler to the shared singleton for the rest
# of the pytest process. An in-process assertion on a process-wide
# singleton can only ever prove "nothing ELSE in this process already did
# this," never "importing this module alone never does this" -- so this is
# rewritten as two subprocess checks, each a genuinely fresh interpreter
# with no shared state, and each one also fingerprints the real
# logs/wfo.log around the call to directly confirm it wasn't touched.

def _wfo_log_fingerprint():
    if _WFO_LOG_FOR_TESTS.exists():
        st = _WFO_LOG_FOR_TESTS.stat()
        return (st.st_size, st.st_mtime)
    return None


def test_importing_engine_wfo_optimizer_alone_does_not_install_handler():
    """Requirement 13: a fresh python -c subprocess imports ONLY
    engine.wfo_optimizer and reports whether a WFO handler got attached --
    immune to whatever any other test file has already imported earlier in
    this pytest process. Also confirms the real logs/wfo.log is untouched."""
    import subprocess
    before = _wfo_log_fingerprint()
    result = subprocess.run(
        [sys.executable, "-c",
         "import engine.wfo_optimizer as mod; "
         "print(any(getattr(h, '_is_wfo_handler', False) for h in mod.logger.handlers))"],
        cwd=str(_PROJECT_ROOT), capture_output=True, text=True, timeout=60,
    )
    after = _wfo_log_fingerprint()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
    assert before == after, "logs/wfo.log changed from merely importing engine.wfo_optimizer"


def test_importing_main_alone_does_not_install_handler():
    """Requirement 14: importing main.py WITHOUT calling main() must also
    not install the WFO handler -- it's only installed from inside main()'s
    own body now, reached only via the `if __name__ == '__main__'` guard at
    the bottom of main.py, never by import alone. Also confirms the real
    logs/wfo.log is untouched by the import."""
    import subprocess
    before = _wfo_log_fingerprint()
    result = subprocess.run(
        [sys.executable, "-c",
         "import logging; import main; "
         "print(any(getattr(h, '_is_wfo_handler', False) "
         "for h in logging.getLogger('engine.wfo_optimizer').handlers))"],
        cwd=str(_PROJECT_ROOT), capture_output=True, text=True, timeout=60,
    )
    after = _wfo_log_fingerprint()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
    assert before == after, "logs/wfo.log changed from merely importing main.py"


def test_explicit_install_creates_exactly_one_handler(tmp_path, _clean_wfo_logger):
    import engine.wfo_optimizer as mod
    log = _clean_wfo_logger
    mod._install_wfo_file_handler(log, log_path=tmp_path / "wfo_test.log")
    wfo_handlers = [h for h in log.handlers if getattr(h, "_is_wfo_handler", False)]
    assert len(wfo_handlers) == 1


def test_explicit_install_is_idempotent(tmp_path, _clean_wfo_logger):
    """Calling it twice -- even with a different log_path the second time
    -- must not add a second handler. The dedup check only looks for the
    marker, not for a matching path, so this also documents that a second
    call's `log_path` argument is simply ignored once a handler exists."""
    import engine.wfo_optimizer as mod
    log = _clean_wfo_logger
    mod._install_wfo_file_handler(log, log_path=tmp_path / "wfo_test.log")
    mod._install_wfo_file_handler(log, log_path=tmp_path / "wfo_test_2.log")
    wfo_handlers = [h for h in log.handlers if getattr(h, "_is_wfo_handler", False)]
    assert len(wfo_handlers) == 1


def test_installed_handler_configuration(tmp_path, _clean_wfo_logger):
    """Requirement D: TimedRotatingFileHandler, when='D', interval=1 (which
    the stdlib normalizes to 86400 seconds), backupCount=35, and a
    formatter whose datefmt includes the full date, not just time-of-day
    (unlike main.py's own root-logger formatter, which is HH:MM:SS only)."""
    import engine.wfo_optimizer as mod
    log = _clean_wfo_logger
    mod._install_wfo_file_handler(log, log_path=tmp_path / "wfo_test.log")
    handler = next(h for h in log.handlers if getattr(h, "_is_wfo_handler", False))

    assert isinstance(handler, TimedRotatingFileHandler)
    assert handler.when == "D"
    assert handler.interval == 24 * 60 * 60
    assert handler.backupCount == 35
    assert handler.formatter.datefmt == "%Y-%m-%d %H:%M:%S"


def test_installed_handler_attaches_to_named_logger_not_root(tmp_path, _clean_wfo_logger):
    """Requirement E: the handler goes on engine.wfo_optimizer's own
    logger, never on the root logger main.py separately configures."""
    import logging
    import engine.wfo_optimizer as mod
    log = _clean_wfo_logger
    assert log.name == "engine.wfo_optimizer"
    root_handlers_before = list(logging.getLogger().handlers)

    mod._install_wfo_file_handler(log, log_path=tmp_path / "wfo_test.log")

    assert any(getattr(h, "_is_wfo_handler", False) for h in log.handlers)
    assert not any(getattr(h, "_is_wfo_handler", False) for h in logging.getLogger().handlers)
    assert logging.getLogger().handlers == root_handlers_before


def test_wfo_logger_propagation_preserved():
    """Requirement F (propagation half): the file handler must be purely
    additive -- propagate must stay True (untouched by this module
    entirely) so messages still also reach main.log/console exactly as
    before, and so pytest's caplog (which hooks in above the root logger)
    keeps seeing WFO log records at all. True regardless of whether the
    handler has been explicitly installed in this process yet."""
    import engine.wfo_optimizer as mod
    assert mod.logger.propagate is True


def test_caplog_still_captures_wfo_messages_with_file_handler_attached(tmp_path, _clean_wfo_logger, caplog):
    """Requirement F: existing caplog-based tests in this file must keep
    working even once the dedicated file handler has been explicitly
    installed (simulating main.py's own init) -- a second handler on this
    logger must not interfere with caplog's own capture of the same
    records (it hooks in via propagation to root)."""
    import logging
    log = _clean_wfo_logger
    import engine.wfo_optimizer as mod
    mod._install_wfo_file_handler(log, log_path=tmp_path / "wfo_test.log")
    with caplog.at_level(logging.INFO):
        log.info("caplog smoke test message")
    assert any("caplog smoke test message" in r.message for r in caplog.records)


def test_run_all_pairs_batch_starting_precedes_batch_complete(tmp_path, caplog):
    import logging
    wfo = _wfo(tmp_path)

    def _get_candles(pair, granularity, count):
        return None  # -> insufficient_data (candle-fetch), fastest path through the loop

    with caplog.at_level(logging.INFO):
        wfo.run_all_pairs(_get_candles, ["EUR_USD"])

    messages = [r.message for r in caplog.records]
    start_idx = next(i for i, m in enumerate(messages) if "WFO batch starting" in m)
    complete_idx = next(i for i, m in enumerate(messages) if "WFO batch complete" in m)
    assert start_idx < complete_idx


def test_run_all_pairs_logs_and_reraises_should_refit_exception(tmp_path, monkeypatch, caplog):
    """should_refit() sits outside the per-pair try/except -- an exception
    there must be logged against the WFO logger (so it survives in wfo.log,
    not just crash_logging's generic thread-level record) and must still
    propagate unchanged: not suppressed, not turned into a counter, and the
    batch-summary log must NOT appear, since the function still exits via
    the exception exactly as it did before this change."""
    import logging

    wfo = _wfo(tmp_path)

    def _boom(pair):
        raise RuntimeError("simulated should_refit crash")
    monkeypatch.setattr(wfo, "should_refit", _boom)

    def _get_candles(pair, granularity, count):
        raise AssertionError("should never be reached -- should_refit raises first")

    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError, match="simulated should_refit crash"):
            wfo.run_all_pairs(_get_candles, ["EUR_USD"])

    assert any(
        r.levelname == "ERROR" and "unexpected exception outside per-pair handling" in r.message
        for r in caplog.records
    )
    assert not any("WFO batch complete" in r.message for r in caplog.records)


# ═══════════════════════════════════════════════════════════════════════════════
# 2026-10-18 — Phase 5B: OOS-rejection diagnostics. Logging only: these tests
# prove the diagnostics are observable AND that they cannot change what the
# optimizer selects, rejects, or saves.
# ═══════════════════════════════════════════════════════════════════════════════

def _sabotage_diagnostics(monkeypatch):
    """Make every diagnostic helper raise -- equivalent to 'diagnostics off'."""
    import engine.wfo_optimizer as mod

    def _boom(*a, **kw):
        raise RuntimeError("diagnostic bug (simulated)")
    monkeypatch.setattr(mod, "_result_pf", _boom)
    monkeypatch.setattr(mod, "_format_oos_diagnostic", _boom)


def _state_without_time(wfo, pair):
    return {k: v for k, v in wfo._state[pair].items() if k != "fitted_at"}


def test_diagnostics_do_not_change_selection_or_saved_params(tmp_path, monkeypatch):
    import json
    import engine.wfo_optimizer as mod
    monkeypatch.setattr(mod, "run_backtest", _fake_run_backtest_factory())

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _wfo(tmp_path / "a")
    b = _wfo(tmp_path / "b")
    res_a = a.run("EUR_JPY", _df(300), _df(300))
    _sabotage_diagnostics(monkeypatch)
    res_b = b.run("EUR_JPY", _df(300), _df(300))

    assert res_a == res_b and res_a["min_score"] == 55
    assert _state_without_time(a, "EUR_JPY") == _state_without_time(b, "EUR_JPY")
    ja = json.loads((tmp_path / "a" / "test_wfo_params.json").read_text())
    jb = json.loads((tmp_path / "b" / "test_wfo_params.json").read_text())
    for j in (ja, jb):
        j["EUR_JPY"].pop("fitted_at")
    assert ja == jb


def _profile_fake():
    """Fit: every combo (except CCI_PERIOD 28 -> raises) gets 10 trades PF 4.
    Holdout: the first 8 holdout calls follow fixed profiles (see below)."""
    calls = {"hold": 0}
    profiles = [
        [-5.0, 5.0],            # 2 trades, PF 1
        [-5.0],                 # 1 trade,  PF 0
        [5.0, 5.0, -5.0],       # 3 trades, PF 2
        [-5.0] * 4,             # 4 trades, PF 0
        [-5.0] * 5,             # 5 trades, PF 0
        [-5.0] * 6,             # 6 trades, PF 0
        [],                     # 0 trades
        None,                   # raises -> run_backtest error -> None
    ]

    def _fake(pair, h1, h4, min_score=None, market=None, adaptive=None):
        if len(h1) >= 140:
            if adaptive["CCI_PERIOD"] == 28:
                raise RuntimeError("fit combo error (simulated)")
            return _mk_result([5.0] * 8 + [-5.0] * 2)
        p = profiles[calls["hold"]]
        calls["hold"] += 1
        if p is None:
            raise RuntimeError("holdout combo error (simulated)")
        return _mk_result(p)
    return _fake


def test_rejection_diagnostic_content_and_classification(tmp_path, monkeypatch, caplog):
    import logging
    import engine.wfo_optimizer as mod
    monkeypatch.setattr(mod, "run_backtest", _profile_fake())

    wfo = _wfo(tmp_path)
    with caplog.at_level(logging.INFO):
        result = wfo.run("EUR_JPY", _df(300), _df(300))

    assert result is None
    assert wfo._last_fail_reason == "failed_oos"
    line = next(r.message for r in caplog.records if "OOS-reject diagnostic" in r.message)
    assert "WFO EUR_JPY" in line
    assert f"tf={config.primary_tf_for('EUR_JPY')}/{config.confirm_tf_for('EUR_JPY')}" in line
    assert "strategy=EMA-Bounce" in line
    assert "verdict=low_pf" in line                      # 5+ trades exist, none profitable
    assert "best in-sample: trades=10 PF=4.0" in line
    assert "holdout candidates evaluated=7 (None=1)" in line
    assert "holdout trades min/med/max=0/3/6" in line
    assert "holdout PF min/med/max=0/0/2" in line
    assert "trades <3: 3, 3-4: 2, >=5: 2" in line
    assert "configured min=3, effective min=5" in line
    assert "PF condition met (score>0): 0, PF>0.5: 2" in line
    assert "failed both (trades<5 and PF<=0): 3" in line
    assert "in-sample combos returning None: 72" in line


def test_rejection_leaves_state_and_disk_untouched(tmp_path, monkeypatch):
    import engine.wfo_optimizer as mod
    monkeypatch.setattr(mod, "run_backtest", _profile_fake())

    wfo = _wfo(tmp_path)
    wfo._state["EUR_JPY"] = {"params": {"min_score": 999}, "fitted_at": "sentinel"}
    before = dict(wfo._state["EUR_JPY"])
    assert wfo.run("EUR_JPY", _df(300), _df(300)) is None
    assert wfo._state["EUR_JPY"] == before
    assert wfo._last_save_ok is None
    assert not wfo._path.exists(), "a rejection must never write the params file"


def test_rejection_decision_identical_with_diagnostics_broken(tmp_path, monkeypatch):
    import engine.wfo_optimizer as mod
    monkeypatch.setattr(mod, "run_backtest", _profile_fake())
    assert _wfo(tmp_path).run("EUR_JPY", _df(300), _df(300)) is None

    monkeypatch.setattr(mod, "run_backtest", _profile_fake())   # fresh call counter
    _sabotage_diagnostics(monkeypatch)
    wfo = _wfo(tmp_path)
    assert wfo.run("EUR_JPY", _df(300), _df(300)) is None
    assert wfo._last_fail_reason == "failed_oos"


def test_no_in_sample_candidate_logs_none_count(tmp_path, monkeypatch, caplog):
    import logging
    import engine.wfo_optimizer as mod

    def _fake(pair, h1, h4, min_score=None, market=None, adaptive=None):
        if adaptive["CCI_PERIOD"] == 28:
            raise RuntimeError("simulated")
        return _mk_result([-5.0])          # <5 trades -> score -1
    monkeypatch.setattr(mod, "run_backtest", _fake)

    wfo = _wfo(tmp_path)
    with caplog.at_level(logging.INFO):
        assert wfo.run("EUR_JPY", _df(300), _df(300)) is None
    assert wfo._last_fail_reason == "no_in_sample_candidate"
    assert any("no-in-sample-candidate diagnostic" in r.message
               and "returning None: 72 of 216" in r.message for r in caplog.records)


@pytest.mark.parametrize("cands,expected", [
    ([], "no_holdout_results"),
    ([(5, 0.0, -1.0)], "low_pf"),
    ([(4, 2.0, -1.0), (2, 0.0, -1.0)], "too_few_trades"),
    ([(4, 0.0, -1.0), (0, 0.0, -1.0)], "both"),
])
def test_classify_oos_rejection(cands, expected):
    from engine.wfo_optimizer import _classify_oos_rejection
    assert _classify_oos_rejection(cands) == expected


def test_result_pf_matches_composite_formula():
    from engine.wfo_optimizer import _result_pf, _composite_score
    r = _mk_result([5.0] * 3 + [-5.0] * 3 + [5.0] * 2)       # 8 trades, PF 10/... 
    pf = _result_pf(r)
    assert pf == pytest.approx(25.0 / 15.0)
    assert _composite_score(r) == pytest.approx(pf * (0.5 + r["win_rate"]))
    assert _result_pf(_mk_result([5.0, 5.0])) == 10.0         # no losses -> gross profit
    assert _result_pf({}) == 0.0
