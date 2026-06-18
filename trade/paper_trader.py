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
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_TP1_PCT = 0.40
_TP2_PCT = 0.35
_TP3_PCT = 0.25  # must equal 1 - _TP1_PCT - _TP2_PCT

_DEFAULT_SAVE_PATH = Path("data/paper_state.json")


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


class PaperTrader:

    def __init__(self, starting_balance: float = 500.0,
                 save_path: str | Path = None):
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
        try:
            self._save_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "balance":        self.balance,
                "_start_balance": self._start_balance,
                "open_trades":    [_ser(t) for t in self.open_trades],
                # keep last 500 closed trades to avoid unbounded growth
                "closed_trades":  [_ser(t) for t in self.closed_trades[-500:]],
                "pending_orders": [_ser(o) for o in self.pending_orders],
                "saved_at":       datetime.now(timezone.utc).isoformat(),
            }
            tmp = str(self._save_path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self._save_path)   # atomic rename
        except Exception as exc:
            logger.warning("PaperTrader._save_state failed: %s", exc)

    def _load_state(self) -> dict | None:
        """Read state from disk. Returns None if file missing or corrupt."""
        try:
            if not self._save_path.exists():
                return None
            with open(self._save_path, "r") as f:
                raw = json.load(f)
            raw["open_trades"]    = [_deser(t) for t in raw.get("open_trades",    [])]
            raw["closed_trades"]  = [_deser(t) for t in raw.get("closed_trades",  [])]
            raw["pending_orders"] = [_deser(o) for o in raw.get("pending_orders", [])]
            return raw
        except Exception as exc:
            logger.warning("PaperTrader._load_state failed: %s", exc)
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
    ) -> str:
        """
        Fill immediately at entry_price (market order).
        size: units (forex) or qty (crypto) — used as a raw multiplier for P&L.
        Returns trade_id string.
        """
        trade_id = str(uuid.uuid4())[:8].upper()
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

    # ── Update (called on every candle close) ─────────────────────────────────

    def update(self, pair: str, candle_high: float, candle_low: float, candle_close: float) -> None:
        """Process one completed candle for all open trades and pending limit orders on `pair`."""
        self._check_limit_fills(pair, candle_high, candle_low)

        dirty = False
        still_open = []
        for trade in self.open_trades:
            if trade["pair"] != pair:
                still_open.append(trade)
                continue
            trade["last_price"] = candle_close
            fully_closed = self._eval_candle(trade, candle_high, candle_low)
            if not fully_closed:
                still_open.append(trade)
            else:
                dirty = True
        self.open_trades = still_open
        if dirty:
            self._save_state()

    def _check_limit_fills(self, pair: str, high: float, low: float) -> None:
        remaining = []
        dirty = False
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
                remaining.append(order)
        self.pending_orders = remaining
        if dirty:
            self._save_state()

    def _eval_candle(self, t: dict, high: float, low: float) -> bool:
        d = t["direction"]

        # ── Trailing stop: advance SL after TP2 hit (final 25% leg only) ────────
        # Only activates after TP2 is captured so the first 75% plays out cleanly.
        # A 50-pip+ gap respects typical H1 candle ranges and avoids noise exits.
        trail_pips = getattr(__import__("config"), "TRAILING_STOP_PIPS", 0)
        if trail_pips > 0 and t.get("tp2_hit"):
            pip       = 0.01 if "JPY" in t.get("pair", "") else 0.0001
            trail_gap = pip * trail_pips
            # Use the candle close (last_price) as the reference; fall back to high/low.
            ref_price = t.get("last_price") or (high if d == "long" else low)
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
                t["tp1_hit"]     = True
                t["sl"]          = t["entry"]   # move SL to breakeven
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
    def _pnl(entry: float, exit_price: float, direction: str, size: float) -> float:
        diff = (exit_price - entry) if direction == "long" else (entry - exit_price)
        return round(diff * size, 4)

    def _partial_close(self, t: dict, price: float, original_pct: float, label: str) -> None:
        units = t["size"] * original_pct
        pnl   = self._pnl(t["entry"], price, t["direction"], units)
        t["realised_pnl"] += pnl
        t["remaining"]    -= original_pct
        self.balance      += pnl
        logger.info(
            "PAPER %-4s  %s %s  price=%.5f  units=%.4f  pnl=%+.2f  balance=%.2f",
            label.upper(), t["direction"].upper(), t["pair"],
            price, units, pnl, self.balance,
        )

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
        return True

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

    # ── Manual close (dashboard CLOSE button) ─────────────────────────────────

    def manual_close(self, trade_id: str, exit_price: float) -> bool:
        for i, t in enumerate(self.open_trades):
            if t["id"] == trade_id:
                self._close_remaining(t, exit_price, "manual")
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
            total += self._pnl(t["entry"], t["last_price"], t["direction"], units)
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
