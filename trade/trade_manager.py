"""
Live trade manager — routes orders to the real broker and manages the
partial-close sequence candle by candle.

Strategy:
  - SL is attached to the initial order (broker-side protection if bot goes offline).
  - TPs are NOT attached to the broker order — the trade manager monitors them
    internally via candle high/low and issues manual partial-close requests.
    This is the only reliable way to execute the 40/35/25 split on both brokers.

Partial close sequence (mirrors PaperTrader):
  TP1 → close 40% of original size, move SL to breakeven via connector
  TP2 → close 35%
  TP3 → close remaining 25%
"""
import logging
from datetime import datetime, timezone

from risk.risk_manager import (
    calculate_position_size, get_tp_levels, validate_pre_trade, update_daily_loss,
)

logger = logging.getLogger(__name__)

_TP1_PCT = 0.40
_TP2_PCT = 0.35
_TP3_PCT = 0.25


class TradeManager:
    """
    Works with either OandaConnector or AlpacaConnector.
    Pass market="forex" for Oanda, market="crypto" for Alpaca.
    """

    def __init__(self, connector, market: str):
        self.connector  = connector
        self.market     = market        # "forex" or "crypto"
        self.open_trades: dict[str, dict] = {}   # trade_id → trade_dict

    # ── Open ──────────────────────────────────────────────────────────────────

    def open_trade(self, signal: dict) -> str | None:
        """
        Validate signal, size the position, place the order.
        Returns trade_id on success, None on validation failure.
        """
        pair       = signal["pair"]
        direction  = signal["direction"]
        entry      = signal["entry"]
        stop_loss  = signal["stop_loss"]
        tp_levels  = signal["tp_levels"]
        score      = signal["score"]

        # Pre-trade validation
        open_pairs = [t["pair"] for t in self.open_trades.values()]
        ok, reason = validate_pre_trade(score, len(self.open_trades), pair, open_pairs)
        if not ok:
            logger.warning("Trade rejected for %s: %s", pair, reason)
            return None

        # Account balance for position sizing
        account   = self._get_account()
        balance   = account.get("balance") or account.get("cash", 0)
        size      = calculate_position_size(balance, entry, stop_loss, self.market, pair)

        if size <= 0:
            logger.error("Calculated size <= 0 for %s — skipping", pair)
            return None

        # Place order — SL attached, no TP (managed internally)
        try:
            if self.market == "forex":
                trade_id = self.connector.place_market_order(
                    instrument=pair,
                    units=size if direction == "long" else -size,
                    sl_price=stop_loss,
                    tp_prices=[],          # managed by trade manager
                )
            else:
                trade_id = self.connector.place_market_order(
                    symbol=pair,
                    qty=size,
                    side=direction,
                    sl_price=stop_loss,
                    tp_prices=[],
                )
        except Exception as exc:
            logger.error("Order placement failed for %s: %s", pair, exc)
            return None

        risk_dollar = balance * 0.01
        self.open_trades[trade_id] = {
            "id":            trade_id,
            "pair":          pair,
            "market":        self.market,
            "direction":     direction,
            "entry":         entry,
            "sl":            stop_loss,
            "tp1":           tp_levels["tp1"],
            "tp2":           tp_levels["tp2"],
            "tp3":           tp_levels["tp3"],
            "size":          size,
            "remaining":     1.0,
            "tp1_hit":       False,
            "tp2_hit":       False,
            "tp3_hit":       False,
            "breakeven_set": False,
            "risk_dollar":   risk_dollar,
            "score":         score,
            "open_time":     datetime.now(timezone.utc),
        }
        logger.info(
            "LIVE OPEN  %s %s  entry=%.5f  sl=%.5f  size=%s  id=%s",
            direction.upper(), pair, entry, stop_loss, size, trade_id,
        )
        return trade_id

    # ── Candle update ─────────────────────────────────────────────────────────

    def on_candle_close(self, pair: str, candle: dict) -> None:
        """
        Called after every candle close for each pair.
        candle keys: high, low, close
        """
        high  = candle["high"]
        low   = candle["low"]
        close = candle["close"]

        to_remove = []
        for trade_id, t in self.open_trades.items():
            if t["pair"] != pair:
                continue
            fully_closed = self._eval_candle(t, high, low, close, trade_id)
            if fully_closed:
                to_remove.append(trade_id)

        for tid in to_remove:
            del self.open_trades[tid]

    def _eval_candle(
        self, t: dict, high: float, low: float, close: float, trade_id: str
    ) -> bool:
        """Check SL and TPs in correct order. Returns True when fully closed."""
        d = t["direction"]

        # ── SL check (first) ──────────────────────────────────────────────────
        # Broker-side SL may have already closed the position; we reconcile via
        # the account check on the next loop. Here we still check to update state.
        sl_hit = (d == "long" and low <= t["sl"]) or \
                 (d == "short" and high >= t["sl"])
        if sl_hit:
            try:
                self._exec_close(t, t["sl"], t["remaining"], "sl")
            except Exception as exc:
                logger.error("SL close failed for %s: %s", trade_id, exc)
            pnl = self._pnl(t["entry"], t["sl"], d, t["size"] * t["remaining"])
            update_daily_loss(pnl, self._get_account().get("balance", t["size"] * t["entry"]))
            return True

        # ── TP1 ───────────────────────────────────────────────────────────────
        if not t["tp1_hit"]:
            tp1_hit = (d == "long" and high >= t["tp1"]) or \
                      (d == "short" and low  <= t["tp1"])
            if tp1_hit:
                self._exec_close(t, t["tp1"], _TP1_PCT, "tp1")
                t["tp1_hit"] = True
                t["remaining"] -= _TP1_PCT
                # Move SL to breakeven
                try:
                    if self.market == "forex":
                        self.connector.set_sl_tp(trade_id, sl_price=t["entry"], tp_price=None)
                    # Alpaca SL is managed via the broker's existing stop order — leave as is
                    # (Alpaca doesn't support updating stop price on a bracket child easily)
                except Exception as exc:
                    logger.warning("Could not update SL to BE for %s: %s", trade_id, exc)
                t["sl"] = t["entry"]
                t["breakeven_set"] = True

        # ── TP2 ───────────────────────────────────────────────────────────────
        if t["tp1_hit"] and not t["tp2_hit"]:
            tp2_hit = (d == "long" and high >= t["tp2"]) or \
                      (d == "short" and low  <= t["tp2"])
            if tp2_hit:
                self._exec_close(t, t["tp2"], _TP2_PCT, "tp2")
                t["tp2_hit"] = True
                t["remaining"] -= _TP2_PCT

        # ── TP3 ───────────────────────────────────────────────────────────────
        if t["tp2_hit"] and not t["tp3_hit"]:
            tp3_hit = (d == "long" and high >= t["tp3"]) or \
                      (d == "short" and low  <= t["tp3"])
            if tp3_hit:
                self._exec_close(t, t["tp3"], t["remaining"], "tp3")
                t["tp3_hit"] = True
                t["remaining"] = 0.0
                return True

        return False

    def _exec_close(self, t: dict, price: float, fraction: float, label: str) -> None:
        """Issue a partial-close order to the broker."""
        original_size = t["size"]
        close_size    = original_size * fraction

        try:
            if self.market == "forex":
                units = int(round(close_size))
                self.connector.close_trade(t["id"], units=units)
            else:
                self.connector.close_position(t["pair"], qty=round(close_size, 6))
        except Exception as exc:
            logger.error("Partial close (%s) failed for %s: %s", label, t["id"], exc)
            raise

        pnl = self._pnl(t["entry"], price, t["direction"], close_size)
        logger.info(
            "LIVE %-4s  %s %s  price=%.5f  size=%.4f  pnl=%+.2f",
            label.upper(), t["direction"].upper(), t["pair"], price, close_size, pnl,
        )

    # ── Manual close ──────────────────────────────────────────────────────────

    def close_trade(self, trade_id: str, reason: str = "manual") -> None:
        """Force-close the entire remaining position."""
        t = self.open_trades.get(trade_id)
        if not t:
            logger.warning("close_trade: trade_id %s not found", trade_id)
            return
        try:
            if self.market == "forex":
                self.connector.close_trade(trade_id)
            else:
                self.connector.close_position(t["pair"])
        except Exception as exc:
            logger.error("Manual close failed for %s: %s", trade_id, exc)
            raise
        del self.open_trades[trade_id]
        logger.info("LIVE CLOSED  %s  reason=%s", trade_id, reason)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_account(self) -> dict:
        try:
            if self.market == "forex":
                return self.connector.get_account_summary()
            else:
                return self.connector.get_account()
        except Exception as exc:
            logger.error("Could not fetch account: %s", exc)
            return {"balance": 0, "cash": 0}

    @staticmethod
    def _pnl(entry: float, exit_price: float, direction: str, size: float) -> float:
        diff = (exit_price - entry) if direction == "long" else (entry - exit_price)
        return round(diff * size, 4)

    def open_pairs(self) -> list[str]:
        return [t["pair"] for t in self.open_trades.values()]
