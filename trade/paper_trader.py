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
"""
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_TP1_PCT = 0.40
_TP2_PCT = 0.35
_TP3_PCT = 0.25  # must equal 1 - _TP1_PCT - _TP2_PCT


class PaperTrader:

    def __init__(self, starting_balance: float = 500.0):
        self.balance         = starting_balance
        self._start_balance  = starting_balance
        self.open_trades:    list[dict] = []
        self.closed_trades:  list[dict] = []
        self.pending_orders: list[dict] = []   # limit orders waiting to fill

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
        """
        Queue a limit order.  Fills when the next candle's low (long) or high
        (short) touches limit_price.
        Returns order_id string.
        """
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
        logger.info(
            "LIMIT ORDER QUEUED  %s %s  limit=%.5f  sl=%.5f  tp1=%.5f  id=%s",
            direction.upper(), pair, limit_price, sl, tp_levels["tp1"], order_id,
        )
        return order_id

    def cancel_limit_order(self, order_id: str) -> bool:
        """Cancel a pending limit order before it fills."""
        for i, o in enumerate(self.pending_orders):
            if o["id"] == order_id:
                self.pending_orders.pop(i)
                logger.info("LIMIT ORDER CANCELLED  id=%s", order_id)
                return True
        return False

    # ── Update (called on every candle close) ─────────────────────────────────

    def update(self, pair: str, candle_high: float, candle_low: float, candle_close: float) -> None:
        """
        Process one completed candle for all open trades and pending limit orders
        on `pair`.  Call this from the scheduler after every candle close.
        """
        # Check pending limit orders first — they may open new trades
        self._check_limit_fills(pair, candle_high, candle_low)

        still_open = []
        for trade in self.open_trades:
            if trade["pair"] != pair:
                still_open.append(trade)
                continue
            trade["last_price"] = candle_close
            fully_closed = self._eval_candle(trade, candle_high, candle_low)
            if not fully_closed:
                still_open.append(trade)
        self.open_trades = still_open

    def _check_limit_fills(self, pair: str, high: float, low: float) -> None:
        """Fill any pending limit orders whose price has been reached."""
        remaining = []
        for order in self.pending_orders:
            if order["pair"] != pair:
                remaining.append(order)
                continue

            limit = order["limit_price"]
            d     = order["direction"]
            # Long limit: buy when price dips to/below the limit
            # Short limit: sell when price rises to/above the limit
            filled = (d == "long" and low <= limit) or (d == "short" and high >= limit)

            if filled:
                self.open_trade(
                    pair=pair,
                    direction=d,
                    entry_price=limit,
                    sl=order["sl"],
                    tp_levels={"tp1": order["tp1"], "tp2": order["tp2"], "tp3": order["tp3"]},
                    size=order["size"],
                )
                logger.info("LIMIT ORDER FILLED  %s %s  at=%.5f  id=%s",
                            d.upper(), pair, limit, order["id"])
            else:
                remaining.append(order)

        self.pending_orders = remaining

    def _eval_candle(self, t: dict, high: float, low: float) -> bool:
        """Evaluate SL/TP against one candle. Returns True when fully closed."""
        d = t["direction"]

        # SL checked BEFORE TP (worst-case fill)
        sl_hit = (d == "long" and low <= t["sl"]) or \
                 (d == "short" and high >= t["sl"])
        if sl_hit:
            return self._close_remaining(t, t["sl"], "sl")

        if not t["tp1_hit"]:
            tp1_hit = (d == "long" and high >= t["tp1"]) or \
                      (d == "short" and low  <= t["tp1"])
            if tp1_hit:
                self._partial_close(t, t["tp1"], _TP1_PCT, "tp1")
                t["tp1_hit"] = True
                t["sl"] = t["entry"]
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
        logger.info(
            "PAPER CLOSED  %s %s  reason=%s  exit=%.5f  total_pnl=%+.2f",
            t["direction"].upper(), t["pair"], reason, price, t["realised_pnl"],
        )
        return True

    # ── Manual close (dashboard CLOSE button) ─────────────────────────────────

    def manual_close(self, trade_id: str, exit_price: float) -> bool:
        """Force-close a specific open trade at exit_price."""
        for i, t in enumerate(self.open_trades):
            if t["id"] == trade_id:
                self._close_remaining(t, exit_price, "manual")
                self.open_trades.pop(i)
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

    # ── Stats helpers (for dashboard / data_collector) ────────────────────────

    def daily_pnl(self) -> float:
        return round(self.balance - self._start_balance, 2)

    def win_rate(self, last_n: int = 0) -> float:
        trades = self.closed_trades[-last_n:] if last_n else self.closed_trades
        if not trades:
            return 0.0
        wins = sum(1 for t in trades if t.get("realised_pnl", 0) > 0)
        return round(wins / len(trades), 4)
