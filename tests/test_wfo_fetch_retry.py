"""
Phase 5D-3 — bounded retries for transient historical candle-fetch failures,
inside the WFO path only (engine/wfo_optimizer.py).

Why: the 10-25 run lost EUR_CAD and GBP_JPY (and 10-18 lost GBP_AUD) to a single
transient Cloudflare 504 / connection reset each, five seconds after the pair
started -- whole pairs forfeited for a one-off network blip.

Scope guarantees tested here: max 3 attempts; only HTTP 502/503/504, connection
resets and timeouts retry; auth / invalid-request / other statuses never retry;
the ORIGINAL exception is re-raised once retries are exhausted (existing
failure reporting unchanged); only the failed fetch is retried (never the grid
search or the other timeframe's fetch); the shared OANDA connector is untouched.

No network: every test uses fakes, and an autouse fixture makes any real
socket creation fail loudly. Backoff sleeps are replaced by a recorder.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import socket

import pandas as pd
import pytest
import requests
from oandapyV20.exceptions import V20Error
from urllib3.exceptions import ProtocolError

import engine.wfo_optimizer as wfo_mod
from engine.wfo_optimizer import WFOOptimizer, _fetch_with_retry, _transient_fetch_reason


@pytest.fixture(autouse=True)
def _no_network_no_real_sleep(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("test attempted a real network connection")
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    sleeps = []
    monkeypatch.setattr(wfo_mod, "_retry_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(wfo_mod, "_retry_jitter", lambda lo, hi: 1.0)   # neutral by default
    return sleeps


@pytest.fixture
def sleeps(_no_network_no_real_sleep):
    return _no_network_no_real_sleep


def _wrapped_reset():
    """What requests really raises for a reset: ConnectionError(ProtocolError(.., ConnectionResetError))."""
    return requests.exceptions.ConnectionError(
        ProtocolError("Connection aborted.", ConnectionResetError(10054, "forcibly closed")))


class _Flaky:
    """get_candles stand-in: raises the queued exceptions in order, then returns a frame."""
    def __init__(self, *failures):
        self.failures = list(failures)
        self.calls = []

    def __call__(self, pair, gran, count):
        self.calls.append((pair, gran, count))
        if self.failures:
            raise self.failures.pop(0)
        return pd.DataFrame({"close": [1.0]})


# ═══════════════════════════════════════════════════════════════════════════════
# Classification
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("code", [502, 503, 504])
def test_retryable_http_statuses(code):
    assert _transient_fetch_reason(V20Error(code, "<html>gateway</html>")) == f"HTTP {code}"


@pytest.mark.parametrize("code", [400, 401, 403, 404, 405, 408, 429, 500, 501, 505])
def test_other_http_statuses_are_not_retried(code):
    assert _transient_fetch_reason(V20Error(code, "x")) is None


def test_auth_and_invalid_request_are_never_retried():
    assert _transient_fetch_reason(V20Error(401, "Insufficient authorization")) is None
    assert _transient_fetch_reason(V20Error(400, "Invalid instrument")) is None


@pytest.mark.parametrize("exc", [
    ConnectionResetError(10054, "reset"),
    _wrapped_reset(),
])
def test_connection_reset_is_retried_even_when_wrapped(exc):
    assert _transient_fetch_reason(exc) == "connection reset"


@pytest.mark.parametrize("exc", [
    requests.exceptions.ConnectTimeout("connect timed out"),
    requests.exceptions.ReadTimeout("read timed out"),
    TimeoutError("timed out"),
])
def test_timeouts_are_retried(exc):
    assert _transient_fetch_reason(exc) == "timeout"


@pytest.mark.parametrize("exc", [
    requests.exceptions.ConnectionError(ConnectionRefusedError(10061, "refused")),   # not approved
    requests.exceptions.ConnectionError("Name or service not known"),                # DNS, not approved
    requests.exceptions.SSLError("bad cert"),
    ValueError("bad"), KeyError("k"), RuntimeError("boom"), Exception("generic"),
])
def test_everything_else_is_not_retried(exc):
    assert _transient_fetch_reason(exc) is None


def test_exception_chain_walk_terminates_on_cycles():
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__cause__, b.__cause__ = b, a
    assert _transient_fetch_reason(a) is None


# ═══════════════════════════════════════════════════════════════════════════════
# _fetch_with_retry behaviour
# ═══════════════════════════════════════════════════════════════════════════════

def test_success_first_try_makes_one_call_and_never_sleeps(sleeps):
    fn = _Flaky()
    df = _fetch_with_retry(fn, "EUR_CAD", "M30", 1500)
    assert len(fn.calls) == 1 and sleeps == [] and len(df) == 1


def test_recovers_on_second_attempt_and_does_not_make_a_third_request(sleeps):
    fn = _Flaky(V20Error(504, "<html/>"))
    df = _fetch_with_retry(fn, "EUR_CAD", "M30", 1500)
    assert len(fn.calls) == 2, "a successful second attempt must not trigger a third request"
    assert len(sleeps) == 1 and len(df) == 1


def test_recovers_on_third_attempt(sleeps):
    fn = _Flaky(V20Error(503, "x"), ConnectionResetError("r"))
    assert len(_fetch_with_retry(fn, "GBP_JPY", "H4", 200)) == 1
    assert len(fn.calls) == 3 and len(sleeps) == 2


def test_exhausted_retries_raise_the_original_exception_after_exactly_three_attempts(sleeps):
    errs = [V20Error(504, "a"), V20Error(502, "b"), V20Error(504, "c")]
    fn = _Flaky(*errs)
    with pytest.raises(V20Error) as ei:
        _fetch_with_retry(fn, "EUR_CAD", "M30", 1500)
    assert ei.value is errs[2], "the last original exception object must propagate unchanged"
    assert len(fn.calls) == 3 and len(sleeps) == 2, "max 3 attempts, 2 backoffs, no sleep after the last"


@pytest.mark.parametrize("exc", [V20Error(401, "auth"), V20Error(400, "bad"), V20Error(404, "nf"),
                                 V20Error(429, "slow down"), ValueError("x")])
def test_non_retryable_errors_fail_immediately(exc, sleeps):
    fn = _Flaky(exc)
    with pytest.raises(type(exc)) as ei:
        _fetch_with_retry(fn, "EUR_CAD", "M30", 1500)
    assert ei.value is exc and len(fn.calls) == 1 and sleeps == []


def test_a_non_retryable_error_after_a_transient_one_stops_the_retries(sleeps):
    auth = V20Error(401, "auth")
    fn = _Flaky(V20Error(504, "x"), auth)
    with pytest.raises(V20Error) as ei:
        _fetch_with_retry(fn, "EUR_CAD", "M30", 1500)
    assert ei.value is auth and len(fn.calls) == 2


def test_backoff_is_about_5s_then_20s_with_bounded_jitter(monkeypatch, sleeps):
    seen = []
    monkeypatch.setattr(wfo_mod, "_retry_jitter", lambda lo, hi: (seen.append((lo, hi)), 1.2)[1])
    fn = _Flaky(V20Error(504, "a"), V20Error(504, "b"))
    _fetch_with_retry(fn, "EUR_CAD", "M30", 1500)
    assert sleeps == pytest.approx([5.0 * 1.2, 20.0 * 1.2])
    assert all(0.7 <= lo < 1.0 < hi <= 1.3 for lo, hi in seen) and len(seen) == 2


def test_real_jitter_stays_inside_bounds():
    import random
    for _ in range(200):
        j = random.uniform(1 - wfo_mod._FETCH_JITTER_FRAC, 1 + wfo_mod._FETCH_JITTER_FRAC)
        assert 5.0 * (1 - wfo_mod._FETCH_JITTER_FRAC) <= 5.0 * j <= 5.0 * (1 + wfo_mod._FETCH_JITTER_FRAC)
    assert wfo_mod._FETCH_MAX_ATTEMPTS == 3 and tuple(wfo_mod._FETCH_BACKOFF_SECS) == (5.0, 20.0)


def test_each_retry_is_logged_with_attempt_and_reason(caplog, sleeps):
    fn = _Flaky(V20Error(504, "<html/>"), _wrapped_reset())
    with caplog.at_level(logging.INFO):
        _fetch_with_retry(fn, "EUR_CAD", "M30", 1500)
    msgs = [r.message for r in caplog.records]
    retry_lines = [m for m in msgs if "retrying" in m]
    assert len(retry_lines) == 2
    assert "EUR_CAD" in retry_lines[0] and "M30" in retry_lines[0] and "attempt 1/3" in retry_lines[0] and "HTTP 504" in retry_lines[0]
    assert "attempt 2/3" in retry_lines[1] and "connection reset" in retry_lines[1]
    assert any("recovered" in m and "attempt 3/3" in m for m in msgs)


def test_giving_up_is_logged_once(caplog, sleeps):
    fn = _Flaky(V20Error(504, "a"), V20Error(504, "b"), V20Error(504, "c"))
    with caplog.at_level(logging.INFO), pytest.raises(V20Error):
        _fetch_with_retry(fn, "EUR_CAD", "M30", 1500)
    assert sum("giving up" in r.message for r in caplog.records) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Wiring in run_all_pairs: only the failed fetch retries; reporting is unchanged
# ═══════════════════════════════════════════════════════════════════════════════

def _candles(n):
    idx = pd.date_range("2026-01-04 22:00", periods=n, freq="30min")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}, index=idx)


@pytest.fixture
def wfo(tmp_path):
    return WFOOptimizer(save_path=tmp_path / "wfo.json")


def _spy_run(monkeypatch, seen):
    def _run(self, pair, h1, h4, market="forex"):
        seen.append(pair)
        self._last_fail_reason = "failed_oos"
        return None
    monkeypatch.setattr(WFOOptimizer, "run", _run)


def test_transient_h4_failure_retries_only_the_h4_fetch(wfo, monkeypatch, sleeps):
    calls, ran = [], []
    boom = [V20Error(504, "<html/>")]

    def get_candles(pair, gran, count):
        calls.append((pair, gran, count))
        if gran == "H4" and boom:
            raise boom.pop(0)
        return _candles(300)
    _spy_run(monkeypatch, ran)
    wfo.run_all_pairs(get_candles, ["GBP_JPY"])
    assert [g for _p, g, _c in calls] == ["M30", "H4", "H4"], "M30 must NOT be re-fetched"
    assert ran == ["GBP_JPY"], "the pair must proceed to run() after recovery"


def test_transient_m30_failure_recovers_and_pair_proceeds(wfo, monkeypatch, sleeps):
    calls, ran = [], []
    boom = [ConnectionResetError(10054, "reset")]

    def get_candles(pair, gran, count):
        calls.append(gran)
        if gran == "M30" and boom:
            raise boom.pop(0)
        return _candles(300)
    _spy_run(monkeypatch, ran)
    wfo.run_all_pairs(get_candles, ["EUR_CAD"])
    assert calls == ["M30", "M30", "H4"] and ran == ["EUR_CAD"]


def test_exhausted_retries_keep_the_existing_failure_reporting(wfo, monkeypatch, sleeps, caplog):
    """Pair is counted as 'raised an exception', logged by the EXISTING handler,
    the grid search never starts, and the batch continues with the next pair."""
    ran, per_pair = [], {"EUR_CAD": 0}

    def get_candles(pair, gran, count):
        if pair == "EUR_CAD":
            per_pair["EUR_CAD"] += 1
            raise V20Error(504, "<html>Cloudflare</html>")
        return _candles(300)
    _spy_run(monkeypatch, ran)
    with caplog.at_level(logging.INFO):
        wfo.run_all_pairs(get_candles, ["EUR_CAD", "GBP_JPY"])
    assert per_pair["EUR_CAD"] == 3
    assert ran == ["GBP_JPY"], "failed pair never reaches run(); next pair still processed"
    assert any(r.levelno == logging.ERROR and "WFO EUR_CAD failed" in r.message for r in caplog.records)
    summary = next(r.message for r in caplog.records if "WFO batch complete" in r.message)
    assert "1 raised an exception" in summary and "2 attempted" in summary


def test_auth_failure_is_not_retried_at_the_batch_level(wfo, monkeypatch, sleeps):
    n = {"calls": 0}

    def get_candles(pair, gran, count):
        n["calls"] += 1
        raise V20Error(401, "Insufficient authorization")
    _spy_run(monkeypatch, [])
    wfo.run_all_pairs(get_candles, ["EUR_CAD"])
    assert n["calls"] == 1 and sleeps == []


def test_none_result_is_not_treated_as_a_transient_failure(wfo, monkeypatch, sleeps):
    n = {"calls": 0}

    def get_candles(pair, gran, count):
        n["calls"] += 1
        return None
    _spy_run(monkeypatch, [])
    wfo.run_all_pairs(get_candles, ["EUR_CAD"])
    # existing flow fetches both timeframes before the None check: exactly one
    # request each, no retry of an empty/None result
    assert n["calls"] == 2 and sleeps == []


def test_fetch_sizes_are_unchanged_by_the_retry_wrapper(wfo, monkeypatch, sleeps):
    asked = []

    def get_candles(pair, gran, count):
        asked.append((gran, count))
        return _candles(300)
    _spy_run(monkeypatch, [])
    wfo.run_all_pairs(get_candles, ["EUR_JPY"])
    assert asked == [("M30", 1500), ("H4", 1440 // 8 + 20)]


def test_retry_never_wraps_or_restarts_the_grid_search(wfo, monkeypatch, sleeps):
    """A failure INSIDE run() (not a fetch) must not be retried or re-run."""
    runs = []

    def _run(self, pair, h1, h4, market="forex"):
        runs.append(pair)
        raise V20Error(504, "raised from inside the optimisation")
    monkeypatch.setattr(WFOOptimizer, "run", _run)
    wfo.run_all_pairs(lambda p, g, c: _candles(300), ["EUR_CAD"])
    assert runs == ["EUR_CAD"] and sleeps == []


# ═══════════════════════════════════════════════════════════════════════════════
# Blast radius
# ═══════════════════════════════════════════════════════════════════════════════

def test_shared_connector_has_no_retry_logic():
    """The retry lives in engine/wfo_optimizer.py only; live scans and orders
    go through the connector, which must stay exactly as it was."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "connectors", "oanda_connector.py"),
               encoding="utf-8").read()
    assert "_fetch_with_retry" not in src and "retry" not in src.lower()
