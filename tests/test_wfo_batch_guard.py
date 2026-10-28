"""
Phase 5D-5 -- single-batch guard for WFOOptimizer.run_all_pairs().

The lock inside run_all_pairs() is authoritative for BOTH entry points (the
APScheduler job and the dashboard button). Deterministic: Events/Barriers only,
bounded joins, tmp paths, fake candle fetchers -- no OANDA, no sleeping.
"""
import json
import threading
from pathlib import Path

import pytest

from engine.wfo_optimizer import WFOOptimizer

_ROOT = Path(__file__).resolve().parent.parent
T = 10  # seconds; only ever reached if a test is about to fail


def _wfo(tmp_path, name="test_wfo_params.json"):
    return WFOOptimizer(save_path=tmp_path / name)


class Blocker:
    """get_candles stand-in: the first fetch announces itself and waits."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, pair, gran, count):
        with self._lock:
            self.calls.append((pair, gran))
        self.entered.set()
        assert self.release.wait(T), "test never released the blocker"
        return None                     # -> 'insufficient data (candle fetch)', batch continues


def _bg(fn, *a, **k):
    out = {}

    def run():
        try:
            out["ret"] = fn(*a, **k)
        except BaseException as exc:      # noqa: BLE001 - tests inspect it
            out["exc"] = exc
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def _join(t):
    t.join(T)
    assert not t.is_alive()


# ── authority of the lock ───────────────────────────────────────────────────

def test_second_call_is_skipped_while_first_runs(tmp_path, caplog):
    w, b = _wfo(tmp_path), Blocker()
    t1, o1 = _bg(w.run_all_pairs, b, ["EUR_CAD"])
    assert b.entered.wait(T)
    assert w.batch_running() is True
    n_before = len(b.calls)
    with caplog.at_level("WARNING"):
        assert w.run_all_pairs(b, ["GBP_JPY", "EUR_CAD"]) is False
    assert len(b.calls) == n_before                      # no fetch, no run() for the skipped call
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "skipped" in msgs.lower() and "already running" in msgs.lower()
    b.release.set()
    _join(t1)
    assert o1["ret"] is True
    assert w.batch_running() is False


def test_ten_simultaneous_triggers_exactly_one_executes(tmp_path):
    w, b = _wfo(tmp_path), Blocker()
    barrier = threading.Barrier(10)
    rets, lock = [], threading.Lock()

    def trigger():
        barrier.wait(T)
        r = w.run_all_pairs(b, ["EUR_CAD"])
        with lock:
            rets.append(r)

    ts = [threading.Thread(target=trigger, daemon=True) for _ in range(10)]
    for t in ts:
        t.start()
    assert b.entered.wait(T)
    # let the 9 losers finish while the winner is still blocked in its fetch
    for _ in range(int(T * 100)):
        with lock:
            if len(rets) == 9:
                break
        threading.Event().wait(0.01)
    with lock:
        assert rets.count(False) == 9
    b.release.set()
    for t in ts:
        _join(t)
    assert sorted(rets) == [False] * 9 + [True]
    assert len([c for c in b.calls if c[0] == "EUR_CAD"]) == 2     # one batch = primary + confirm fetch


def test_skipper_never_releases_the_holders_lock(tmp_path):
    w, b = _wfo(tmp_path), Blocker()
    t1, _ = _bg(w.run_all_pairs, b, ["EUR_CAD"])
    assert b.entered.wait(T)
    assert w.run_all_pairs(b, ["EUR_CAD"]) is False
    assert w.batch_running() is True                       # still held by the first batch
    assert w.run_all_pairs(b, ["EUR_CAD"]) is False        # and a third is skipped too
    b.release.set()
    _join(t1)


def test_skipped_call_leaves_shared_state_untouched(tmp_path):
    path = tmp_path / "test_wfo_params.json"
    path.write_text(json.dumps({"EUR_USD": {"params": {"min_score": 55}, "fitted_at": "x"}}), "utf-8")
    w, b = WFOOptimizer(save_path=path), Blocker()
    t1, _ = _bg(w.run_all_pairs, b, ["EUR_CAD"])
    assert b.entered.wait(T)
    w._last_save_ok, w._last_fail_reason, w._last_fail_detail = "S", "R", "D"
    state_before = json.dumps(w._state, sort_keys=True)
    bytes_before, mtime_before = path.read_bytes(), path.stat().st_mtime_ns
    assert w.run_all_pairs(b, ["EUR_USD", "EUR_CAD"]) is False
    assert (w._last_save_ok, w._last_fail_reason, w._last_fail_detail) == ("S", "R", "D")
    assert json.dumps(w._state, sort_keys=True) == state_before
    assert path.read_bytes() == bytes_before and path.stat().st_mtime_ns == mtime_before
    b.release.set()
    _join(t1)


# ── release on every exit path ──────────────────────────────────────────────

def test_released_after_normal_completion_and_next_call_runs(tmp_path):
    w = _wfo(tmp_path)
    assert w.run_all_pairs(lambda p, g, c: None, ["EUR_CAD"]) is True
    assert w.batch_running() is False
    assert w.run_all_pairs(lambda p, g, c: None, ["EUR_CAD"]) is True


def test_true_does_not_mean_anything_was_saved(tmp_path):
    w = _wfo(tmp_path)
    assert w.run_all_pairs(lambda p, g, c: None, ["EUR_CAD", "GBP_JPY"]) is True
    assert not (tmp_path / "test_wfo_params.json").exists()      # zero saves, still "ran"


def test_empty_pair_list_still_counts_as_ran(tmp_path):
    assert _wfo(tmp_path).run_all_pairs(lambda p, g, c: None, []) is True


def test_released_after_per_pair_exception(tmp_path):
    w = _wfo(tmp_path)

    def boom(p, g, c):
        raise RuntimeError("fetch exploded")
    assert w.run_all_pairs(boom, ["EUR_CAD"]) is True            # handled per pair, as before
    assert w.batch_running() is False


def test_released_when_should_refit_raises_and_exception_still_propagates(tmp_path, monkeypatch):
    w = _wfo(tmp_path)

    def bad(pair, now=None):
        raise RuntimeError("simulated should_refit crash")
    monkeypatch.setattr(w, "should_refit", bad)
    with pytest.raises(RuntimeError, match="simulated should_refit crash"):
        w.run_all_pairs(lambda p, g, c: None, ["EUR_CAD"])
    assert w.batch_running() is False
    monkeypatch.undo()
    assert w.run_all_pairs(lambda p, g, c: None, ["EUR_CAD"]) is True


def test_released_after_system_exit_in_fetch(tmp_path):
    w = _wfo(tmp_path)

    def die(p, g, c):
        raise SystemExit(3)
    with pytest.raises(SystemExit):
        w.run_all_pairs(die, ["EUR_CAD"])
    assert w.batch_running() is False


def test_released_when_the_tz_mismatch_probe_fails(tmp_path, monkeypatch):
    import tzlocal

    def broken():
        raise RuntimeError("no tz")
    monkeypatch.setattr(tzlocal, "get_localzone_name", broken)
    w = _wfo(tmp_path)
    assert w.run_all_pairs(lambda p, g, c: None, []) is True
    assert w.batch_running() is False


# ── on_start hook (dashboard Telegram) ──────────────────────────────────────

def test_on_start_runs_once_inside_the_guard(tmp_path):
    w, seen = _wfo(tmp_path), []
    w.run_all_pairs(lambda p, g, c: None, ["EUR_CAD"],
                    on_start=lambda: seen.append(w.batch_running()))
    assert seen == [True]


def test_on_start_not_called_for_a_skipped_call(tmp_path):
    w, b, seen = _wfo(tmp_path), Blocker(), []
    t1, _ = _bg(w.run_all_pairs, b, ["EUR_CAD"])
    assert b.entered.wait(T)
    assert w.run_all_pairs(b, ["EUR_CAD"], on_start=lambda: seen.append(1)) is False
    assert seen == []
    b.release.set()
    _join(t1)


def test_on_start_exception_does_not_abort_the_batch(tmp_path):
    w, calls = _wfo(tmp_path), []

    def bad():
        raise RuntimeError("telegram down")

    def fetch(p, g, c):
        calls.append(p)
        return None
    assert w.run_all_pairs(fetch, ["EUR_CAD"], on_start=bad) is True
    assert calls and w.batch_running() is False


# ── restart & topology ──────────────────────────────────────────────────────

def test_new_instance_models_a_restart_and_is_unlocked(tmp_path):
    a, b = _wfo(tmp_path), Blocker()
    t1, _ = _bg(a.run_all_pairs, b, ["EUR_CAD"])
    assert b.entered.wait(T)
    fresh = _wfo(tmp_path)                    # what a restarted process starts with
    assert fresh.batch_running() is False
    assert fresh.run_all_pairs(lambda p, g, c: None, ["EUR_CAD"]) is True
    b.release.set()
    _join(t1)


def test_guard_is_in_memory_only_no_lock_file(tmp_path):
    w = _wfo(tmp_path)
    w.run_all_pairs(lambda p, g, c: None, ["EUR_CAD"])
    assert list(tmp_path.iterdir()) == []


def test_module_singleton_is_shared_by_scheduler_and_dashboard_paths():
    import agents.evaluation_agent as ea
    import engine.wfo_optimizer as mod
    assert ea.wfo_optimizer is mod.wfo_optimizer
    main_src = (_ROOT / "main.py").read_text("utf-8")
    dash_src = (_ROOT / "dashboard" / "app.py").read_text("utf-8")
    assert "from engine.wfo_optimizer import wfo_optimizer" in main_src
    assert "target=wfo_optimizer.run_all_pairs" in main_src
    assert "from engine.wfo_optimizer import wfo_optimizer" in dash_src


def test_dashboard_is_hosted_in_a_thread_of_the_bot_process():
    """Topology pin: if main.py ever launches the dashboard as a subprocess the
    process-local guard no longer covers the button -- this test must then fail."""
    src = (_ROOT / "main.py").read_text("utf-8")
    assert "threading.Thread(target=_run_dash" in src
    assert "dash_app.run(" in src
    launch = src[src.index("from dashboard.app import app as dash_app"):src.index('logger.info("Dashboard:')]
    assert "subprocess" not in launch and "Popen" not in launch and "multiprocessing" not in launch


# ── dashboard helper ────────────────────────────────────────────────────────

@pytest.fixture
def dash(monkeypatch):
    import dashboard.app as dash_app
    import alerts.telegram_alert as tg
    events = []
    monkeypatch.setattr(tg, "send_wfo_started", lambda pairs: events.append(("started", tuple(pairs))) or True)
    monkeypatch.setattr(tg, "send_wfo_complete", lambda s: events.append(("complete",)) or True)
    return dash_app, events


def _join_dash_threads():
    for t in [t for t in threading.enumerate() if t.name == "wfo-dashboard"]:
        t.join(T)


def test_dashboard_busy_shows_clear_message_and_sends_nothing(tmp_path, dash):
    dash_app, events = dash
    w, b = _wfo(tmp_path), Blocker()
    t1, _ = _bg(w.run_all_pairs, b, ["EUR_CAD"])
    assert b.entered.wait(T)
    msg = dash_app._start_wfo_from_dashboard(["EUR_CAD"], b, optimizer=w)
    assert msg == "WFO optimization is already running. No additional batch was started."
    assert events == []
    assert not [t for t in threading.enumerate() if t.name == "wfo-dashboard"]
    b.release.set()
    _join(t1)


def test_dashboard_idle_starts_one_batch_with_start_and_complete_notices(tmp_path, dash):
    dash_app, events = dash
    w = _wfo(tmp_path)
    msg = dash_app._start_wfo_from_dashboard(["EUR_CAD"], lambda p, g, c: None, optimizer=w)
    _join_dash_threads()
    assert "already running" not in msg
    assert events == [("started", ("EUR_CAD",)), ("complete",)]


def test_dashboard_race_past_the_preliminary_check_cannot_double_run_or_double_notify(tmp_path, dash, monkeypatch):
    """Both requests pass the advisory batch_running() check; the lock decides."""
    dash_app, events = dash
    w, b = _wfo(tmp_path), Blocker()
    monkeypatch.setattr(w, "batch_running", lambda: False)        # advisory check always says idle
    dash_app._start_wfo_from_dashboard(["EUR_CAD"], b, optimizer=w)
    assert b.entered.wait(T)
    first = {t for t in threading.enumerate() if t.name == "wfo-dashboard"}
    dash_app._start_wfo_from_dashboard(["EUR_CAD"], b, optimizer=w)
    for t in {t for t in threading.enumerate() if t.name == "wfo-dashboard"} - first:
        t.join(T)                                                 # the loser finishes immediately
    b.release.set()
    _join_dash_threads()
    assert events.count(("started", ("EUR_CAD",))) == 1
    assert events.count(("complete",)) == 1
    assert len([c for c in b.calls if c[1]]) == 2                 # a single batch's two fetches


def test_scheduler_and_dashboard_paths_exclude_each_other(tmp_path, dash):
    dash_app, events = dash
    w, b = _wfo(tmp_path), Blocker()
    t1, o1 = _bg(w.run_all_pairs, b, ["EUR_CAD"])                  # == main.py's wfo-refit thread
    assert b.entered.wait(T)
    assert dash_app._start_wfo_from_dashboard(["EUR_CAD"], b, optimizer=w).startswith("WFO optimization is already")
    b.release.set()
    _join(t1)
    assert o1["ret"] is True and events == []


def test_dashboard_skipped_batch_sends_no_completion(tmp_path, dash, monkeypatch):
    dash_app, events = dash
    w, b = _wfo(tmp_path), Blocker()
    t1, _ = _bg(w.run_all_pairs, b, ["EUR_CAD"])
    assert b.entered.wait(T)
    monkeypatch.setattr(w, "batch_running", lambda: False)
    dash_app._start_wfo_from_dashboard(["EUR_CAD"], b, optimizer=w)
    _join_dash_threads()                                          # loser returned False
    assert events == []
    b.release.set()
    _join(t1)


# ── return contract: nobody depends on None ─────────────────────────────────

def test_scheduler_callback_does_not_use_the_return_value():
    """main.py hands run_all_pairs to Thread(target=...): the value is discarded, so
    None -> bool is invisible to it. The dashboard uses it only to decide whether to
    send the completion notice (covered above)."""
    import re
    src = (_ROOT / "main.py").read_text("utf-8")
    assert not re.search(r"=\s*[\w.]*run_all_pairs\(", src)
    assert "target=wfo_optimizer.run_all_pairs" in src
