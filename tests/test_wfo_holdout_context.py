"""
Phase 5D-1B (Option B1) — holdout-only historical context for WFO.

Defect (5D-1A, derived by arithmetic on a modelled FX calendar): run_backtest()
skips every bar until >= 50 confirm-TF candles have CLOSED, but WFO handed the
holdout combos ONLY the holdout slice (433 M30 bars + ~55 H4 candles), so just
41 of 433 holdout bars (9.5%) could ever emit a signal (GBP_CAD H4/D: 133).

B1: the holdout combos run on the whole 1,440-bar window with
`eval_start=<holdout boundary>`: fit-period bars are CONTEXT ONLY (visible to
indicators exactly as live would see them) and nothing is scored, entered, or
measured before the boundary. The fit stage is untouched.

All tests here are deterministic synthetic-data unit tests: no network, no
OANDA, no optimisation, no historical backtest.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging

import numpy as np
import pandas as pd
import pytest

import config
import backtest.runner as bt
import engine.strategy_ema_cci_macd as ema_strat
import engine.wfo_optimizer as wfo_mod
from engine.wfo_optimizer import WFOOptimizer, _composite_score, _PARAM_COMBINATIONS

TRAIN, HOLD_FRAC = 1440, 0.3
SPLIT = int(TRAIN * (1 - HOLD_FRAC))            # 1007 (float truncation, matches wfo.log)
HOLD_N = TRAIN - SPLIT                          # 433


# ── synthetic OANDA-style calendars ───────────────────────────────────────────

def _fx_index(minutes, weeks, start="2026-01-04 22:00"):
    """Contiguous FX weeks: open Sun 22:00 UTC, last bar closes Fri 22:00."""
    per_week = 120 * 60 // minutes
    stamps = []
    for w in range(weeks):
        wk = pd.Timestamp(start) + pd.Timedelta(days=7 * w)
        stamps += [wk + pd.Timedelta(minutes=minutes * k) for k in range(per_week)]
    return pd.DatetimeIndex(stamps)


def _walk(index, seed, step=0.0003, start=1.10):
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(rng.uniform(-step, step, len(index)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "open": open_,
        "high": np.maximum(open_, close) + 0.0004,
        "low":  np.minimum(open_, close) - 0.0004,
        "close": close,
        "volume": 1000,
    }, index=index)


def _resample(df, rule):
    return df.resample(rule, origin=df.index[0]).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


@pytest.fixture(scope="module")
def m30_h4():
    """12 full weeks of M30 + the H4 built from it (both end at Friday close,
    exactly as the Sunday-02:00-EDT scheduled WFO fetch does)."""
    m30 = _walk(_fx_index(30, 12), seed=11)
    return m30, _resample(m30, "4h")


@pytest.fixture(scope="module")
def h4_d():
    """GBP_CAD shape: H4 primary, Daily confirm."""
    h4 = _walk(_fx_index(240, 52), seed=23)
    return h4, _resample(h4, "24h")


def _train_frames(prim, conf, ratio):
    """Exactly wfo_optimizer.run()'s own slicing of what run_all_pairs fetches."""
    p = prim.tail(TRAIN + 60).tail(TRAIN)
    c = conf.tail(TRAIN // ratio + 20).tail(max(TRAIN // ratio, 50))
    return p, c


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    ema_strat.clear_cache()
    # hermetic w.r.t. config edits: pin everything run_backtest's loop reads
    monkeypatch.setattr(config, "ATR_TRAILING_ENABLED", False)
    monkeypatch.setattr(config, "BACKTEST_SPREAD_ENABLED", False)
    monkeypatch.setattr(config, "ML_SCORE_BOOST_ENABLED", False)
    monkeypatch.setattr(config, "ALLOW_MULTIPLE_PER_PAIR", True)
    monkeypatch.setattr(config, "SESSION_START_UTC", 4)
    monkeypatch.setattr(config, "SESSION_END_UTC", 17)
    yield
    ema_strat.clear_cache()


# ── stub strategies ───────────────────────────────────────────────────────────

class _Rec:
    """Zero-signal strategy that records every bar run_backtest lets through its
    confirm-candle gate (the strategy is only called for eligible bars)."""
    def __init__(self):
        self.p_last, self.h4_len, self.c_last_end = [], [], []

    def buy(self, pair, h1, h4, adaptive=None):
        self.p_last.append(h1.index[-1])
        self.h4_len.append(len(h4))
        self.c_last_end.append(h4.index[-1])
        return 0.0

    @staticmethod
    def sell(pair, h1, h4, adaptive=None):
        return 0.0


def _sl_long(pair, h1, direction):
    c = float(h1["close"].iloc[-1])
    return c - 0.0030 if direction == "long" else c + 0.0030


def _momentum_buy(pair, h1, h4, adaptive=None):
    c, c4 = h1["close"], h4["close"]
    return 60.0 if (c.iloc[-1] > c.iloc[-2] > c.iloc[-3] and c4.iloc[-1] >= c4.iloc[-2]) else 0.0


def _momentum_sell(pair, h1, h4, adaptive=None):
    c, c4 = h1["close"], h4["close"]
    return 60.0 if (c.iloc[-1] < c.iloc[-2] < c.iloc[-3] and c4.iloc[-1] <= c4.iloc[-2]) else 0.0


def _confirm_candle_buy(pair, h1, h4, adaptive=None):
    """Hinges on the NEWEST confirm candle's direction, so any peek at a candle
    that has not closed yet (its final OHLC is the future) changes the decision."""
    return 60.0 if (h4["close"].iloc[-1] > h4["open"].iloc[-1]
                    and h1["close"].iloc[-1] > h1["close"].iloc[-2]) else 0.0


def _confirm_candle_sell(pair, h1, h4, adaptive=None):
    return 60.0 if (h4["close"].iloc[-1] < h4["open"].iloc[-1]
                    and h1["close"].iloc[-1] < h1["close"].iloc[-2]) else 0.0


def _pin_risk_gates(monkeypatch):
    """Deterministic pyramiding gates (risk_manager has process-global state)."""
    monkeypatch.setattr(bt, "validate_pre_trade", lambda *a, **k: (True, ""))
    monkeypatch.setattr(bt, "_addon_ok", lambda *a, **k: True)


def _fire_between(m30, lo, hi):
    """Strategy that fires only while the newest bar is in [m30[lo], m30[hi])."""
    t0, t1 = m30.index[lo], m30.index[hi]
    return lambda pair, h1, h4, adaptive=None: 60.0 if t0 <= h1.index[-1] < t1 else 0.0


def _run(prim, conf, rec=None, **kw):
    rec = rec or _Rec()
    return bt.run_backtest("EUR_JPY", prim, conf, buy_fn=rec.buy, sell_fn=rec.sell,
                           stop_loss_fn=_sl_long, min_score=0, **kw), rec


# ═══════════════════════════════════════════════════════════════════════════════
# 1. The defect, and its fix: actual holdout eligibility (demonstrated, not assumed)
# ═══════════════════════════════════════════════════════════════════════════════

def test_legacy_holdout_slice_is_starved_m30_h4(m30_h4):
    """Documents the defect: holdout-only frames let 41 of 433 bars signal."""
    p, c = _train_frames(*m30_h4, ratio=8)
    assert (len(p), len(c)) == (1440, 180)
    hold_p, hold_c = p.iloc[SPLIT:], c.iloc[int(180 * (1 - HOLD_FRAC)):]
    assert (len(hold_p), len(hold_c)) == (433, 55)
    _, rec = _run(hold_p, hold_c)                       # legacy call, no eval_start
    assert len(rec.p_last) == 41


def test_b1_holdout_is_fully_eligible_m30_h4(m30_h4):
    p, c = _train_frames(*m30_h4, ratio=8)
    _, rec = _run(p, c, eval_start=SPLIT)
    assert len(rec.p_last) == HOLD_N == 433
    assert rec.p_last[0] == p.index[SPLIT]              # first scored bar == boundary
    assert rec.p_last[-1] == p.index[-1]


def test_legacy_and_b1_eligibility_gbp_cad_h4_d(h4_d):
    p, c = _train_frames(*h4_d, ratio=6)
    assert (len(p), len(c)) == (1440, 240)
    hold_p, hold_c = p.iloc[SPLIT:], c.iloc[int(240 * (1 - HOLD_FRAC)):]
    _, legacy = _run(hold_p, hold_c)
    assert len(legacy.p_last) == 133
    _, b1 = _run(p, c, eval_start=SPLIT)
    assert len(b1.p_last) == 433


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Safeguard 1 — context is never scored history; confirm timing respected
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_entry_equity_or_signal_before_the_boundary(m30_h4, monkeypatch):
    _pin_risk_gates(monkeypatch)
    p, c = _train_frames(*m30_h4, ratio=8)
    res = bt.run_backtest("EUR_JPY", p, c, buy_fn=_momentum_buy, sell_fn=_momentum_sell,
                          stop_loss_fn=_sl_long, min_score=0, eval_start=SPLIT)
    assert res["total_signals"] > 0, "scenario must actually trade or this test proves nothing"
    assert min(s["bar_idx"] for s in res["signal_log"]) >= SPLIT
    assert min(pd.Timestamp(s["time"]) for s in res["signal_log"]) >= p.index[SPLIT]
    assert min(e["bar_idx"] for e in res["equity_curve"]) >= SPLIT
    assert res["eval_start"] == SPLIT


def test_confirm_candles_are_closed_at_every_scored_bar(m30_h4):
    """Candle-close rule survives context: at every scored bar the newest
    confirm candle the strategy can see closed no later than that bar's close."""
    p, c = _train_frames(*m30_h4, ratio=8)
    _, rec = _run(p, c, eval_start=SPLIT)
    c_len, p_len = pd.Timedelta(hours=4), pd.Timedelta(minutes=30)
    assert rec.p_last
    for bar, c_open in zip(rec.p_last, rec.c_last_end):
        assert c_open + c_len <= bar + p_len


def test_first_scored_bar_sees_only_past_data_and_enough_candles(m30_h4):
    p, c = _train_frames(*m30_h4, ratio=8)
    _, rec = _run(p, c, eval_start=SPLIT)
    assert rec.h4_len[0] >= 100                         # warmed up, not starved
    assert rec.h4_len[0] == min(bt.confirm_candles_closed_at(p, c, SPLIT), config.LIVE_CANDLE_WINDOW)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. No lookahead
# ═══════════════════════════════════════════════════════════════════════════════

def test_future_data_cannot_change_earlier_entries(m30_h4, monkeypatch):
    _pin_risk_gates(monkeypatch)
    p, c = _train_frames(*m30_h4, ratio=8)
    k = SPLIT + 150                                     # perturb everything after bar k
    cutoff_close = p.index[k] + pd.Timedelta(minutes=30)

    p2, c2 = p.copy(), c.copy()
    rng = np.random.default_rng(99)

    def scramble(df, mask):
        base = 1.10 + rng.uniform(-0.05, 0.05, int(mask.sum()))
        df.loc[mask, "open"] = base
        df.loc[mask, "close"] = base + rng.uniform(-0.01, 0.01, int(mask.sum()))
        df.loc[mask, "high"] = base + 0.02
        df.loc[mask, "low"] = base - 0.02

    scramble(p2, p2.index > p.index[k])                              # bars after k
    scramble(c2, (c2.index + pd.Timedelta(hours=4)) > cutoff_close)  # candles not yet closed at bar k
    assert not p2.equals(p) and not c2.equals(c)

    def go(a, b):
        return bt.run_backtest("EUR_JPY", a, b, buy_fn=_confirm_candle_buy,
                               sell_fn=_confirm_candle_sell, stop_loss_fn=_sl_long,
                               min_score=0, eval_start=SPLIT)

    base, pert = go(p, c), go(p2, c2)
    early = lambda r: [(s["bar_idx"], s["direction"], s["entry"], s["sl"])
                       for s in r["signal_log"] if s["bar_idx"] <= k]
    assert len(early(base)) >= 5, "need several entries at/before bar k to be a meaningful check"
    assert early(base) == early(pert)


def test_decision_at_each_bar_equals_a_live_feed_that_stops_there(m30_h4, monkeypatch):
    """Prefix equivalence (catches 1-bar and forming-candle look-ahead): for many
    cut points k, give the engine ONLY what a live feed has at bar k -- primary
    bars <= k and confirm candles already closed at k's close -- and require the
    identical decision at bar k as on the full series."""
    _pin_risk_gates(monkeypatch)
    p, c = _train_frames(*m30_h4, ratio=8)
    c_len, p_len = pd.Timedelta(hours=4), pd.Timedelta(minutes=30)

    def go(pp, cc, start):
        return bt.run_backtest("EUR_JPY", pp, cc, buy_fn=_confirm_candle_buy,
                               sell_fn=_confirm_candle_sell, stop_loss_fn=_sl_long,
                               min_score=0, eval_start=start)

    full = go(p, c, SPLIT)
    by_bar = {s_["bar_idx"]: (s_["direction"], s_["entry"], s_["sl"]) for s_ in full["signal_log"]}
    fired = 0
    for k in range(SPLIT + 3, len(p), 11):
        live_c = c[(c.index + c_len) <= (p.index[k] + p_len)]
        part = go(p.iloc[:k + 1], live_c, k)
        got = {s_["bar_idx"]: (s_["direction"], s_["entry"], s_["sl"]) for s_ in part["signal_log"]}
        assert got.get(k) == by_bar.get(k), f"decision at bar {k} depends on data a live feed would not have"
        fired += k in by_bar
    assert fired >= 5, "scenario must fire at several cut points or this proves nothing"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Default behaviour unchanged
# ═══════════════════════════════════════════════════════════════════════════════

def _golden_scenario(monkeypatch):
    _pin_risk_gates(monkeypatch)
    m30 = _walk(_fx_index(30, 6), seed=5)
    h4 = _resample(m30, "4h")
    return bt.run_backtest("EUR_JPY", m30, h4, buy_fn=_momentum_buy, sell_fn=_momentum_sell,
                           stop_loss_fn=_sl_long, min_score=0)


# Captured from the UNMODIFIED run_backtest (before 5D-1B) so this pins the
# legacy behaviour, not the new code's own output.
_GOLDEN = {
    "total_trades": 137, "total_signals": 137, "wins": 54, "win_rate": 0.3942,
    "total_pnl": -114.8, "profit_factor": 0.49, "final_balance": 385.2,
    "max_drawdown": 0.4002,
    "first_signal": "2026-01-15T09:00:00", "last_signal": "2026-02-13T14:00:00",
    "n_equity": 1041,
}


def test_default_behaviour_matches_pre_change_golden(monkeypatch):
    r = _golden_scenario(monkeypatch)
    got = {
        "total_trades": r["total_trades"], "total_signals": r["total_signals"],
        "wins": r["wins"], "win_rate": r["win_rate"], "total_pnl": r["total_pnl"],
        "profit_factor": r["profit_factor"], "final_balance": r["final_balance"],
        "max_drawdown": r["max_drawdown"],
        "first_signal": r["signal_log"][0]["time"], "last_signal": r["signal_log"][-1]["time"],
        "n_equity": len(r["equity_curve"]),
    }
    assert got == _GOLDEN


def test_eval_start_none_and_warmup_are_identical(monkeypatch):
    _pin_risk_gates(monkeypatch)
    m30 = _walk(_fx_index(30, 6), seed=5)
    h4 = _resample(m30, "4h")

    def go(**kw):
        return bt.run_backtest("EUR_JPY", m30, h4, buy_fn=_momentum_buy, sell_fn=_momentum_sell,
                               stop_loss_fn=_sl_long, min_score=0, **kw)
    a, b, c = go(), go(eval_start=None), go(eval_start=bt._WARMUP)
    def stable(r):
        out = {k: v for k, v in r.items() if k not in ("eval_start", "trades")}
        out["pnls"] = [t["realised_pnl"] for t in r["trades"]]
        return out
    assert a["total_trades"] > 20
    assert stable(a) == stable(b) == stable(c)


def test_eval_start_is_floored_at_warmup(m30_h4):
    p, c = _train_frames(*m30_h4, ratio=8)
    _, rec = _run(p, c, eval_start=5)                   # absurdly early -> same as default
    _, ref = _run(p, c)
    assert rec.p_last == ref.p_last


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Safeguard 3 — diagnostics are additive, count real events, change nothing
# ═══════════════════════════════════════════════════════════════════════════════

def test_counters_are_additive_and_do_not_alter_existing_fields(monkeypatch):
    r = _golden_scenario(monkeypatch)
    for key in ("entry_signals", "addon_entries", "forced_closes"):
        assert key in r
    assert r["total_trades"] == len(r["trades"])        # semantics unchanged
    assert r["total_signals"] == len(r["signal_log"])


def test_pyramid_add_ons_and_forced_closes_are_counted_separately(monkeypatch):
    """Fires every bar with a far stop: 1 independent entry, N-1 add-ons, and
    every position is still open at end-of-data -> forced closes == positions,
    NOT additional entries."""
    _pin_risk_gates(monkeypatch)
    monkeypatch.setattr(config, "SESSION_START_UTC", 0)
    monkeypatch.setattr(config, "SESSION_END_UTC", 24)
    m30 = _walk(_fx_index(30, 6), seed=8)
    h4 = _resample(m30, "4h")
    fire = _fire_between(m30, 500, 580)                 # 80 consecutive bars, then stop
    res = bt.run_backtest("EUR_JPY", m30, h4, buy_fn=fire, sell_fn=_Rec.sell,
                          stop_loss_fn=lambda p, h, d: float(h["close"].iloc[-1]) - 0.02, min_score=0)
    n = res["total_signals"]
    assert n > 5
    assert res["entry_signals"] == 1
    assert res["addon_entries"] == n - 1
    assert res["forced_closes"] == n == res["total_trades"]
    assert res["entry_signals"] + res["addon_entries"] == len(res["signal_log"])
    assert [s["addon"] for s in res["signal_log"]] == [False] + [True] * (n - 1)


def test_positions_that_close_naturally_are_not_forced_closes(monkeypatch):
    """Tight stop: every position stops out on the next bar, so each signal is a
    fresh independent entry and nothing is left to force-close."""
    _pin_risk_gates(monkeypatch)
    monkeypatch.setattr(config, "SESSION_START_UTC", 0)
    monkeypatch.setattr(config, "SESSION_END_UTC", 24)
    m30 = _walk(_fx_index(30, 6), seed=8)
    h4 = _resample(m30, "4h")
    fire = _fire_between(m30, 500, 530)
    res = bt.run_backtest("EUR_JPY", m30, h4, buy_fn=fire, sell_fn=_Rec.sell,
                          stop_loss_fn=lambda p, h, d: float(h["close"].iloc[-1]) - 0.0002, min_score=0)
    assert res["total_signals"] > 5
    assert res["forced_closes"] == 0
    assert res["entry_signals"] + res["addon_entries"] == res["total_trades"]


def test_rejected_opens_are_not_counted(monkeypatch):
    """Counters follow the actual open_trade() outcome (None == rejected)."""
    _pin_risk_gates(monkeypatch)
    monkeypatch.setattr(config, "SESSION_START_UTC", 0)
    monkeypatch.setattr(config, "SESSION_END_UTC", 24)
    real = bt.PaperTrader.open_trade
    calls = {"n": 0}

    def flaky(self, *a, **k):
        calls["n"] += 1
        return None if calls["n"] % 2 == 0 else real(self, *a, **k)
    monkeypatch.setattr(bt.PaperTrader, "open_trade", flaky)
    m30 = _walk(_fx_index(30, 6), seed=8)
    h4 = _resample(m30, "4h")
    fire = _fire_between(m30, 500, 530)
    res = bt.run_backtest("EUR_JPY", m30, h4, buy_fn=fire, sell_fn=_Rec.sell,
                          stop_loss_fn=lambda p, h, d: float(h["close"].iloc[-1]) - 0.02, min_score=0)
    accepted = res["entry_signals"] + res["addon_entries"]
    assert accepted == res["total_trades"] == calls["n"] - calls["n"] // 2
    assert accepted < calls["n"]
    # legacy quirk preserved on purpose: signal_log records every attempted open
    assert res["total_signals"] == calls["n"]


# ═══════════════════════════════════════════════════════════════════════════════
# 6. WFO wiring — fit stage untouched, holdout gets context, no extra requests
# ═══════════════════════════════════════════════════════════════════════════════

def _mk_result(pnls, **extra):
    wins = sum(1 for p in pnls if p > 0)
    r = {"total_trades": len(pnls), "trades": [{"realised_pnl": p} for p in pnls],
         "win_rate": wins / len(pnls) if pnls else 0.0, "total_pnl": sum(pnls)}
    r.update(extra)
    return r


class _FakeBacktest:
    """Records each run_backtest call. Fit results vary per combo (so ranking is
    non-trivial); holdout results are caller-supplied."""
    def __init__(self, hold_pnls=None, hold_extra=None):
        self.calls = []
        self.hold_pnls = hold_pnls if hold_pnls is not None else [5.0] * 4 + [-5.0] * 2
        self.hold_extra = hold_extra or {}

    @staticmethod
    def fit_result(adaptive, min_score):
        wins = 5 + (adaptive["CCI_PERIOD"] % 7) + (adaptive["cci_threshold"] % 5) + (min_score % 4)
        return _mk_result([5.0] * wins + [-5.0] * 3)

    def __call__(self, pair, h1, h4, min_score=None, market=None, adaptive=None, eval_start=None):
        key = (min_score, adaptive["CCI_PERIOD"], adaptive["MACD_FAST"], adaptive["MACD_SLOW"],
               adaptive["MACD_SIGNAL"], adaptive["cci_threshold"], adaptive["touch_lookback"],
               adaptive["adx_threshold"])
        self.calls.append({"h1": h1, "h4": h4, "eval_start": eval_start, "key": key})
        if eval_start is None:
            return self.fit_result(adaptive, min_score)
        return _mk_result(self.hold_pnls, **self.hold_extra)


@pytest.fixture
def wfo(tmp_path):
    return WFOOptimizer(save_path=tmp_path / "wfo.json")


def test_fit_stage_is_byte_identical_to_legacy_slicing(wfo, m30_h4, monkeypatch):
    fake = _FakeBacktest()
    monkeypatch.setattr(wfo_mod, "run_backtest", fake)
    prim, conf = m30_h4
    wfo.run("EUR_JPY", prim.tail(TRAIN + 60), conf.tail(TRAIN // 8 + 20))
    p, c = _train_frames(prim, conf, 8)
    fit_calls = [x for x in fake.calls if x["eval_start"] is None]
    assert len(fit_calls) == len(_PARAM_COMBINATIONS) == 216
    exp_p, exp_c = p.iloc[:SPLIT], c.iloc[:int(len(c) * (1 - HOLD_FRAC))]
    assert (len(exp_p), len(exp_c)) == (1007, 125)
    for x in fit_calls:
        assert x["h1"].equals(exp_p) and x["h4"].equals(exp_c)
        assert x["h1"].index[-1] < p.index[SPLIT]       # fit never sees a holdout bar


def test_fit_ranking_and_holdout_candidate_order_unchanged(wfo, m30_h4, monkeypatch):
    fake = _FakeBacktest()
    monkeypatch.setattr(wfo_mod, "run_backtest", fake)
    prim, conf = m30_h4
    wfo.run("EUR_JPY", prim.tail(TRAIN + 60), conf.tail(TRAIN // 8 + 20))

    scored = []
    for combo in _PARAM_COMBINATIONS:
        ind = {k: combo[k] for k in ("CCI_PERIOD", "MACD_FAST", "MACD_SLOW", "MACD_SIGNAL",
                                     "cci_threshold", "touch_lookback", "adx_threshold")}
        s = _composite_score(_FakeBacktest.fit_result(ind, combo["min_score"]))
        if s > 0:
            scored.append((s, combo))
    scored.sort(key=lambda t: t[0], reverse=True)       # same stable sort as run()
    expected = [(c["min_score"], c["CCI_PERIOD"], c["MACD_FAST"], c["MACD_SLOW"], c["MACD_SIGNAL"],
                 c["cci_threshold"], c["touch_lookback"], c["adx_threshold"]) for _s, c in scored[:8]]
    hold_keys = [x["key"] for x in fake.calls if x["eval_start"] is not None]
    assert hold_keys == expected


def test_holdout_combos_get_full_window_and_boundary(wfo, m30_h4, monkeypatch):
    fake = _FakeBacktest()
    monkeypatch.setattr(wfo_mod, "run_backtest", fake)
    prim, conf = m30_h4
    out = wfo.run("EUR_JPY", prim.tail(TRAIN + 60), conf.tail(TRAIN // 8 + 20))
    p, c = _train_frames(prim, conf, 8)
    hold = [x for x in fake.calls if x["eval_start"] is not None]
    assert len(hold) == 8
    for x in hold:
        assert x["eval_start"] == SPLIT
        assert x["h1"].equals(p) and x["h4"].equals(c)  # same 1,440-bar window, no extra history
    assert out is not None
    assert wfo._state["EUR_JPY"]["holdout_bars"] == HOLD_N   # evaluation window unchanged


def test_both_fetches_use_legacy_sizes(wfo, monkeypatch):
    asked = []

    def get_candles(pair, gran, count):
        asked.append((gran, count))
        return None if len(asked) == 2 else _walk(_fx_index(30, 1), seed=1)
    monkeypatch.setattr(wfo_mod.WFOOptimizer, "run", lambda *a, **k: None)
    wfo.run_all_pairs(get_candles, ["EUR_JPY"])
    assert asked == [("M30", 1500), ("H4", 1440 // 8 + 20)]


# ── Safeguard 2: guard validates ACTUAL closed history ───────────────────────

def test_default_context_knob_is_100_and_wfo_only():
    assert config.WFO_CONTEXT_CONFIRM_CANDLES == 100
    assert config.WFO_HOLDOUT_MIN_TRADES == 5           # 5D-2 policy (was 3); 5D-1B itself changed no threshold
    assert config.WFO_HOLDOUT_MIN_PF == 1.0
    assert config.WFO_HOLDOUT_FRAC == 0.3 and config.WFO_TRAIN_BARS == 1440


def test_real_shape_frames_pass_the_guard_with_margin(m30_h4, h4_d):
    p, c = _train_frames(*m30_h4, ratio=8)
    assert bt.confirm_candles_closed_at(p, c, SPLIT) == 126
    p2, c2 = _train_frames(*h4_d, ratio=6)
    assert bt.confirm_candles_closed_at(p2, c2, SPLIT) == 168


def test_guard_rejects_when_confirm_history_is_short(wfo, m30_h4, monkeypatch, caplog):
    fake = _FakeBacktest()
    monkeypatch.setattr(wfo_mod, "run_backtest", fake)
    prim, conf = m30_h4
    with caplog.at_level(logging.WARNING):
        out = wfo.run("EUR_JPY", prim.tail(TRAIN + 60), conf.tail(90))   # only 90 candles exist
    assert out is None
    assert wfo._last_fail_reason == "insufficient_holdout"
    assert fake.calls == [], "must fail BEFORE spending a 216-combo grid search"
    assert any("closed confirm" in r.message and "100" in r.message for r in caplog.records)


def test_guard_uses_timestamps_not_row_counts(wfo, m30_h4, monkeypatch):
    """180+ rows of confirm candles, but they all START at the holdout boundary,
    so none are closed before the first scored bar: a row-count check would pass."""
    fake = _FakeBacktest()
    monkeypatch.setattr(wfo_mod, "run_backtest", fake)
    prim, conf = m30_h4
    p = prim.tail(TRAIN + 60).tail(TRAIN)
    late = conf[conf.index >= p.index[SPLIT]]
    assert len(late) >= 50
    assert wfo.run("EUR_JPY", prim.tail(TRAIN + 60), late) is None
    assert wfo._last_fail_reason == "insufficient_holdout" and fake.calls == []


def test_guard_can_be_disabled_and_thresholds_not_involved(wfo, m30_h4, monkeypatch):
    monkeypatch.setattr(config, "WFO_CONTEXT_CONFIRM_CANDLES", 0)
    fake = _FakeBacktest()
    monkeypatch.setattr(wfo_mod, "run_backtest", fake)
    prim, conf = m30_h4
    wfo.run("EUR_JPY", prim.tail(TRAIN + 60), conf.tail(90))
    assert len(fake.calls) > 0


def test_run_combo_omits_eval_start_when_not_given(wfo):
    """Legacy callers/fakes with the old signature keep working on the fit path."""
    seen = {}

    def strict(pair, h1, h4, min_score=None, market=None, adaptive=None):
        seen["ok"] = True
        return _mk_result([1.0])
    orig = wfo_mod.run_backtest
    wfo_mod.run_backtest = strict
    try:
        res = wfo._run_combo("EUR_JPY", _PARAM_COMBINATIONS[0], None, None, "forex")
    finally:
        wfo_mod.run_backtest = orig
    assert seen.get("ok") and res is not None


# ── Diagnostics: new fields shown, decisions unchanged ───────────────────────

def test_reject_diagnostic_reports_entry_signal_breakdown(wfo, m30_h4, monkeypatch, caplog):
    fake = _FakeBacktest(hold_pnls=[5.0, -5.0],
                         hold_extra={"entry_signals": 1, "addon_entries": 1, "forced_closes": 1})
    monkeypatch.setattr(wfo_mod, "run_backtest", fake)
    prim, conf = m30_h4
    with caplog.at_level(logging.INFO):
        assert wfo.run("EUR_JPY", prim.tail(TRAIN + 60), conf.tail(TRAIN // 8 + 20)) is None
    line = next(r.message for r in caplog.records if "OOS-reject diagnostic" in r.message)
    assert "entry_signals" in line and "addon_entries" in line and "forced_closes" in line
    assert "trades min/med/max=2/2/2" in line


def test_reject_diagnostic_survives_results_without_new_fields(wfo, m30_h4, monkeypatch, caplog):
    fake = _FakeBacktest(hold_pnls=[5.0, -5.0])          # legacy-shaped result
    monkeypatch.setattr(wfo_mod, "run_backtest", fake)
    prim, conf = m30_h4
    with caplog.at_level(logging.INFO):
        assert wfo.run("EUR_JPY", prim.tail(TRAIN + 60), conf.tail(TRAIN // 8 + 20)) is None
    assert any("OOS-reject diagnostic" in r.message for r in caplog.records)
    assert wfo._last_fail_reason == "failed_oos"


def test_counters_do_not_change_the_selection_decision(tmp_path, m30_h4, monkeypatch):
    prim, conf = m30_h4
    outs = []
    for i, extra in enumerate(({}, {"entry_signals": 9, "addon_entries": 0, "forced_closes": 9})):
        w = WFOOptimizer(save_path=tmp_path / f"w{i}.json")
        monkeypatch.setattr(wfo_mod, "run_backtest", _FakeBacktest(hold_extra=extra))
        outs.append(w.run("EUR_JPY", prim.tail(TRAIN + 60), conf.tail(TRAIN // 8 + 20)))
    assert outs[0] == outs[1] and outs[0] is not None


# ── Real strategy smoke test (single deterministic run, synthetic data) ───────

def test_real_ema_bounce_runs_with_eval_start_on_synthetic_data(m30_h4):
    p, c = _train_frames(*m30_h4, ratio=8)
    res = bt.run_backtest("EUR_JPY", p, c, eval_start=SPLIT)
    assert "error" not in res
    assert all(s["bar_idx"] >= SPLIT for s in res["signal_log"])
    assert res["eval_start"] == SPLIT
