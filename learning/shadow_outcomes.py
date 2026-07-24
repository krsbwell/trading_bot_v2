"""
Shadow-outcome resolver.

For every 'skipped' row in signal_log.csv — a signal the bot scored but
never traded (below threshold, watching, or gate-blocked by ML/structure/
session/news/duplicate/cooldown — see main.py's record_skip() call sites) —
walks forward through the candles that actually followed to determine what
WOULD have happened, so the ML can learn from near-misses and rejected
setups, not just trades the bot actually took.

Deliberately simplified vs PaperTrader's real fill simulation: checks only
TP1 vs SL (first touch wins), not the full TP1/TP2/TP3 partial-close/
trailing-stop sequence — this answers "would this setup have worked at
all", not "what would the exact P&L have been". SL is treated as the
winner if both SL and TP1 are touched within the same candle (matches
PaperTrader._eval_candle's own SL-first convention, applied here because
OHLC data alone can't tell us the true intra-candle order).

Outcome values written back into the CSV's `outcome` column:
    would_win   — TP1 touched before SL
    would_lose  — SL touched before (or same candle as) TP1
    expired     — neither touched within MAX_LOOKFORWARD_CANDLES, or the
                  row was missing entry/SL/TP and can never be resolved
    (left as 'skipped' if not enough candles have elapsed yet — retried on
    the next run)
"""
import logging

import pandas as pd

from .data_collector import SIGNAL_LOG

logger = logging.getLogger(__name__)

MAX_LOOKFORWARD_CANDLES = 96    # ~48h of M30 candles — give up past this
CANDLE_FETCH_COUNT      = 500   # ~10.4 days of M30 — how far back get_candles() can reach


def resolve_pending(connector, log_path: str = SIGNAL_LOG) -> int:
    """
    Walk every 'skipped' row and try to resolve it against real subsequent
    candles. Rewrites the CSV in place (only rows that actually resolved
    this run are changed). Returns the number of rows resolved.
    """
    try:
        df = pd.read_csv(log_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return 0

    pending = df.index[df["outcome"] == "skipped"]
    if len(pending) == 0:
        return 0

    resolved = 0
    _candle_cache: dict[str, pd.DataFrame] = {}

    for idx in pending:
        try:
            outcome, pnl_pips = _resolve_one(df.loc[idx], connector, _candle_cache)
        except Exception as exc:
            logger.debug("shadow_outcomes: failed to resolve row %s: %s", idx, exc)
            continue
        if outcome is None:
            continue   # not enough time/candles elapsed yet — retry on a future run
        df.loc[idx, "outcome"] = outcome
        if pnl_pips is not None:
            df.loc[idx, "pnl_pips"] = pnl_pips
        resolved += 1

    if resolved:
        df.to_csv(log_path, index=False)
        logger.info("shadow_outcomes: resolved %d pending signal(s)", resolved)
    return resolved


def _resolve_one(row: pd.Series, connector, candle_cache: dict) -> tuple[str | None, float | None]:
    """
    Returns (outcome, pnl_pips). pnl_pips is the distance from entry to
    whichever level actually got hit (TP1 for would_win, SL for
    would_lose) — the same "first touch" simplification as the outcome
    itself (see module docstring): a real magnitude, just not the exact
    P&L a live TP1/TP2/TP3 partial-close sequence would have produced.
    None for "expired"/unresolvable rows, since there's no level to measure.
    """
    pair      = row.get("pair", "")
    direction = row.get("direction", "")
    entry     = row.get("entry_price")
    sl        = row.get("stop_loss")
    tp1       = row.get("tp1")
    ts_raw    = row.get("timestamp")

    if not pair or direction not in ("long", "short") or pd.isna(entry) or \
       pd.isna(sl) or pd.isna(tp1) or not ts_raw:
        return "expired", None   # incomplete row — can never be resolved, stop retrying it

    signal_time = pd.to_datetime(ts_raw)
    if signal_time.tzinfo is not None:
        signal_time = signal_time.tz_convert("UTC").tz_localize(None)

    gran      = row.get("timeframe_primary") or "M30"
    cache_key = f"{pair}_{gran}"
    if cache_key not in candle_cache:
        candle_cache[cache_key] = connector.get_candles(pair, gran, CANDLE_FETCH_COUNT)
    candles = candle_cache[cache_key]
    if candles is None or candles.empty:
        return None, None

    after = candles[candles.index > signal_time]
    if after.empty:
        return None, None   # too recent — no candles closed after this signal yet

    pip = 0.01 if "JPY" in pair else 0.0001

    window = after.iloc[:MAX_LOOKFORWARD_CANDLES]
    for _, c in window.iterrows():
        high, low = c["high"], c["low"]
        if direction == "long":
            sl_hit = low  <= sl
            tp_hit = high >= tp1
        else:
            sl_hit = high >= sl
            tp_hit = low  <= tp1
        if sl_hit:              # SL-first convention — see module docstring
            diff = (sl - entry) if direction == "long" else (entry - sl)
            return "would_lose", round(diff / pip, 1)
        if tp_hit:
            diff = (tp1 - entry) if direction == "long" else (entry - tp1)
            return "would_win", round(diff / pip, 1)

    if len(window) >= MAX_LOOKFORWARD_CANDLES:
        return "expired", None
    return None, None   # still within the lookforward window — not resolvable yet
