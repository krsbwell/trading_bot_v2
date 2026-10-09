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

import pandas as pd
import pytest

import config
from engine.wfo_optimizer import WFOOptimizer


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
