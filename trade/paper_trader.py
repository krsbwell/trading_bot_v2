"""
Paper trading engine — simulates broker execution with no real orders.

Fill rules (conservative / worst-case):
  - Market entry: fills at the signal candle's close price
  - Limit entry:  fills when candle low (long) or high (short) touches the limit price
  - SL:     checked against candle LOW  (long) / HIGH  (short) — checked FIRST
  - TP1/2/3 checked against candle HIGH (long) / LOW   (short)

Partial close sequence (of ORIGINAL size):
  TP1 → close 40%, move SL to breakeven
  TP2 → close 35%
  TP3 → close remaining 25%

Persistence:
  State (balance, open/closed trades, pending orders) is written to disk after
  every mutation so it survives server restarts and browser disconnects.
  Default path: data/paper_state.json
"""
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_TP1_PCT = 0.40
_TP2_PCT = 0.35
_TP3_PCT = 0.25  # must equal 1 - _TP1_PCT - _TP2_PCT

# Absolute path derived from this file's location — immune to working-directory changes
_DEFAULT_SAVE_PATH = Path(__file__).parent.parent / "data" / "paper_state.json"


# ── JSON helpers for datetime serialisation ────────────────────────────────────

def _dt_to_str(dt) -> str | None:
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def _str_to_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _ser(trade: dict) -> dict:
    """Serialise a trade dict for JSON (convert datetimes to strings)."""
    d = dict(trade)
    for key in ("open_time", "close_time", "created_time"):
        if key in d:
            d[key] = _dt_to_str(d[key])
    return d


def _deser(trade: dict) -> dict:
    """Deserialise a trade dict from JSON (convert strings back to datetimes)."""
    d = dict(trade)
    for key in ("open_time", "close_time", "created_time"):
        if key in d and isinstance(d[key], str):
            d[key] = _str_to_dt(d[key])
    return d


class _InstrumentedLock:
    """
    threading.Lock wrapper — same context-manager interface, so every existing
    `with self._lock:` call site is unaffected. Logs a WARNING if acquiring
    takes >1s or if held for >1s. Added 2026-07-06 to catch the mechanism
    behind recurring on_candle_close scheduler misfires (see
    bugs_scheduler_reliability memory) — if the price-stream thread's tick
    callback holds this lock for a long time, the scheduler thread would
    block waiting for it on the next scan cycle. Purely diagnostic: no
    behavior change on the fast path.
    """
    _SLOW_S = 1.0

    def __init__(self):
        self._lock = threading.Lock()

    def __enter__(self):
        start = time.monotonic()
        self._lock.acquire()
        waited = time.monotonic() - start
        if waited > self._SLOW_S:
            logger.warning(
                "PaperTrader lock acquisition took %.1fs (thread=%s)",
                waited, threading.current_thread().name,
            )
        self._acquired_at = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        held = time.monotonic() - self._acquired_at
        if held > self._SLOW_S:
            logger.warning(
                "PaperTrader lock held for %.1fs (thread=%s)",
                held, threading.current_thread().name,
            )
        self._lock.release()
        return False


class PaperTrader:

    def __init__(self, starting_balance: float = 500.0,
                 save_path: str | Path | bool = None):
        self._lock = _InstrumentedLock()   # protects open_trades / closed_trades mutations
        # save_path=False disables all disk I/O (used by backtest/WFO to avoid file contention)
        if save_path is False:
            self._save_path = None
        else:
            self._save_path = Path(save_path) if save_path else _DEFAULT_SAVE_PATH

        loaded = self._load_state()
        if loaded:
            self.balance        = loaded["balance"]
            self._start_balance = loaded.get("_start_balance", starting_balance)
            self.open_trades    = loaded["open_trades"]
            self.closed_trades  = loaded["closed_trades"]
            self.pending_orders = loaded.get("pending_orders", [])
            logger.info(
                "PaperTrader restored — balance=$%.2f  open=%d  closed=%d",
                self.balance, len(self.open_trades), len(self.closed_trades),
            )
        else:
            self.balance         = starting_balance
            self._start_balance  = starting_balance
            self.open_trades:    list[dict] = []
            self.closed_trades:  list[dict] = []
            self.pending_orders: list[dict] = []

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_state(self) -> None:
        """Write full state to disk. Called after every mutation."""
        if self._save_path is None:
            return   # disk I/O disabled (backtest / WFO mode)
        try:
            self._save_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "balance":        self.balance,
                "_start_balance": self._start_balance,
                "open_trades":    [_ser(t) for t in self.open_trades],
                # keep last 500 closed trades to avoid unbounded growth
                "closed_trades":  [_ser(t) for t in self.closed_trades[-500:]],
                "pending_orders": [_ser(o) for o in self.pending_orders],
                "saved_at":       datetime.now(timezone.utc).isoformat(),
            }
            json_str = json.dumps(payload, indent=2)
            tmp = Path(str(self._save_path) + ".tmp")
            tmp.write_text(json_str, encoding="utf-8")

            # Tier 1: atomic rename (preferred — safest on same-volume writes)
            for _attempt in range(3):
                try:
                    os.replace(tmp, self._save_path)
                    return
                except OSError:
                    if _attempt < 2:
                        time.sleep(0.05)

            # Tier 2: Windows file-lock workaround — delete destination then rename.
            # os.replace() can fail on Windows when an AV scanner or VS Code briefly
            # holds a read handle on the destination (WinError 32/5).
            try:
                self._save_path.unlink(missing_ok=True)
                tmp.rename(self._save_path)
                return
            except OSError:
                pass

            # Tier 3: direct overwrite — non-atomic but guarantees data is written.
            # tmp already has the correct content; copy it directly.
            self._save_path.write_text(json_str, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            logger.debug("PaperTrader._save_state: used direct-write fallback")

        except Exception as exc:
            logger.warning("PaperTrader._save_state failed: %s", exc)

    def _load_state(self) -> dict | None:
        """Read state from disk. Returns None if file missing or corrupt."""
        if self._save_path is None:
            return None   # disk I/O disabled (backtest / WFO mode)
        try:
            logger.info("PaperTrader._load_state: reading %s", self._save_path)
            if not self._save_path.exists():
                logger.debug("PaperTrader._load_state: file not found at %s", self._save_path)
                return None
            with open(self._save_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            logger.info("PaperTrader._load_state: parsed OK — balance=%.2f  closed=%d",
                        raw.get("balance", 0), len(raw.get("closed_trades", [])))
            raw["open_trades"]    = [_deser(t) for t in raw.get("open_trades",    [])]
            raw["closed_trades"]  = [_deser(t) for t in raw.get("closed_trades",  [])]
            raw["pending_orders"] = [_deser(o) for o in raw.get("pending_orders", [])]
            return raw
        except Exception as exc:
            logger.warning("PaperTrader._load_state FAILED: %s", exc, exc_info=True)
            return None

    # ── Open (Market) ─────────────────────────────────────────────────────────

    def open_trade(
        self,
        pair: str,
        direction: str,
        entry_price: float,
        sl: float,
        tp_levels: dict,
        size: float,
        extra: dict | None = None,
        trade_id: str | None = None,
    ) -> str | None:
        """
        Fill immediately at entry_price (market order).
        size: units — used as a raw multiplier for P&L.
        extra: optional dict of additional fields to store on the trade (e.g. latency_ms).
        trade_id: caller-supplied id (e.g. to match learning.data_collector's
            record_signal() id so record_close() can find the cached signal
            when this trade closes) — auto-generated if not given.
        Returns trade_id string, or None if rejected (duplicate pair).
        """
        with self._lock:
            # ── Last-resort duplicate guard (primary check is in main._process_pair) ──
            existing = self.get_open_trade(pair)
            if existing and not getattr(__import__("config"), "ALLOW_MULTIPLE_PER_PAIR", False):
                logger.warning(
                    "PAPER TRADER: Duplicate trade rejected for %s "
                    "(existing id=%s dir=%s) — ALLOW_MULTIPLE_PER_PAIR=False",
                    pair, existing["id"], existing["direction"],
                )
                return None

            trade_id = trade_id or str(uuid.uuid4())[:8].upper()
            trade = {
                "id":            trade_id,
                "pair":          pair,
                "direction":     direction,
                "entry":         entry_price,
                "sl":            sl,
                "tp1":           tp_levels["tp1"],
                "tp2":           tp_levels["tp2"],
                "tp3":           tp_levels["tp3"],
                "size":          float(size),
                "remaining":     1.0,
                "tp1_hit":       False,
                "tp2_hit":       False,
                "tp3_hit":       False,
                "breakeven_set": False,
                "realised_pnl":  0.0,
                "open_time":     datetime.now(timezone.utc),
                "last_price":    entry_price,
                "order_type":    "market",
            }
            if extra:
                trade.update(extra)
            self.open_trades.append(trade)
            self._save_state()
            logger.info(
                "PAPER OPEN  %s %s  entry=%.5f  sl=%.5f  tp1=%.5f  size=%s  id=%s",
                direction.upper(), pair, entry_price, sl, tp_levels["tp1"], size, trade_id,
            )
            return trade_id

    # ── Open (Limit) ──────────────────────────────────────────────────────────

    def open_limit_order(
        self,
        pair: str,
        direction: str,
        limit_price: float,
        sl: float,
        tp_levels: dict,
        size: float,
    ) -> str:
        """Queue a limit order. Fills on next candle."""
        order_id = str(uuid.uuid4())[:8].upper()
        order = {
            "id":           order_id,
            "pair":         pair,
            "direction":    direction,
            "limit_price":  limit_price,
            "sl":           sl,
            "tp1":          tp_levels["tp1"],
            "tp2":          tp_levels["tp2"],
            "tp3":          tp_levels["tp3"],
            "size":         float(size),
            "order_type":   "limit",
            "status":       "pending",
            "created_time": datetime.now(timezone.utc),
        }
        self.pending_orders.append(order)
        self._save_state()
        logger.info(
            "LIMIT ORDER QUEUED  %s %s  limit=%.5f  sl=%.5f  id=%s",
            direction.upper(), pair, limit_price, sl, order_id,
        )
        return order_id

    def cancel_limit_order(self, order_id: str) -> bool:
        for i, o in enumerate(self.pending_orders):
            if o["id"] == order_id:
                self.pending_orders.pop(i)
                self._save_state()
                logger.info("LIMIT ORDER CANCELLED  id=%s", order_id)
                return True
        return False

    def modify_pending_order(self, order_id: str, limit_price: float = None,
                             sl: float = None, tp1: float = None,
                             tp2: float = None, tp3: float = None) -> bool:
        """Update limit price / SL / TP levels on a pending order. Pass None to leave unchanged."""
        for o in self.pending_orders:
            if o["id"] == order_id:
                if limit_price is not None: o["limit_price"] = limit_price
                if sl          is not None: o["sl"]          = sl
                if tp1         is not None: o["tp1"]          = tp1
                if tp2         is not None: o["tp2"]          = tp2
                if tp3         is not None: o["tp3"]          = tp3
                self._save_state()
                logger.info(
                    "LIMIT ORDER MODIFIED  id=%s  limit=%s  SL=%s  TP1=%s  TP2=%s  TP3=%s",
                    order_id,
                    f"{limit_price:.5f}" if limit_price else "—",
                    f"{sl:.5f}"  if sl  else "—",
                    f"{tp1:.5f}" if tp1 else "—",
                    f"{tp2:.5f}" if tp2 else "—",
                    f"{tp3:.5f}" if tp3 else "—",
                )
                return True
        return False

    # ── Update (called on every candle close) ─────────────────────────────────

    def update(self, pair: str, candle_high: float, candle_low: float, candle_close: float,
               atr_value: float = None) -> None:
        """
        Process one completed candle for all open trades and pending limit
        orders on `pair`. `atr_value` (current ATR in price units, not pips)
        is only used when config.ATR_TRAILING_ENABLED — pass None otherwise.
        """
        with self._lock:
            self._check_limit_fills(pair, candle_high, candle_low)

            dirty = False
            still_open = []
            for trade in self.open_trades:
                if trade["pair"] != pair:
                    still_open.append(trade)
                    continue
                trade["last_price"] = candle_close
                fully_closed = self._eval_candle(trade, candle_high, candle_low, atr_value)
                if not fully_closed:
                    still_open.append(trade)
                else:
                    dirty = True
            self.open_trades = still_open
            if dirty:
                self._save_state()

    # Default: cancel unfilled limit orders after this many candles (configurable in config)
    _LIMIT_ORDER_EXPIRY_CANDLES = 4   # expire after 4 candle closes (≈ 4 hours on H1)

    def _check_limit_fills(self, pair: str, high: float, low: float) -> None:
        remaining = []
        dirty = False
        expiry_candles = getattr(
            __import__("config"), "LIMIT_ORDER_EXPIRY_CANDLES",
            self._LIMIT_ORDER_EXPIRY_CANDLES,
        )
        for order in self.pending_orders:
            if order["pair"] != pair:
                remaining.append(order)
                continue
            limit  = order["limit_price"]
            d      = order["direction"]
            filled = (d == "long" and low <= limit) or (d == "short" and high >= limit)
            if filled:
                self.open_trade(
                    pair=pair, direction=d, entry_price=limit,
                    sl=order["sl"],
                    tp_levels={"tp1": order["tp1"], "tp2": order["tp2"], "tp3": order["tp3"]},
                    size=order["size"],
                )
                dirty = True
                logger.info("LIMIT ORDER FILLED  %s %s  at=%.5f  id=%s",
                            d.upper(), pair, limit, order["id"])
            else:
                # ── Candle-based expiry: increment miss counter and cancel if exceeded ──
                order["candles_missed"] = order.get("candles_missed", 0) + 1
                if order["candles_missed"] >= expiry_candles:
                    logger.info(
                        "LIMIT ORDER EXPIRED (missed %d candles)  %s %s  limit=%.5f  id=%s",
                        order["candles_missed"], d.upper(), pair, limit, order["id"],
                    )
                    dirty = True   # state changed — needs save
                else:
                    remaining.append(order)
        self.pending_orders = remaining
        if dirty:
            self._save_state()

    def _eval_candle(self, t: dict, high: float, low: float, atr_value: float = None) -> bool:
        d = t["direction"]

        # ── Trailing stop: advance SL after TP2 hit (final 25% leg only) ────────
        # Only activates after TP2 is captured so the first 75% plays out cleanly.
        # ATR-scaled trail takes priority when enabled (adapts to volatility instead
        # of one static pip distance for every market condition); falls back to the
        # fixed-pip trail otherwise. Off by default (ATR_TRAILING_ENABLED=False) —
        # same convention as BREAKEVEN_ENABLED, so this doesn't change live behavior
        # until deliberately tested/enabled. See bugs_shadow_outcome_duplication-style
        # caution: don't let a new mechanism silently alter what's already validated.
        if t.get("tp2_hit"):
            ref_price = t.get("last_price") or (high if d == "long" else low)   # shared by both trail modes
            if config.ATR_TRAILING_ENABLED and atr_value:
                trail_gap = atr_value * config.ATR_TRAILING_MULTIPLIER
                if d == "long":
                    new_sl = ref_price - trail_gap
                    if new_sl > t["sl"]:       # only advance — never retreat
                        t["sl"] = round(new_sl, 3 if "JPY" in t.get("pair", "") else 5)
                else:
                    new_sl = ref_price + trail_gap
                    if new_sl < t["sl"]:       # only advance — never retreat
                        t["sl"] = round(new_sl, 3 if "JPY" in t.get("pair", "") else 5)
            else:
                trail_pips = getattr(__import__("config"), "TRAILING_STOP_PIPS", 0)
                if trail_pips > 0:
                    pip       = 0.01 if "JPY" in t.get("pair", "") else 0.0001
                    trail_gap = pip * trail_pips
                    if d == "long":
                        new_sl = ref_price - trail_gap
                        if new_sl > t["sl"]:       # only advance — never retreat
                            t["sl"] = round(new_sl, 3 if "JPY" in t.get("pair","") else 5)
                    else:
                        new_sl = ref_price + trail_gap
                        if new_sl < t["sl"]:       # only advance — never retreat
                            t["sl"] = round(new_sl, 3 if "JPY" in t.get("pair","") else 5)

        # ── SL check ───────────────────────────────────────────────────────────
        sl_hit = (d == "long" and low <= t["sl"]) or \
                 (d == "short" and high >= t["sl"])
        if sl_hit:
            return self._close_remaining(t, t["sl"], "sl")

        # ── TP checks ──────────────────────────────────────────────────────────
        if not t["tp1_hit"]:
            tp1_hit = (d == "long" and high >= t["tp1"]) or \
                      (d == "short" and low  <= t["tp1"])
            if tp1_hit:
                self._partial_close(t, t["tp1"], _TP1_PCT, "tp1")
                t["tp1_hit"] = True
                _pair = t.get("pair", "")
                _be_on = config.BREAKEVEN_PER_PAIR.get(_pair, config.BREAKEVEN_ENABLED)
                if _be_on:
                    _pip  = 0.01 if "JPY" in _pair else 0.0001
                    _buf  = config.BREAKEVEN_BUFFER_PIPS * _pip
                    _mult = 1 if d == "long" else -1
                    t["sl"]            = round(t["entry"] + _mult * _buf, 5)
                    t["breakeven_set"] = True

        if t["tp1_hit"] and not t["tp2_hit"]:
            tp2_hit = (d == "long" and high >= t["tp2"]) or \
                      (d == "short" and low  <= t["tp2"])
            if tp2_hit:
                self._partial_close(t, t["tp2"], _TP2_PCT, "tp2")
                t["tp2_hit"] = True

        if t["tp2_hit"] and not t["tp3_hit"]:
            tp3_hit = (d == "long" and high >= t["tp3"]) or \
                      (d == "short" and low  <= t["tp3"])
            if tp3_hit:
                return self._close_remaining(t, t["tp3"], "tp3")

        return False

    # ── P&L helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _pnl(entry: float, exit_price: float, direction: str, size: float,
              pair: str = "") -> float:
        """
        diff is in `pair`'s quote currency, not necessarily USD (e.g. JPY
        for EUR_JPY) — needs the same conversion calculate_position_size()
        applies, or a correctly-sized JPY/CAD/AUD position (thousands of
        units, post 2026-08-04 sizing fix) produces P&L wrong by the same
        ~30-150x factor. No live connector here (paper/backtest), so this
        always uses the approximate rate table.
        """
        from risk.risk_manager import get_quote_to_usd_rate
        diff = (exit_price - entry) if direction == "long" else (entry - exit_price)
        q2u  = get_quote_to_usd_rate(pair) if pair else 1.0
        return round(diff * size * q2u, 4)

    def _partial_close(self, t: dict, price: float, original_pct: float, label: str) -> None:
        units = t["size"] * original_pct
        pnl   = self._pnl(t["entry"], price, t["direction"], units, t.get("pair", ""))
        t["realised_pnl"] += pnl
        t["remaining"]    -= original_pct
        self.balance      += pnl
        logger.info(
            "PAPER %-4s  %s %s  price=%.5f  units=%.4f  pnl=%+.2f  balance=%.2f",
            label.upper(), t["direction"].upper(), t["pair"],
            price, units, pnl, self.balance,
        )
        self._save_state()

    def _close_remaining(self, t: dict, price: float, reason: str) -> bool:
        if t["remaining"] > 0:
            self._partial_close(t, price, t["remaining"], reason)
        t["remaining"]    = 0.0
        t["close_time"]   = datetime.now(timezone.utc)
        t["exit_price"]   = price
        t["close_reason"] = reason
        self.closed_trades.append(dict(t))
        self._save_state()
        logger.info(
            "PAPER CLOSED  %s %s  reason=%s  exit=%.5f  total_pnl=%+.2f",
            t["direction"].upper(), t["pair"], reason, price, t["realised_pnl"],
        )
        self._log_outcome(t)
        return True

    def _log_outcome(self, t: dict) -> None:
        """
        Feed the closed trade to learning.data_collector.record_close() so it
        merges with the cached signal features (from record_signal() at open
        time) into signal_log.csv for ML training. Best-effort — a logging
        failure must never break real trade closing.
        """
        try:
            from learning.data_collector import record_close
            pair = t.get("pair", "")
            pip  = 0.01 if "JPY" in pair else 0.0001
            entry, exit_price = t.get("entry", 0), t.get("exit_price", 0)
            diff = (exit_price - entry) if t.get("direction") == "long" else (entry - exit_price)
            pnl_pips   = diff / pip
            pnl_dollar = t.get("realised_pnl", 0.0)
            open_time, close_time = t.get("open_time"), t.get("close_time")
            hold_hours = (
                (close_time - open_time).total_seconds() / 3600
                if open_time and close_time else 0.0
            )
            record_close(
                trade_id     = t.get("id", ""),
                outcome      = "win" if pnl_dollar > 0 else "loss",
                pnl_pips     = pnl_pips,
                pnl_dollar   = pnl_dollar,
                hold_hours   = hold_hours,
                tp_level_hit = t.get("close_reason", ""),
            )
        except Exception as exc:
            logger.debug("record_close failed for trade %s: %s", t.get("id"), exc)

    # ── Real-time tick check (called every ~5 s from dashboard interval) ─────────

    def tick_check(self, pair: str, bid: float, ask: float) -> list[str]:
        """
        Check SL/TP against the current live bid/ask and close any trades that are hit.
        Returns a list of closed trade IDs so callers can sync state.

        Uses ask for short SL (worst-case fill) and bid for long SL,
        mirroring real broker fill logic for paper simulation.
        Thread-safe — called from both the price-stream daemon and the 5-second Dash interval.
        """
        with self._lock:
            closed_ids: list[str] = []
            still_open = []
            dirty = False

            for t in self.open_trades:
                if t["pair"] != pair:
                    still_open.append(t)
                    continue

                d = t["direction"]
                check_price = bid if d == "long" else ask

                sl_hit = (d == "long"  and bid <= t["sl"]) or \
                         (d == "short" and ask >= t["sl"])

                if sl_hit:
                    fill = t["sl"]
                    logger.info(
                        "TICK SL HIT  %s %s  price=%.5f  sl=%.5f",
                        d.upper(), pair, check_price, t["sl"],
                    )
                    self._close_remaining(t, fill, "sl")
                    closed_ids.append(t["id"])
                    dirty = True
                    continue

                # TP checks (favourable side: long=ask, short=bid)
                tp_price = ask if d == "long" else bid

                if not t.get("tp1_hit") and t.get("tp1"):
                    tp1_hit = (d == "long"  and tp_price >= t["tp1"]) or \
                              (d == "short" and tp_price <= t["tp1"])
                    if tp1_hit:
                        self._partial_close(t, t["tp1"], _TP1_PCT, "tp1")
                        t["tp1_hit"] = True
                        _pair = t.get("pair", "")
                        _be_on = config.BREAKEVEN_PER_PAIR.get(_pair, config.BREAKEVEN_ENABLED)
                        if _be_on:
                            _pip  = 0.01 if "JPY" in _pair else 0.0001
                            _buf  = config.BREAKEVEN_BUFFER_PIPS * _pip
                            _mult = 1 if d == "long" else -1
                            t["sl"]            = round(t["entry"] + _mult * _buf, 5)
                            t["breakeven_set"] = True
                        dirty = True

                if t.get("tp1_hit") and not t.get("tp2_hit") and t.get("tp2"):
                    tp2_hit = (d == "long"  and tp_price >= t["tp2"]) or \
                              (d == "short" and tp_price <= t["tp2"])
                    if tp2_hit:
                        self._partial_close(t, t["tp2"], _TP2_PCT, "tp2")
                        t["tp2_hit"] = True
                        dirty = True

                if t.get("tp2_hit") and not t.get("tp3_hit") and t.get("tp3"):
                    tp3_hit = (d == "long"  and tp_price >= t["tp3"]) or \
                              (d == "short" and tp_price <= t["tp3"])
                    if tp3_hit:
                        self._close_remaining(t, t["tp3"], "tp3")
                        closed_ids.append(t["id"])
                        dirty = True
                        continue

                still_open.append(t)

            self.open_trades = still_open
            if dirty:
                self._save_state()
            return closed_ids

    # ── Trade modification (dashboard edit form) ──────────────────────────────

    def modify_trade(self, trade_id: str, sl: float = None,
                     tp1: float = None, tp2: float = None,
                     tp3: float = None) -> bool:
        """Update SL and/or TP levels on an open trade. Pass None to leave unchanged."""
        for t in self.open_trades:
            if t["id"] == trade_id:
                if sl  is not None: t["sl"]  = sl
                if tp1 is not None: t["tp1"] = tp1
                if tp2 is not None: t["tp2"] = tp2
                if tp3 is not None: t["tp3"] = tp3
                self._save_state()
                logger.info(
                    "TRADE MODIFIED  id=%s  SL=%s  TP1=%s  TP2=%s  TP3=%s",
                    trade_id,
                    f"{sl:.5f}" if sl  else "—",
                    f"{tp1:.5f}" if tp1 else "—",
                    f"{tp2:.5f}" if tp2 else "—",
                    f"{tp3:.5f}" if tp3 else "—",
                )
                return True
        return False

    def move_to_breakeven(self, trade_id: str) -> bool:
        """Move SL to the trade's entry price (break-even)."""
        for t in self.open_trades:
            if t["id"] == trade_id:
                t["sl"]            = t["entry"]
                t["breakeven_set"] = True
                self._save_state()
                logger.info("BREAKEVEN SET  id=%s  entry=%.5f", trade_id, t["entry"])
                return True
        return False

    # ── Manual close (dashboard CLOSE button, weekend auto-close) ─────────────

    def manual_close(self, trade_id: str, exit_price: float, reason: str = "manual") -> bool:
        with self._lock:
            for i, t in enumerate(self.open_trades):
                if t["id"] == trade_id:
                    self._close_remaining(t, exit_price, reason)
                    self.open_trades.pop(i)
                    self._save_state()
                    return True
        return False

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        unrealised = self._calc_unrealized()
        return {
            "balance":          round(self.balance, 2),
            "unrealized_pnl":   unrealised,
            "nav":              round(self.balance + unrealised, 2),
            "open_trade_count": len(self.open_trades),
            "pending_count":    len(self.pending_orders),
        }

    def _calc_unrealized(self) -> float:
        total = 0.0
        for t in self.open_trades:
            units = t["size"] * t["remaining"]
            total += self._pnl(t["entry"], t["last_price"], t["direction"], units, t.get("pair", ""))
        return round(total, 4)

    def get_open_trade(self, pair: str) -> dict | None:
        for t in self.open_trades:
            if t["pair"] == pair:
                return t
        return None

    # ── Stats helpers ─────────────────────────────────────────────────────────

    def daily_pnl(self) -> float:
        return round(self.balance - self._start_balance, 2)

    def win_rate(self, last_n: int = 0) -> float:
        trades = self.closed_trades[-last_n:] if last_n else self.closed_trades
        if not trades:
            return 0.0
        wins = sum(1 for t in trades if t.get("realised_pnl", 0) > 0)
        return round(wins / len(trades), 4)

    def profit_factor(self, last_n: int = 0) -> float:
        """Gross profit / gross loss. > 1.0 = profitable. Returns 0.0 if no losses."""
        trades = self.closed_trades[-last_n:] if last_n else self.closed_trades
        gross_profit = sum(t.get("realised_pnl", 0) for t in trades if t.get("realised_pnl", 0) > 0)
        gross_loss   = abs(sum(t.get("realised_pnl", 0) for t in trades if t.get("realised_pnl", 0) < 0))
        if gross_loss == 0:
            return round(gross_profit, 2) if gross_profit else 0.0
        return round(gross_profit / gross_loss, 2)

    def expectancy(self, last_n: int = 0) -> float:
        """Average P&L per trade = (Win% × Avg win) − (Loss% × Avg loss)."""
        trades = self.closed_trades[-last_n:] if last_n else self.closed_trades
        if not trades:
            return 0.0
        pnls = [t.get("realised_pnl", 0) for t in trades]
        return round(sum(pnls) / len(pnls), 2)

    def avg_latency_ms(self) -> float | None:
        """Mean signal-to-fill latency in milliseconds from closed trades."""
        lats = [t.get("latency_ms") for t in self.closed_trades if t.get("latency_ms") is not None]
        if not lats:
            return None
        return round(sum(lats) / len(lats), 1)
