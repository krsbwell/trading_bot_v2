"""
Signal Lifecycle Instrumentation — Phase 2B (2026-10-07).

Traces every signal that scores > 0 from creation through its terminal
state, with a stable signal_id that survives the whole pipeline. Built in
response to Phase 2A's forensic trace of the 214 historical TRIGGERED rows
in signal_audit.csv: 41 of them (2026-07-31 onward, where ground-truth
trade records exist to check against) could not be matched to either a
real trade or a main.py-level block, and every gate between TRIGGERED and
execution already audits on rejection — meaning the gap is an
OBSERVABILITY problem, not a provable decision-logic bug (see
tasks/todo.md "Phase 2A"). This module closes that gap going forward:
signal_id's last row in data/signal_lifecycle.csv IS its documented
terminal state, no cross-referencing required.

Explicit scope (per user's instructions): this is LOGGING ONLY. No
strategy logic, entry thresholds, risk parameters, or ML behavior change
as a result of this module existing — every call site below is additive,
placed alongside an existing decision, never inside one.

Lifecycle (see tasks/todo.md "Phase 2B" for the full design rationale):
    SIGNAL_CREATED      engine/signal_engine.py — score > 0, carries
                        whatever the engine itself concludes (TRIGGERED /
                        WATCHING / SCANNING / ATR-gate-blocked) as
                        signal_status. WATCHING/SCANNING/ATR-blocked
                        signals never reach main.py — this row IS their
                        complete, correct terminal state, not a gap.
    TRIGGERED           engine/signal_engine.py — only when signal_status
                        == TRIGGERED, marking handoff to main.py.
    GATE_APPROVED /
    GATE_BLOCKED        main.py::_process_pair() — the 7-stage gate chain
                        (score floor, ML, H4/ATR, news, same-candle, risk,
                        cooldown). failure_reason carries the exact
                        existing reject_reason string.
    OPEN_TRADE_CALLED   main.py — immediately before PaperTrader/
                        TradeManager.open_trade().
    EXECUTED /
    EXEC_FAILED         main.py — immediately after. EXECUTED carries
                        trade_id and a mode-specific execution_status
                        (PAPER_EXECUTED / DEMO_EXECUTED / LIVE_EXECUTED,
                        read from config.MODE at that instant — paper,
                        demo and live are materially different things per
                        the user's explicit requirement). EXEC_FAILED
                        carries failure_reason = EXCEPTION (caught) or
                        ORDER_REJECTED (open_trade() returned None).

signal_id format: SIG-{YYYYMMDD}-{PAIR_NO_UNDERSCORE}-{TIMEFRAME}-{NNNNNN}
e.g. SIG-20261007-GBPUSD-M30-000123. The sequence number is a persistent
global monotonic counter (data/signal_id_seq.json) — never resets, so
there's no midnight-rollover edge case to get wrong.

Diagnostics: strategy-specific, NOT forced into the legacy c1-c6
ema_bounce-shaped schema that engine/signal_audit.py uses — that
mislabeling (e.g. a trend_retest signal's "price-action-confirmed" flag
landing in a column named c4_cci_at_touch) is a real, separate bug this
module does not touch in the legacy file; untangling it there risks
breaking something that reads that exact schema. Instead,
format_diagnostics() builds a correctly-labeled pipe-joined string per
strategy from that strategy's own get_last_diag() output, confirmed
against each strategy module's actual source (not guessed from column
names):
    ema_bounce       (strategy_ema_cci_macd.py, c1-c7):
                     above_ema, h4_aligned, touch, cci_at_touch,
                     cci_recovery, macd_confirm, macd_rising
    trend_retest     (strategy_trend_retest.py, h4_bias + c1-c4):
                     h4_bias, retest, not_invalidated, price_action,
                     ma_aligned
    breakout_retest  (strategy_breakout_retest.py, c1-c5):
                     bos, retest, not_invalidated, price_action,
                     macd_momentum
"""

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

_LOG_PATH = Path("data/signal_lifecycle.csv")
_SEQ_PATH = Path("data/signal_id_seq.json")

_FIELDNAMES = [
    "event_time", "signal_id", "pair", "timeframe", "direction", "strategy",
    "signal_score", "stage", "signal_status", "execution_status",
    "trade_id", "failure_reason", "mode", "diagnostics",
]

_STAGES = (
    "SIGNAL_CREATED", "TRIGGERED", "GATE_APPROVED", "GATE_BLOCKED",
    "OPEN_TRADE_CALLED", "EXECUTED", "EXEC_FAILED",
)

# Per-strategy diagnostic key -> human label, in the order each strategy's
# own get_last_diag() dict defines them. See module docstring for the
# source line each mapping was confirmed against.
_DIAG_LABELS = {
    "ema_bounce": [
        ("c1", "above_ema"), ("c2", "h4_aligned"), ("c3", "touch"),
        ("c4", "cci_at_touch"), ("c5", "cci_recovery"),
        ("c6", "macd_confirm"), ("c7", "macd_rising"),
    ],
    "trend_retest": [
        ("h4_bias", "h4_bias"), ("c1", "retest"), ("c2", "not_invalidated"),
        ("c3", "price_action"), ("c4", "ma_aligned"),
    ],
    "breakout_retest": [
        ("c1", "bos"), ("c2", "retest"), ("c3", "not_invalidated"),
        ("c4", "price_action"), ("c5", "macd_momentum"),
    ],
}

_lock = Lock()
_initialised = False


def _ensure_file() -> None:
    global _initialised
    if _initialised:
        return
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _LOG_PATH.exists():
        with open(_LOG_PATH, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=_FIELDNAMES).writeheader()
    _initialised = True


def next_signal_id(pair: str, timeframe: str) -> str:
    """Allocate the next signal_id. Persistent, global, monotonic — never
    resets, so there's no midnight-UTC rollover race to get wrong. Safe to
    call from the single trading-loop process this bot runs as (no
    cross-process locking beyond the module-level Lock, matching every
    other piece of file-backed state in this codebase — see
    single_instance.py's docstring for why a second real trading process
    should never be running concurrently in the first place)."""
    with _lock:
        seq = 0
        try:
            if _SEQ_PATH.exists():
                seq = json.loads(_SEQ_PATH.read_text()).get("seq", 0)
        except Exception as exc:
            logger.warning("signal_lifecycle: couldn't read seq file, resuming at 0: %s", exc)
        seq += 1
        try:
            _SEQ_PATH.parent.mkdir(parents=True, exist_ok=True)
            _SEQ_PATH.write_text(json.dumps({"seq": seq}))
        except Exception as exc:
            logger.warning("signal_lifecycle: couldn't persist seq file: %s", exc)
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"SIG-{date_part}-{pair.replace('_', '')}-{timeframe}-{seq:06d}"


def format_diagnostics(strategy: str, diag: dict | None) -> str:
    """Pipe-joined key=value string using THIS strategy's own field
    meanings — never the generic c1..c7 names a different strategy's
    get_last_diag() happens to share. Unknown strategy or empty diag
    returns "" rather than guessing."""
    if not diag:
        return ""
    labels = _DIAG_LABELS.get(strategy)
    if not labels:
        return ""
    parts = []
    for raw_key, label in labels:
        if raw_key in diag:
            v = diag[raw_key]
            # Strategy diag dicts mix plain Python bool (hardcoded literals
            # in early-return branches) with numpy's bool scalar (computed
            # comparisons like `price > ema`) in the SAME dict — confirmed
            # live: strategy_ema_cci_macd's c1 came back numpy.bool
            # (np.True_) while c2-c7 were plain Python bool, which
            # isinstance(v, bool) alone does NOT catch (numpy.bool is not a
            # bool subclass despite sharing the display name "bool" in
            # recent numpy). Matching on type name instead of isinstance
            # catches both Python's and numpy's naming across versions
            # ("bool" current, "bool_" older numpy) without importing
            # numpy here. Non-boolean diagnostic values (floats like
            # broken_level) are left as-is, not coerced to 0/1.
            if type(v).__name__ in ("bool", "bool_"):
                parts.append(f"{label}={1 if v else 0}")
            else:
                parts.append(f"{label}={v}")
    return "|".join(parts)


def log_event(
    *,
    signal_id: str,
    pair: str,
    timeframe: str,
    direction: str,
    strategy: str,
    signal_score,
    stage: str,
    signal_status: str = "",
    execution_status: str = "",
    trade_id: str = "",
    failure_reason: str = "",
    mode: str = "",
    diagnostics: str = "",
) -> None:
    """Append one lifecycle event row. Non-blocking — errors are logged,
    never raised, matching engine/signal_audit.py's log_signal() so a
    logging failure here can never interrupt real signal processing."""
    if stage not in _STAGES:
        logger.warning("signal_lifecycle.log_event: unknown stage %r — logging anyway", stage)
    try:
        _ensure_file()
        row = {
            "event_time":        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "signal_id":         signal_id,
            "pair":              pair,
            "timeframe":         timeframe,
            "direction":         direction,
            "strategy":          strategy,
            "signal_score":      signal_score if signal_score is not None else "",
            "stage":             stage,
            "signal_status":     signal_status,
            "execution_status":  execution_status,
            "trade_id":          trade_id or "",
            "failure_reason":    failure_reason,
            "mode":              mode,
            "diagnostics":       diagnostics,
        }
        with _lock:
            with open(_LOG_PATH, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=_FIELDNAMES).writerow(row)
    except Exception as exc:
        logger.warning("signal_lifecycle.log_event failed: %s", exc)


def terminal_states(path: Path | str | None = None) -> dict:
    """Return {signal_id: last_row_dict} — every signal's documented
    terminal state, per the module's whole reason for existing. A signal
    whose last stage is SIGNAL_CREATED with signal_status in (WATCHING,
    SCANNING) or GATE_BLOCKED/EXECUTED/EXEC_FAILED is correctly terminal.
    A signal whose last stage is TRIGGERED, GATE_APPROVED, or
    OPEN_TRADE_CALLED and nothing followed is exactly the "stuck mid-
    pipeline" anomaly Phase 2A couldn't explain — now directly queryable
    instead of reconstructed after the fact."""
    p = Path(path) if path else _LOG_PATH
    if not p.exists():
        return {}
    last = {}
    with open(p, newline="") as f:
        for row in csv.DictReader(f):
            last[row["signal_id"]] = row
    return last
