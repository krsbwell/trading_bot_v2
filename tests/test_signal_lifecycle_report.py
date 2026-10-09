"""
scripts/signal_lifecycle_report.py — covers the two counting modes
(pipeline vs terminal) against a synthetic event stream, since getting
these two confused is exactly the kind of mistake that would silently
misreport "how many signals reached TRIGGERED" as "how many are stuck at
TRIGGERED" — the opposite of what Phase 2C needs.
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import signal_lifecycle_report as slr


def _row(signal_id, stage, **kw):
    base = {"event_time": "2026-10-07 10:00:00", "signal_id": signal_id,
            "pair": "GBP_USD", "timeframe": "M30", "direction": "short",
            "strategy": "trend_retest", "signal_score": "54", "stage": stage,
            "signal_status": "", "execution_status": "", "trade_id": "",
            "failure_reason": "", "mode": "", "diagnostics": ""}
    base.update(kw)
    return base


def test_pipeline_count_includes_signals_that_moved_past_the_stage():
    """A signal that reached TRIGGERED then went on to EXECUTED must still
    count toward 'triggered' — this is the distinction the whole report
    exists to get right."""
    rows = [
        _row("A", "SIGNAL_CREATED"), _row("A", "TRIGGERED"),
        _row("A", "GATE_APPROVED"), _row("A", "OPEN_TRADE_CALLED"),
        _row("A", "EXECUTED", execution_status="DEMO_EXECUTED", trade_id="1"),
    ]
    reached = slr._stages_reached(rows)
    assert "TRIGGERED" in reached["A"]
    assert "EXECUTED" in reached["A"]


def test_terminal_state_is_last_stage_only():
    rows = [
        _row("A", "SIGNAL_CREATED"), _row("A", "TRIGGERED"),
        _row("A", "GATE_APPROVED"), _row("A", "OPEN_TRADE_CALLED"),
        _row("A", "EXECUTED", execution_status="DEMO_EXECUTED", trade_id="1"),
    ]
    # write + read through the real module so terminal_states() sees it
    import engine.signal_lifecycle as sl
    for r in rows:
        sl.log_event(signal_id=r["signal_id"], pair=r["pair"], timeframe=r["timeframe"],
                     direction=r["direction"], strategy=r["strategy"],
                     signal_score=r["signal_score"], stage=r["stage"],
                     execution_status=r["execution_status"], trade_id=r["trade_id"])
    states = sl.terminal_states()
    assert states["A"]["stage"] == "EXECUTED"


def test_orphaned_signal_detected_via_terminal_not_pipeline():
    """A signal stuck at TRIGGERED (nothing followed) must show up as an
    orphan — the exact Phase 2A anomaly this tool exists to surface
    immediately instead of after weeks of forensic reconstruction."""
    import engine.signal_lifecycle as sl
    sl.log_event(signal_id="STUCK", pair="NZD_USD", timeframe="M30",
                 direction="long", strategy="ema_bounce", signal_score=53,
                 stage="SIGNAL_CREATED", signal_status="TRIGGERED")
    sl.log_event(signal_id="STUCK", pair="NZD_USD", timeframe="M30",
                 direction="long", strategy="ema_bounce", signal_score=53,
                 stage="TRIGGERED", signal_status="TRIGGERED")
    states = sl.terminal_states()
    assert states["STUCK"]["stage"] == "TRIGGERED"   # orphaned — report.py flags this


def test_report_runs_without_error_on_empty_file(capsys):
    slr.report([])
    out = capsys.readouterr().out
    assert "nothing to report" in out


def test_report_funnel_invariant_holds_on_clean_data(capsys):
    rows = [
        _row("A", "SIGNAL_CREATED"), _row("A", "TRIGGERED"),
        _row("A", "GATE_APPROVED"), _row("A", "OPEN_TRADE_CALLED"),
        _row("A", "EXECUTED", execution_status="DEMO_EXECUTED", trade_id="1"),
        _row("B", "SIGNAL_CREATED"), _row("B", "TRIGGERED"),
        _row("B", "GATE_BLOCKED", failure_reason="ML gate: P(win)=0.3"),
    ]
    import engine.signal_lifecycle as sl
    for r in rows:
        sl.log_event(signal_id=r["signal_id"], pair=r["pair"], timeframe=r["timeframe"],
                     direction=r["direction"], strategy=r["strategy"],
                     signal_score=r["signal_score"], stage=r["stage"],
                     execution_status=r["execution_status"], trade_id=r["trade_id"],
                     failure_reason=r["failure_reason"])
    slr.report(rows)
    out = capsys.readouterr().out
    assert "NOTE:" not in out   # invariants hold, no mismatch flagged
    assert "Triggered:                 2" in out
    assert "Executed:                  1" in out


def test_trace_prints_full_history_in_order(capsys):
    rows = [
        _row("SIG-X", "SIGNAL_CREATED", diagnostics="retest=1|price_action=1"),
        _row("SIG-X", "TRIGGERED"),
        _row("SIG-X", "GATE_BLOCKED", failure_reason="Cooldown 2.0h / 4h"),
    ]
    slr.trace("SIG-X", rows)
    out = capsys.readouterr().out
    assert "SIGNAL_CREATED" in out
    assert "TRIGGERED" in out
    assert "GATE_BLOCKED" in out
    assert "Cooldown 2.0h / 4h" in out
    assert "retest=1" in out


def test_trace_unknown_signal_id_does_not_crash(capsys):
    slr.trace("SIG-DOES-NOT-EXIST", [])
    out = capsys.readouterr().out
    assert "No rows found" in out
