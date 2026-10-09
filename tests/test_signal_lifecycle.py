"""
engine/signal_lifecycle.py — Phase 2B signal lifecycle instrumentation
(2026-10-07, see tasks/todo.md "Phase 2B"). Covers the module in isolation:
signal_id format/uniqueness/monotonic persistence, event logging shape,
per-strategy diagnostic labeling (the fix for the c1-c6 mislabeling Phase
2A found), and the terminal_states() query the whole module exists to make
possible. conftest.py's _isolate_signal_lifecycle fixture redirects every
test here to a tmp_path file automatically — none of this touches
data/signal_lifecycle.csv or data/signal_id_seq.json.
"""
import csv
import json

import engine.signal_lifecycle as sl


# ── next_signal_id ───────────────────────────────────────────────────────

def test_signal_id_format():
    sig_id = sl.next_signal_id("GBP_USD", "M30")
    # SIG-{YYYYMMDD}-{PAIR_NO_UNDERSCORE}-{TF}-{NNNNNN}
    parts = sig_id.split("-")
    assert parts[0] == "SIG"
    assert len(parts[1]) == 8 and parts[1].isdigit()   # YYYYMMDD
    assert parts[2] == "GBPUSD"                         # underscore stripped
    assert parts[3] == "M30"
    assert len(parts[4]) == 6 and parts[4].isdigit()    # zero-padded sequence


def test_signal_id_strips_pair_underscore():
    sig_id = sl.next_signal_id("USD_CAD", "H4")
    assert "USDCAD" in sig_id
    assert "USD_CAD" not in sig_id


def test_signal_id_monotonic_and_unique():
    ids = [sl.next_signal_id("EUR_USD", "M30") for _ in range(5)]
    seqs = [int(i.rsplit("-", 1)[1]) for i in ids]
    assert seqs == sorted(seqs)
    assert len(set(ids)) == 5   # no collisions


def test_signal_id_counter_persists_across_calls():
    first = sl.next_signal_id("AUD_JPY", "M30")
    first_seq = int(first.rsplit("-", 1)[1])
    seq_on_disk = json.loads(sl._SEQ_PATH.read_text())["seq"]
    assert seq_on_disk == first_seq
    second = sl.next_signal_id("AUD_JPY", "M30")
    assert int(second.rsplit("-", 1)[1]) == first_seq + 1


def test_signal_id_counter_survives_missing_file(tmp_path, monkeypatch):
    """A fresh bot start (no seq file yet) begins at 1, not a crash."""
    monkeypatch.setattr(sl, "_SEQ_PATH", tmp_path / "does_not_exist_yet.json")
    sig_id = sl.next_signal_id("GBP_USD", "M30")
    assert sig_id.endswith("-000001")


def test_signal_id_counter_survives_corrupt_file(tmp_path, monkeypatch):
    """A corrupt/partial seq file (e.g. a crash mid-write) must not crash
    signal generation — best-effort resume at 0, matching the module's
    documented non-blocking behavior everywhere else."""
    bad = tmp_path / "corrupt_seq.json"
    bad.write_text("{not valid json")
    monkeypatch.setattr(sl, "_SEQ_PATH", bad)
    sig_id = sl.next_signal_id("GBP_USD", "M30")
    assert sig_id.endswith("-000001")


# ── log_event / file shape ───────────────────────────────────────────────

def test_log_event_creates_header_on_first_write():
    sl.log_event(signal_id="SIG-TEST", pair="GBP_USD", timeframe="M30",
                 direction="short", strategy="trend_retest", signal_score=54,
                 stage="SIGNAL_CREATED", signal_status="TRIGGERED")
    with open(sl._LOG_PATH, newline="") as f:
        header = next(csv.reader(f))
    assert header == sl._FIELDNAMES


def test_log_event_row_shape():
    sl.log_event(signal_id="SIG-ROWSHAPE", pair="EUR_USD", timeframe="M30",
                 direction="long", strategy="ema_bounce", signal_score=62,
                 stage="EXECUTED", execution_status="DEMO_EXECUTED",
                 trade_id="12345", mode="demo")
    with open(sl._LOG_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    row = next(r for r in rows if r["signal_id"] == "SIG-ROWSHAPE")
    assert row["pair"] == "EUR_USD"
    assert row["direction"] == "long"
    assert row["strategy"] == "ema_bounce"
    assert row["signal_score"] == "62"
    assert row["stage"] == "EXECUTED"
    assert row["execution_status"] == "DEMO_EXECUTED"
    assert row["trade_id"] == "12345"
    assert row["mode"] == "demo"


def test_log_event_never_raises_on_bad_path(tmp_path, monkeypatch):
    """Matches engine/signal_audit.py's log_signal() contract: a logging
    failure must never interrupt real signal processing."""
    monkeypatch.setattr(sl, "_LOG_PATH", tmp_path / "nonexistent_dir" / "sub" / "x.csv")
    monkeypatch.setattr(sl, "_initialised", True)   # skip mkdir, force the write itself to fail
    sl.log_event(signal_id="SIG-X", pair="GBP_USD", timeframe="M30",
                 direction="long", strategy="ema_bounce", signal_score=50,
                 stage="SIGNAL_CREATED")   # must not raise


def test_log_event_unknown_stage_does_not_raise(caplog):
    sl.log_event(signal_id="SIG-UNKNOWN-STAGE", pair="GBP_USD", timeframe="M30",
                 direction="long", strategy="ema_bounce", signal_score=50,
                 stage="NOT_A_REAL_STAGE")
    with open(sl._LOG_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    assert any(r["signal_id"] == "SIG-UNKNOWN-STAGE" for r in rows)


# ── format_diagnostics — the c1-c6 mislabeling fix ──────────────────────

def test_diagnostics_ema_bounce_labels():
    diag = {"c1": True, "c2": False, "c3": True, "c4": True, "c5": False, "c6": True, "c7": False}
    out = sl.format_diagnostics("ema_bounce", diag)
    assert out == "above_ema=1|h4_aligned=0|touch=1|cci_at_touch=1|cci_recovery=0|macd_confirm=1|macd_rising=0"


def test_diagnostics_trend_retest_labels_differ_from_ema_bounce():
    """The actual bug Phase 2A flagged: the SAME raw c1/c2/c3/c4 keys mean
    something completely different per strategy. This must never collapse
    to the same output for the same raw values."""
    diag = {"h4_bias": True, "c1": True, "c2": True, "c3": False, "c4": True}
    out = sl.format_diagnostics("trend_retest", diag)
    assert out == "h4_bias=1|retest=1|not_invalidated=1|price_action=0|ma_aligned=1"
    assert "cci_at_touch" not in out   # never borrow ema_bounce's vocabulary
    assert "above_ema" not in out


def test_diagnostics_breakout_retest_labels():
    diag = {"c1": True, "c2": False, "c3": True, "c4": False, "c5": True}
    out = sl.format_diagnostics("breakout_retest", diag)
    assert out == "bos=1|retest=0|not_invalidated=1|price_action=0|macd_momentum=1"


def test_diagnostics_empty_diag_returns_empty_string():
    assert sl.format_diagnostics("ema_bounce", {}) == ""
    assert sl.format_diagnostics("ema_bounce", None) == ""


def test_diagnostics_unknown_strategy_returns_empty_not_a_guess():
    diag = {"c1": True, "c2": True}
    assert sl.format_diagnostics("some_future_strategy", diag) == ""


def test_diagnostics_normalizes_numpy_bool_same_as_python_bool():
    """Regression: strategy diag dicts mix plain Python bool (hardcoded
    literals in early-return branches) with numpy's bool scalar (computed
    comparisons) in the SAME dict — confirmed live against real OANDA data,
    strategy_ema_cci_macd's c1 came back numpy.bool while c2-c7 were plain
    Python bool. isinstance(v, bool) alone misses numpy.bool (not a bool
    subclass despite the same display name), producing a diagnostics
    string like 'above_ema=True|touch=1' — mixed, inconsistent formatting
    in the exact field this module exists to make trustworthy."""
    import numpy as np
    diag = {"c1": np.bool_(True), "c2": False, "c3": np.True_}
    out = sl.format_diagnostics("ema_bounce", diag)
    assert out == "above_ema=1|h4_aligned=0|touch=1"


def test_diagnostics_partial_diag_only_includes_present_keys():
    """A diag dict missing a key (e.g. an early-return path that never set
    c5) should silently omit it, not crash or fabricate a value."""
    diag = {"c1": True, "c2": False}
    out = sl.format_diagnostics("breakout_retest", diag)
    assert out == "bos=1|retest=0"


# ── terminal_states ───────────────────────────────────────────────────────

def test_terminal_states_returns_last_row_per_signal_id():
    sid = "SIG-TERMINAL-TEST"
    sl.log_event(signal_id=sid, pair="GBP_USD", timeframe="M30", direction="short",
                 strategy="trend_retest", signal_score=54, stage="SIGNAL_CREATED",
                 signal_status="TRIGGERED")
    sl.log_event(signal_id=sid, pair="GBP_USD", timeframe="M30", direction="short",
                 strategy="trend_retest", signal_score=54, stage="TRIGGERED",
                 signal_status="TRIGGERED")
    sl.log_event(signal_id=sid, pair="GBP_USD", timeframe="M30", direction="short",
                 strategy="trend_retest", signal_score=54, stage="GATE_APPROVED")
    sl.log_event(signal_id=sid, pair="GBP_USD", timeframe="M30", direction="short",
                 strategy="trend_retest", signal_score=54, stage="EXECUTED",
                 execution_status="DEMO_EXECUTED", trade_id="999", mode="demo")
    states = sl.terminal_states()
    assert states[sid]["stage"] == "EXECUTED"
    assert states[sid]["trade_id"] == "999"
    assert states[sid]["execution_status"] == "DEMO_EXECUTED"


def test_terminal_states_distinguishes_signals_stuck_mid_pipeline():
    """A signal whose last logged stage is TRIGGERED (nothing followed) is
    exactly the Phase 2A 'unaccounted' anomaly — now directly queryable
    instead of reconstructed after the fact from two other files."""
    sid = "SIG-STUCK"
    sl.log_event(signal_id=sid, pair="NZD_USD", timeframe="M30", direction="long",
                 strategy="ema_bounce", signal_score=53, stage="SIGNAL_CREATED",
                 signal_status="TRIGGERED")
    sl.log_event(signal_id=sid, pair="NZD_USD", timeframe="M30", direction="long",
                 strategy="ema_bounce", signal_score=53, stage="TRIGGERED",
                 signal_status="TRIGGERED")
    states = sl.terminal_states()
    assert states[sid]["stage"] == "TRIGGERED"   # stuck — no GATE_APPROVED/BLOCKED ever followed


def test_terminal_states_empty_file_returns_empty_dict(tmp_path):
    assert sl.terminal_states(tmp_path / "never_written.csv") == {}


def test_terminal_states_watching_signal_is_correctly_terminal_at_creation():
    """WATCHING/SCANNING/ATR-gated signals never reach main.py by design —
    their single SIGNAL_CREATED row IS their complete terminal state, not
    a gap to flag."""
    sid = "SIG-WATCHING"
    sl.log_event(signal_id=sid, pair="CHF_JPY", timeframe="M30", direction="short",
                 strategy="ema_bounce", signal_score=48, stage="SIGNAL_CREATED",
                 signal_status="WATCHING")
    states = sl.terminal_states()
    assert states[sid]["stage"] == "SIGNAL_CREATED"
    assert states[sid]["signal_status"] == "WATCHING"
