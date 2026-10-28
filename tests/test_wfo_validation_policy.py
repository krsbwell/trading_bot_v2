"""
Phase 5D-2 — explicit holdout validation policy for engine/wfo_optimizer.py.

Before: a holdout candidate needed `total_trades >= WFO_HOLDOUT_MIN_TRADES (3)` and
then `_composite_score > 0`. `_composite_score` hides a second 5-trade floor (so
3-4 trade candidates were silently dropped) and `score > 0` means "at least one
winning trade" -- a LOSING set (5 trades, PF 0.36) scored +0.25 and would have
been saved. The "PF > 0.5" in the warning text was never enforced.

Now: eligibility is two explicit, independent checks -- completed holdout trades
>= WFO_HOLDOUT_MIN_TRADES (5) and holdout PF >= WFO_HOLDOUT_MIN_PF (1.0) -- applied
BEFORE ranking. Only eligible candidates are ranked (existing composite value,
existing tie-break), and with no eligible candidate nothing is written anywhere.
In-sample behaviour (`_composite_score`, Stage 1) is pinned unchanged.

Deterministic unit tests only: fake backtests, tmp paths, no network.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import hashlib
import logging
import math
from pathlib import Path

import pytest

import config
import engine.wfo_optimizer as wfo_mod
from engine.wfo_optimizer import (
    WFOOptimizer, _PARAM_COMBINATIONS, _composite_score,
)
from tests.test_wfo_holdout_context import (
    _walk, _fx_index, _resample, _train_frames, _FakeBacktest, _mk_result, TRAIN,
)

ROOT = Path(__file__).parent.parent


def _mk(pnls, **extra):
    return _mk_result(pnls, **extra)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. In-sample behaviour is pinned (golden values captured from the pre-change code)
# ═══════════════════════════════════════════════════════════════════════════════

_GOLDEN_COMPOSITE_SCORE = [
    ([10, 10, 10, -15, -15],          1.1),        # 5 trades, PF exactly 1.0
    ([9.9, 10, 10, -15, -15],         1.09633),    # PF 0.9967
    ([20, 20, -30, -30, -30, -20],    0.30303),    # PF 0.3636 -- a losing set scores > 0
    ([0.01] * 5,                      0.075),      # no losses -> legacy "PF" = gross profit
    ([-5.0] * 5,                      0.0),
    ([0.0] * 5,                       0.0),
    ([100, -1, -1, 0],                -1.0),       # 4 trades: hidden floor
    ([5.0] * 3,                       -1.0),       # 3 trades: hidden floor
]


@pytest.mark.parametrize("pnls,expected", _GOLDEN_COMPOSITE_SCORE)
def test_composite_score_golden_unchanged(pnls, expected):
    assert _composite_score(_mk(pnls)) == pytest.approx(expected, rel=1e-5, abs=1e-9)


def test_composite_score_error_result_golden():
    assert _composite_score({"error": "insufficient_data"}) == -1.0


def test_composite_score_keeps_its_five_trade_floor():
    assert _composite_score(_mk([50.0] * 4)) == -1.0
    assert _composite_score(_mk([50.0] * 5)) > 0


def test_composite_value_is_the_floor_free_twin_of_composite_score():
    from engine.wfo_optimizer import _composite_value
    for pnls, _ in _GOLDEN_COMPOSITE_SCORE:
        r = _mk(pnls)
        if r["total_trades"] >= 5:
            assert _composite_value(r) == pytest.approx(_composite_score(r))
    assert _composite_value(_mk([5.0] * 3)) == pytest.approx(15.0 * 1.5)        # no floor
    assert _composite_value(_mk([100, -1, -1, 0])) == pytest.approx(50 * 0.75)  # legacy pf kept
    assert _composite_value({"error": "x"}) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Holdout profit factor
# ═══════════════════════════════════════════════════════════════════════════════

def test_holdout_pf_ordinary_ratio():
    from engine.wfo_optimizer import _holdout_pf
    assert _holdout_pf(_mk([10, 10, 10, -15, -15])) == 1.0
    assert _holdout_pf(_mk([20, -5, -5])) == pytest.approx(2.0)


def test_holdout_pf_no_losses_with_profit_is_infinite_even_for_tiny_profit():
    from engine.wfo_optimizer import _holdout_pf, _result_pf
    r = _mk([0.01] * 5)
    assert _holdout_pf(r) == math.inf
    assert _result_pf(r) == pytest.approx(0.05), "legacy helper (display/ranking) is deliberately left as-is"


def test_holdout_pf_all_losses_is_zero():
    from engine.wfo_optimizer import _holdout_pf
    assert _holdout_pf(_mk([-5.0] * 5)) == 0.0


@pytest.mark.parametrize("result", [
    _mk([0.0] * 5), _mk([]), {}, {"trades": None}, {"trades": []},
])
def test_holdout_pf_zero_profit_zero_loss_is_undefined_not_profitable(result):
    from engine.wfo_optimizer import _holdout_pf
    assert math.isnan(_holdout_pf(result))


@pytest.mark.parametrize("pnls", [
    [5.0, 5.0, 5.0, 5.0, float("nan")],
    [5.0, 5.0, 5.0, 5.0, float("inf")],
    [5.0, 5.0, 5.0, 5.0, float("-inf")],
])
def test_holdout_pf_nonfinite_input_is_rejected_safely(pnls):
    from engine.wfo_optimizer import _holdout_pf
    assert math.isnan(_holdout_pf(_mk(pnls)))


@pytest.mark.parametrize("trades", [
    [{"realised_pnl": 5.0}, {"nope": 1.0}],            # missing key
    [{"realised_pnl": "abc"}],                          # non-numeric
    [{"realised_pnl": None}],
    "not-a-list",
])
def test_holdout_pf_malformed_input_is_rejected_safely(trades):
    from engine.wfo_optimizer import _holdout_pf
    assert math.isnan(_holdout_pf({"total_trades": 5, "trades": trades}))


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Eligibility boundary matrix: completed trades x PF
# ═══════════════════════════════════════════════════════════════════════════════

def _set(n, kind):
    """n completed trades whose holdout PF is `kind` (0 / 0.99 / 1.0 / 1.01 / 'inf')."""
    if n == 0:
        return []
    if kind == "inf":
        return [1.0] * n
    if kind == 0:
        return [-10.0] * n
    # one winner + (n-1) equal losers, gross loss 10*(n-1) -> exact integers for kind 1.0
    loss = 10.0
    win = {0.99: 0.99, 1.0: 1.0, 1.01: 1.01}[kind] * loss * (n - 1)
    return [win] + [-loss] * (n - 1)


@pytest.mark.parametrize("n", [0, 4, 5, 6])
@pytest.mark.parametrize("kind", [0, 0.99, 1.0, 1.01, "inf"])
def test_eligibility_matrix(n, kind):
    from engine.wfo_optimizer import _holdout_eligibility
    pnls = _set(n, kind)
    if n == 1 and kind not in (0, "inf"):
        pytest.skip("not constructible")
    trades_ok, pf_ok = _holdout_eligibility(_mk(pnls), 5, 1.0)
    assert trades_ok == (n >= 5)
    assert pf_ok == (kind in (1.0, 1.01, "inf") and n > 0)


def test_five_trades_pf_036_is_rejected_though_the_old_gate_would_save_it():
    from engine.wfo_optimizer import _holdout_eligibility, _composite_score
    r = _mk([18.0, -12.5, -12.5, -12.5, -12.5])         # PF = 18/50 = 0.36
    assert _composite_score(r) > 0, "old `score > 0` gate accepted this losing set"
    assert _holdout_eligibility(r, 5, 1.0) == (True, False)


def test_five_trades_pf_exactly_one_is_eligible():
    from engine.wfo_optimizer import _holdout_eligibility, _holdout_pf
    r = _mk([10, 10, 10, -15, -15])
    assert _holdout_pf(r) == 1.0
    assert _holdout_eligibility(r, 5, 1.0) == (True, True)


def test_float_boundary_sum_error_does_not_reject_a_true_pf_of_one():
    from engine.wfo_optimizer import _holdout_eligibility, _holdout_pf
    r = _mk([0.3, 0.3, -0.1, -0.2, -0.3])               # 0.6 vs 0.1+0.2+0.3 = 0.6000000000000001
    pf = _holdout_pf(r)
    assert pf < 1.0 and 1.0 - pf < 1e-12, "construct must actually land just below 1.0"
    assert _holdout_eligibility(r, 5, 1.0) == (True, True)


@pytest.mark.parametrize("shortfall,eligible", [
    (1e-12, True),       # float noise
    (1e-10, True),       # inside the 1e-9 tolerance
    (2e-9, False),       # just outside it
    (1e-7, False),
    (1e-4, False),
    (1e-2, False),       # PF 0.99
])
def test_tolerance_is_confined_to_float_noise_and_admits_nothing_material(shortfall, eligible):
    from engine.wfo_optimizer import _holdout_eligibility, _holdout_pf
    r = _mk([40.0 * (1.0 - shortfall), -10.0, -10.0, -10.0, -10.0])
    assert _holdout_pf(r) == pytest.approx(1.0 - shortfall, abs=1e-15)
    assert _holdout_eligibility(r, 5, 1.0)[1] is eligible


def test_no_loss_profitable_candidate_is_eligible_and_zero_zero_is_not():
    from engine.wfo_optimizer import _holdout_eligibility
    assert _holdout_eligibility(_mk([0.01] * 5), 5, 1.0) == (True, True)
    assert _holdout_eligibility(_mk([0.0] * 5), 5, 1.0) == (True, False)
    assert _holdout_eligibility(_mk([0.0] * 5), 5, 0.0) == (True, False), "undefined PF is never eligible, whatever the floor"


def test_eligibility_never_raises_and_fails_closed():
    from engine.wfo_optimizer import _holdout_eligibility
    for bad in (None, {}, {"error": "x"}, {"total_trades": "five", "trades": []}, "junk", 7,
                {"total_trades": float("nan"), "trades": [{"realised_pnl": 1.0}]}):
        assert _holdout_eligibility(bad, 5, 1.0) in ((False, False), (False, True)) and \
            not all(_holdout_eligibility(bad, 5, 1.0))


def test_eligibility_failing_internally_fails_closed(monkeypatch):
    from engine.wfo_optimizer import _holdout_eligibility
    def boom(_r):
        raise RuntimeError("bug")
    monkeypatch.setattr(wfo_mod, "_holdout_pf", boom)
    assert _holdout_eligibility(_mk([10, 10, 10, -5, -5]), 5, 1.0) == (False, False)


def test_thresholds_come_from_config_and_defaults_are_the_approved_policy():
    assert config.WFO_HOLDOUT_MIN_TRADES == 5
    assert config.WFO_HOLDOUT_MIN_PF == 1.0
    from engine.wfo_optimizer import _holdout_eligibility
    assert _holdout_eligibility(_mk([5.0, 5.0, -1.0]), 3, 1.0) == (True, True)      # lowering min is now real
    assert _holdout_eligibility(_mk([6.0, -5.0, -1.0, 2.0, -1.0]), 5, 1.5) == (True, False)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. run(): eligibility BEFORE ranking, acceptance, persistence safety
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def frames():
    m30 = _walk(_fx_index(30, 12), seed=11)
    return m30, _resample(m30, "4h")


class _ProfileBT:
    """Fit: varied per combo (non-trivial ranking). Holdout: the k-th holdout call
    (== fit rank k) returns profiles[k]; None => raises (backtest error)."""
    def __init__(self, profiles):
        self.profiles, self.hold_calls, self.eval_starts = profiles, 0, []

    def __call__(self, pair, h1, h4, min_score=None, market=None, adaptive=None, eval_start=None):
        if eval_start is None:
            return _FakeBacktest.fit_result(adaptive, min_score)
        self.eval_starts.append(eval_start)
        k = self.hold_calls
        self.hold_calls += 1
        p = self.profiles[k] if k < len(self.profiles) else [-5.0]
        if p is None:
            raise RuntimeError("holdout combo error (simulated)")
        return _mk(p, entry_signals=1, addon_entries=max(0, len(p) - 1), forced_closes=1)


def _top_combos(n=8):
    scored = []
    for combo in _PARAM_COMBINATIONS:
        ind = {k: combo[k] for k in ("CCI_PERIOD", "MACD_FAST", "MACD_SLOW", "MACD_SIGNAL",
                                     "cci_threshold", "touch_lookback", "adx_threshold")}
        s = _composite_score(_FakeBacktest.fit_result(ind, combo["min_score"]))
        if s > 0:
            scored.append((s, combo))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for _s, c in scored[:n]]


@pytest.fixture
def wfo(tmp_path):
    return WFOOptimizer(save_path=tmp_path / "wfo.json")


def _run(wfo, frames, profiles, monkeypatch):
    bt = _ProfileBT(profiles)
    monkeypatch.setattr(wfo_mod, "run_backtest", bt)
    prim, conf = frames
    out = wfo.run("EUR_JPY", prim.tail(TRAIN + 60), conf.tail(TRAIN // 8 + 20))
    return out, bt


ELIG_PF1 = [10, 10, 10, -15, -15]            # 5 trades, PF exactly 1.0, value 1.1
ELIG_PF2 = [20, 10, -5, -5, -10, 5]          # 6 trades, PF 35/20 = 1.75
HIGH_BUT_4 = [100, -2, -2, -2]               # 4 trades, enormous composite -> ineligible
LOSER_5 = [18.0, -12.5, -12.5, -12.5, -12.5]   # 5 trades, PF 0.36


def test_ineligible_candidate_with_huge_score_cannot_outrank_an_eligible_one(wfo, frames, monkeypatch):
    out, _ = _run(wfo, frames, [HIGH_BUT_4, ELIG_PF1] + [[-5.0]] * 6, monkeypatch)
    assert out == _top_combos()[1]
    assert wfo._state["EUR_JPY"]["params"] == _top_combos()[1]
    assert wfo._state["EUR_JPY"]["holdout_trades"] == 5


def test_eligible_low_composite_beats_ineligible_high_composite_of_every_kind(wfo, frames, monkeypatch):
    profiles = [HIGH_BUT_4, LOSER_5, [50.0, 50.0], [0.01] * 3, ELIG_PF1, [-5.0], [-5.0], [-5.0]]
    out, _ = _run(wfo, frames, profiles, monkeypatch)
    assert out == _top_combos()[4]


def test_among_eligible_the_higher_composite_wins(wfo, frames, monkeypatch):
    out, _ = _run(wfo, frames, [ELIG_PF1, [-5.0], ELIG_PF2] + [[-5.0]] * 5, monkeypatch)
    assert out == _top_combos()[2]                      # 1.75-PF set outranks the 1.0-PF set


def test_tie_between_eligible_candidates_goes_to_the_earlier_fit_rank(wfo, frames, monkeypatch):
    out, _ = _run(wfo, frames, [[-5.0], [-5.0], ELIG_PF1, [-5.0], ELIG_PF1] + [[-5.0]] * 3, monkeypatch)
    assert out == _top_combos()[2]


def test_a_single_eligible_candidate_is_saved_with_unchanged_state_fields(wfo, frames, monkeypatch):
    out, bt = _run(wfo, frames, [ELIG_PF1] + [[-5.0]] * 7, monkeypatch)
    assert out is not None and wfo._last_save_ok is True and wfo._last_fail_reason is None
    st = wfo._state["EUR_JPY"]
    assert set(st) == {"params", "fitted_at", "train_bars", "score", "win_rate", "total_trades",
                       "total_pnl", "holdout_score", "holdout_trades", "holdout_bars"}
    assert st["holdout_score"] == pytest.approx(1.1) and st["holdout_bars"] == 433
    assert bt.eval_starts == [1007] * 8, "5D-1B: holdout still scored from the boundary"


def test_stage_one_still_ranks_with_composite_score_and_holdout_does_not_call_it(wfo, frames, monkeypatch):
    calls = {"n": 0}
    real = wfo_mod._composite_score

    def spy(r):
        calls["n"] += 1
        return real(r)
    monkeypatch.setattr(wfo_mod, "_composite_score", spy)
    _run(wfo, frames, [ELIG_PF1] + [[-5.0]] * 7, monkeypatch)
    assert calls["n"] == len(_PARAM_COMBINATIONS) == 216


@pytest.mark.parametrize("profiles", [
    [[-5.0, 5.0]] * 8,                                   # too few trades (2)
    [LOSER_5] * 8,                                       # enough trades, PF 0.36
    [[-5.0] * 6] * 8,                                    # PF 0
    [HIGH_BUT_4] * 8,                                    # 4 trades, great PF
    [[10, 10, 10, -15.1, -15]] * 8,                      # 5 trades, PF 0.9967
    [None] * 8,                                          # every holdout backtest errors
])
def test_no_eligible_candidate_changes_nothing_anywhere(tmp_path, frames, monkeypatch, profiles):
    path = tmp_path / "wfo.json"
    wfo = WFOOptimizer(save_path=path)
    wfo._state["EUR_JPY"] = {"params": {"min_score": 999}, "fitted_at": "sentinel"}
    assert wfo._save() is True
    before = (path.read_bytes(), path.stat().st_mtime_ns, dict(wfo._state["EUR_JPY"]))

    saves = {"n": 0}
    real_save = WFOOptimizer._save

    def counting_save(self):
        saves["n"] += 1
        return real_save(self)
    monkeypatch.setattr(WFOOptimizer, "_save", counting_save)

    out, _ = _run(wfo, frames, profiles, monkeypatch)
    assert out is None and wfo._last_fail_reason == "failed_oos" and wfo._last_save_ok is None
    assert saves["n"] == 0, "a rejection must never reach _save()"
    assert (path.read_bytes(), path.stat().st_mtime_ns, dict(wfo._state["EUR_JPY"])) == before


def test_acceptance_reasserts_eligibility_before_any_write(tmp_path, frames, monkeypatch):
    """Defence in depth: even if selection let an ineligible winner through, the final
    check must stop the write."""
    path = tmp_path / "wfo.json"
    wfo = WFOOptimizer(save_path=path)
    real = wfo_mod._holdout_eligibility
    n = {"calls": 0}

    def lying_then_honest(result, mt, mp):
        n["calls"] += 1
        return (True, True) if n["calls"] <= 8 else real(_mk([-5.0]), mt, mp)   # final re-check fails
    monkeypatch.setattr(wfo_mod, "_holdout_eligibility", lying_then_honest)
    out, _ = _run(wfo, frames, [ELIG_PF1] * 8, monkeypatch)
    assert out is None and not path.exists() and "EUR_JPY" not in wfo._state
    assert wfo._last_fail_reason == "failed_oos" and wfo._last_save_ok is None
    assert wfo._last_fail_detail == "acceptance_recheck_failed"


def test_exception_in_eligibility_for_a_candidate_makes_it_ineligible_not_eligible(wfo, frames, monkeypatch):
    real = wfo_mod._holdout_pf

    def flaky(r):                                   # content-based: independent of call order/count
        if len(r.get("trades", [])) == 6:
            raise RuntimeError("bug")
        return real(r)
    monkeypatch.setattr(wfo_mod, "_holdout_pf", flaky)
    out, _ = _run(wfo, frames, [ELIG_PF2, ELIG_PF1] + [[-5.0]] * 6, monkeypatch)
    assert out == _top_combos()[1], "rank-0 hit the bug -> skipped; next eligible candidate wins"


def test_real_wfo_params_file_is_never_touched_by_an_accepting_run(wfo, frames, monkeypatch):
    real = ROOT / "data" / "wfo_params.json"
    fp = lambda: (real.stat().st_size, real.stat().st_mtime_ns, hashlib.md5(real.read_bytes()).hexdigest())
    before = fp()
    out, _ = _run(wfo, frames, [ELIG_PF1] + [[-5.0]] * 7, monkeypatch)
    assert out is not None
    assert fp() == before


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Reporting
# ═══════════════════════════════════════════════════════════════════════════════

R5 = [-5.0, 5.0]                      # 2 trades, PF 1  -> trades short, PF ok
RP = [LOSER_5]                        # 5 trades, PF .36 -> trades ok, PF short


@pytest.mark.parametrize("profiles,verdict", [
    ([[-5.0, -5.0]] * 8,                         "both"),             # short AND unprofitable
    ([[5.0, 5.0, -1.0]] * 8,                     "too_few_trades"),   # PF ok everywhere, 3 trades
    ([LOSER_5] * 8,                              "low_pf"),           # trades ok everywhere, PF .36
    ([LOSER_5, [5.0, 5.0, -1.0]] + [[-5.0]] * 6, "mixed"),            # different candidates satisfy each
    ([None] * 8,                                 "no_holdout_results"),
])
def test_rejection_detail_codes(wfo, frames, monkeypatch, caplog, profiles, verdict):
    with caplog.at_level(logging.INFO):
        out, _ = _run(wfo, frames, profiles, monkeypatch)
    assert out is None
    assert wfo._last_fail_reason == "failed_oos"
    assert wfo._last_fail_detail == verdict
    if verdict != "no_holdout_results":
        line = next(r.message for r in caplog.records if "OOS-reject diagnostic" in r.message)
        assert f"verdict={verdict}" in line


def test_per_pair_line_states_the_real_thresholds_and_counts(wfo, frames, monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        _run(wfo, frames, [LOSER_5, [5.0, 5.0, -1.0], [-5.0, -5.0]] + [[-5.0, -5.0]] * 5, monkeypatch)
    line = next(r.message for r in caplog.records if "OOS-reject diagnostic" in r.message)
    assert "min_trades=5" in line and "min_pf=1" in line
    assert "trades_ok=1 pf_ok=1 eligible=0" in line
    assert "trades-only=" in line and "pf-only=" in line and "both=" in line
    assert "entry_signals" in line and "forced_closes" in line            # 5D-1B fields still reported
    assert "effective min" not in line and "PF>0.5" not in line           # old contradictory text is gone


def test_warning_text_quotes_the_enforced_thresholds(wfo, frames, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        _run(wfo, frames, [LOSER_5] * 8, monkeypatch)
    msg = next(r.message for r in caplog.records if "held up out-of-sample" in r.message)
    assert ">= 5" in msg and ">= 1.00" in msg and "PF > 0.5" not in msg


def test_fail_detail_resets_between_pairs(wfo, frames, monkeypatch):
    _run(wfo, frames, [LOSER_5] * 8, monkeypatch)
    assert wfo._last_fail_detail == "low_pf"
    prim, conf = frames
    assert wfo.run("TINY", prim.tail(50), conf.tail(50)) is None
    assert wfo._last_fail_reason == "insufficient_data" and wfo._last_fail_detail is None


def test_context_guard_failure_reports_insufficient_context(wfo, frames, monkeypatch):
    bt = _ProfileBT([ELIG_PF1])
    monkeypatch.setattr(wfo_mod, "run_backtest", bt)
    prim, conf = frames
    assert wfo.run("EUR_JPY", prim.tail(TRAIN + 60), conf.tail(90)) is None
    assert wfo._last_fail_reason == "insufficient_holdout"
    assert wfo._last_fail_detail == "insufficient_context"
    assert bt.hold_calls == 0


def test_holdout_too_small_reports_its_own_detail(wfo, frames, monkeypatch):
    monkeypatch.setattr(config, "WFO_TRAIN_BARS", 130)          # holdout 39 bars < 40
    monkeypatch.setattr(wfo_mod, "run_backtest", _ProfileBT([ELIG_PF1]))
    prim, conf = frames
    assert wfo.run("EUR_JPY", prim.tail(300), conf.tail(60)) is None
    assert (wfo._last_fail_reason, wfo._last_fail_detail) == ("insufficient_holdout", "holdout_too_small")


def test_no_in_sample_candidate_is_unchanged(wfo, frames, monkeypatch):
    monkeypatch.setattr(wfo_mod, "run_backtest", lambda *a, **k: _mk([-5.0]))
    prim, conf = frames
    assert wfo.run("EUR_JPY", prim.tail(TRAIN + 60), conf.tail(TRAIN // 8 + 20)) is None
    assert (wfo._last_fail_reason, wfo._last_fail_detail) == ("no_in_sample_candidate", None)


def _candles(n=300):
    idx = __import__("pandas").date_range("2026-01-04 22:00", periods=n, freq="30min")
    return __import__("pandas").DataFrame({"close": 1.0}, index=idx)


def test_batch_summary_is_unchanged_and_breakdown_line_is_additive(wfo, monkeypatch, caplog):
    outcomes = {
        "P1": ("failed_oos", "too_few_trades"),
        "P2": ("failed_oos", "low_pf"),
        "P3": ("failed_oos", "low_pf"),
        "P4": ("insufficient_holdout", "insufficient_context"),
        "P5": ("no_in_sample_candidate", None),
    }

    def _run_fake(self, pair, h1, h4, market="forex"):
        self._last_fail_reason, self._last_fail_detail = outcomes[pair]
        self._last_save_ok = None
        return None
    monkeypatch.setattr(WFOOptimizer, "run", _run_fake)
    with caplog.at_level(logging.INFO):
        wfo.run_all_pairs(lambda p, g, c: _candles(), list(outcomes))
    msgs = [r.message for r in caplog.records]
    assert ("WFO batch complete: 5 considered, 0 skipped (fresh), 5 attempted — of those: "
            "0 validated+saved, 0 validated but FAILED TO SAVE, 0 insufficient training data, "
            "1 insufficient holdout data, 1 no in-sample candidate, 3 failed out-of-sample validation, "
            "0 unclassified failure, 0 insufficient data (candle fetch), 0 raised an exception") in msgs
    assert ("WFO batch OOS breakdown: too_few_trades=1 low_pf=2 both=0 mixed=0 no_holdout_results=0 "
            "| insufficient_context=1") in msgs
    assert msgs.index([m for m in msgs if m.startswith("WFO batch complete")][0]) < \
        msgs.index([m for m in msgs if m.startswith("WFO batch OOS breakdown")][0])


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Neighbouring phases keep working
# ═══════════════════════════════════════════════════════════════════════════════

def test_fetch_retry_behaviour_is_unchanged(wfo, monkeypatch):
    """5D-3: a transient 504 on the confirm-TF fetch still recovers via the retry helper."""
    from oandapyV20.exceptions import V20Error
    monkeypatch.setattr(wfo_mod, "_retry_sleep", lambda s: None)
    seen, boom = [], [V20Error(504, "<html/>")]

    def get_candles(pair, gran, count):
        seen.append(gran)
        if gran == "H4" and boom:
            raise boom.pop(0)
        return _candles()
    monkeypatch.setattr(WFOOptimizer, "run", lambda self, *a, **k: None)
    wfo.run_all_pairs(get_candles, ["GBP_JPY"])
    assert seen == ["M30", "H4", "H4"]


def test_old_helpers_and_constants_the_rest_of_the_module_relies_on_still_exist():
    assert callable(wfo_mod._result_pf) and callable(wfo_mod._composite_score)
    assert wfo_mod._FIT_MIN_TRADES == 5
    assert not hasattr(wfo_mod, "_EFFECTIVE_MIN_TRADES"), "the contradictory 'effective' constant is gone"
