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
