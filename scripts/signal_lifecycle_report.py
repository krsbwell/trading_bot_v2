"""
Signal Lifecycle Report — Phase 2B follow-up (2026-10-07, see
tasks/todo.md "Phase 2B" / "Phase 2C").

Reads data/signal_lifecycle.csv and answers, in one pass, the question
Phase 2A had to spend an afternoon reconstructing by hand from three
different files: what happened to every signal. Two kinds of query over
the same data:
  - PIPELINE counts: did this signal_id's event stream ever reach stage X,
    regardless of what happened after (e.g. "163 signals reached
    TRIGGERED" counts every signal that passed through there, whether it
    went on to execute or got blocked three stages later).
  - TERMINAL counts: what is this signal_id's LAST logged stage (from
    engine.signal_lifecycle.terminal_states()). A terminal stage of
    TRIGGERED or OPEN_TRADE_CALLED with nothing after it is exactly the
    "signal vanished mid-pipeline" anomaly Phase 2A spent so long chasing
    for the 2026-07-31-onward period — now a direct count instead of a
    forensic reconstruction.

Usage:
    <venv>/Scripts/python.exe scripts/signal_lifecycle_report.py
    <venv>/Scripts/python.exe scripts/signal_lifecycle_report.py --trace SIG-20261007-GBPUSD-M30-000123
"""
import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.signal_lifecycle import _LOG_PATH, terminal_states

_STAGE_ORDER = ["SIGNAL_CREATED", "TRIGGERED", "GATE_APPROVED", "GATE_BLOCKED",
                "OPEN_TRADE_CALLED", "EXECUTED", "EXEC_FAILED"]


def _load_rows(path=None):
    p = Path(path) if path else _LOG_PATH
    if not p.exists():
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def _stages_reached(rows) -> dict:
    """{signal_id: set(stages that ever appeared)} — the PIPELINE view."""
    reached = defaultdict(set)
    for r in rows:
        reached[r["signal_id"]].add(r["stage"])
    return reached


def trace(signal_id: str, rows) -> None:
    """Print one signal_id's full event history in order — the direct
    answer to 'follow SIG-...-000123 through the entire lifecycle'."""
    matches = [r for r in rows if r["signal_id"] == signal_id]
    if not matches:
        print(f"No rows found for {signal_id}")
        return
    print(f"=== {signal_id} ===")
    print(f"pair={matches[0]['pair']}  direction={matches[0]['direction']}  "
          f"strategy={matches[0]['strategy']}  score={matches[0]['signal_score']}")
    print()
    for r in matches:
        extra = ", ".join(f"{k}={r[k]}" for k in
                           ("signal_status", "execution_status", "trade_id", "failure_reason", "mode")
                           if r.get(k))
        print(f"  {r['event_time']}  {r['stage']:<18} {extra}")
        if r.get("diagnostics"):
            print(f"      diagnostics: {r['diagnostics']}")


def report(rows) -> None:
    if not rows:
        print("data/signal_lifecycle.csv is empty or doesn't exist yet — nothing to report.")
        return

    reached = _stages_reached(rows)
    term = terminal_states()
    n_signals = len(reached)

    def pipeline_count(stage):
        return sum(1 for stages in reached.values() if stage in stages)

    def terminal_count(stage):
        return sum(1 for row in term.values() if row["stage"] == stage)

    created         = n_signals
    triggered       = pipeline_count("TRIGGERED")
    gate_approved   = pipeline_count("GATE_APPROVED")
    gate_blocked    = pipeline_count("GATE_BLOCKED")
    open_called     = pipeline_count("OPEN_TRADE_CALLED")
    executed        = pipeline_count("EXECUTED")
    exec_failed     = pipeline_count("EXEC_FAILED")

    orphan_triggered  = terminal_count("TRIGGERED")
    orphan_approved   = terminal_count("GATE_APPROVED")
    orphan_opencalled = terminal_count("OPEN_TRADE_CALLED")

    print("Signal Lifecycle Report")
    print("-" * 40)
    print(f"Signals created:    {created:>8,}")
    print(f"Triggered:          {triggered:>8,}" +
          (f"   ({100*triggered/created:.1f}% of created)" if created else ""))
    print(f"Gate blocked:       {gate_blocked:>8,}")
    print(f"Approved:           {gate_approved:>8,}")
    print(f"Open trade called:  {open_called:>8,}")
    print(f"Executed:           {executed:>8,}")
    print(f"Execution failed:   {exec_failed:>8,}")
    print()
    print("Orphaned signals (reached this stage, nothing followed):")
    print(f"  TRIGGERED:          {orphan_triggered:>6,}")
    print(f"  GATE_APPROVED:      {orphan_approved:>6,}")
    print(f"  OPEN_TRADE_CALLED:  {orphan_opencalled:>6,}")
    if orphan_triggered or orphan_approved or orphan_opencalled:
        print("  ^ investigate these — same anomaly class Phase 2A found 41 of")
        print("    (2026-07-31 onward) with no surviving explanation.")
    print()

    # Sanity-check the funnel invariants — a mismatch here is itself a finding.
    if gate_approved + gate_blocked != triggered:
        print(f"  NOTE: approved({gate_approved}) + blocked({gate_blocked}) "
              f"!= triggered({triggered}) — a signal is reaching TRIGGERED without "
              f"a matching GATE_APPROVED/GATE_BLOCKED, or vice versa. Worth a look.")
    if executed + exec_failed != open_called:
        print(f"  NOTE: executed({executed}) + exec_failed({exec_failed}) "
              f"!= open_trade_called({open_called}) — same kind of mismatch, "
              f"downstream of the open_trade() call.")

    # ── Breakdowns ───────────────────────────────────────────────────────
    created_rows = [r for r in rows if r["stage"] == "SIGNAL_CREATED"]

    def _breakdown(title, key_fn, source_rows):
        print(f"-- {title} --")
        c = Counter(key_fn(r) for r in source_rows)
        for k, v in c.most_common():
            print(f"  {v:>6,}  {k}")
        print()

    _breakdown("Signals created, by pair", lambda r: r["pair"], created_rows)
    _breakdown("Signals created, by timeframe", lambda r: r["timeframe"], created_rows)
    _breakdown("Signals created, by strategy", lambda r: r["strategy"], created_rows)

    blocked_rows = [r for r in rows if r["stage"] == "GATE_BLOCKED"]
    _breakdown("Gate-blocked reasons", lambda r: r["failure_reason"] or "(none given)", blocked_rows)

    exec_rows = [r for r in rows if r["stage"] == "EXECUTED"]
    if exec_rows:
        _breakdown("Executed trades, by pair", lambda r: r["pair"], exec_rows)
        _breakdown("Executed trades, by strategy", lambda r: r["strategy"], exec_rows)
        _breakdown("Executed trades, by direction", lambda r: r["direction"], exec_rows)
        _breakdown("Executed trades, by timeframe", lambda r: r["timeframe"], exec_rows)
        _breakdown("Executed trades, by execution_status", lambda r: r["execution_status"], exec_rows)
    else:
        print("-- No executed trades yet --")
        print()

    failed_rows = [r for r in rows if r["stage"] == "EXEC_FAILED"]
    if failed_rows:
        _breakdown("Execution failures, by reason", lambda r: r["failure_reason"] or "(none given)", failed_rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Signal lifecycle report / tracer")
    parser.add_argument("--trace", metavar="SIGNAL_ID",
                         help="Print the full event history for one signal_id instead of the summary report.")
    args = parser.parse_args()

    all_rows = _load_rows()
    if args.trace:
        trace(args.trace, all_rows)
    else:
        report(all_rows)
