"""
Live trade manager — routes orders to the real broker and manages the
partial-close sequence candle by candle.

Strategy:
  - SL is attached to the initial order (broker-side protection if bot goes offline).
  - TPs are NOT attached to the broker order — the trade manager monitors them
    internally via candle high/low and issues manual partial-close requests.
    This is the only reliable way to execute the 40/35/25 split.

Partial close sequence (mirrors PaperTrader):
  TP1 → close 40% of original size, move SL to breakeven via connector
  TP2 → close 35%
  TP3 → close remaining 25%

Persistence:
  State (open/closed trades) is written to disk after every mutation, same
  convention as trade/paper_trader.py — a restart while config.MODE == "live"
  no longer silently loses track of open positions. Default path:
  data/live_state_<market>.json.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from risk.risk_manager import (
    calculate_position_size, get_tp_levels, validate_pre_trade, update_daily_loss,
)
from trade.paper_trader import _ser, _deser

logger = logging.getLogger(__name__)

_TP1_PCT = 0.40
_TP2_PCT = 0.35
_TP3_PCT = 0.25

_DEFAULT_SAVE_DIR = Path(__file__).parent.parent / "data"


class TradeManager:
    """
    Works with OandaConnector. `market` is a label used for the state-file
    name (data/live_state_<market>.json) — pass "forex".
    """

    def __init__(self, connector, market: str, save_path: str | Path | bool = None):
        self.connector  = connector
        self.market     = market

        # save_path=False disables all disk I/O (mirrors PaperTrader — useful for tests)
        if save_path is False:
            self._save_path = None
        else:
            self._save_path = Path(save_path) if save_path else (
                _DEFAULT_SAVE_DIR / f"live_state_{market}.json"
            )

        loaded = self._load_state()
        if loaded:
            self.open_trades:   dict[str, dict] = loaded["open_trades"]
            self.closed_trades: list[dict]      = loaded["closed_trades"]
            logger.info(
                "TradeManager(%s) restored — open=%d  closed=%d",
                market, len(self.open_trades), len(self.closed_trades),
            )
        else:
            self.open_trades:   dict[str, dict] = {}   # trade_id → trade_dict
            # Needed so adaptive_params and the ML logging pipeline see live
            # closes the same way they see paper closes. Capped at 500 to
            # match PaperTrader's convention.
            self.closed_trades: list[dict] = []

    # ── Persistence ───────────────────────────────────────────────────────────
    # Same tiered-write strategy as PaperTrader._save_state (kept as an
    # independent copy rather than a shared helper, to avoid touching that
    # already-tested class for this): atomic rename first, then a
    # delete-then-rename fallback for the Windows case where an AV scanner or
    # editor briefly holds a read handle on the destination, then a direct
    # overwrite as a last resort so a save is never silently lost.

    def _save_state(self) -> None:
        if self._save_path is None:
            return
        try:
            self._save_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "open_trades":   {tid: _ser(t) for tid, t in self.open_trades.items()},
                "closed_trades": [_ser(t) for t in self.closed_trades[-500:]],
                "saved_at":      datetime.now(timezone.utc).isoformat(),
            }
            json_str = json.dumps(payload, indent=2)
            tmp = Path(str(self._save_path) + ".tmp")
            tmp.write_text(json_str, encoding="utf-8")

            for _attempt in range(3):
                try:
                    os.replace(tmp, self._save_path)
                    return
                except OSError:
                    if _attempt < 2:
                        time.sleep(0.05)

            try:
                self._save_path.unlink(missing_ok=True)
                tmp.rename(self._save_path)
                return
            except OSError:
                pass

            self._save_path.write_text(json_str, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            logger.debug("TradeManager._save_state: used direct-write fallback")

        except Exception as exc:
            logger.warning("TradeManager._save_state failed: %s", exc)

    def _load_state(self) -> dict | None:
        if self._save_path is None:
            return None
        try:
            if not self._save_path.exists():
                return None
            with open(self._save_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            logger.info(
                "TradeManager._load_state: reading %s — open=%d  closed=%d",
                self._save_path, len(raw.get("open_trades", {})), len(raw.get("closed_trades", [])),
            )
            return {
                "open_trades":   {tid: _deser(t) for tid, t in raw.get("open_trades", {}).items()},
                "closed_trades": [_deser(t) for t in raw.get("closed_trades", [])],
            }
        except Exception as exc:
            logger.warning("TradeManager._load_state FAILED: %s", exc, exc_info=True)
            return None

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
        size      = calculate_position_size(balance, entry, stop_loss, pair)

        if size <= 0:
            logger.error("Calculated size <= 0 for %s — skipping", pair)
            return None

        # Place order — SL attached, no TP (managed internally)
        try:
            trade_id = self.connector.place_market_order(
                instrument=pair,
                units=size if direction == "long" else -size,
                sl_price=stop_loss,
                tp_prices=[],          # managed by trade manager
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
            "realised_pnl":  0.0,
        }
        self._save_state()
        logger.info(
            "LIVE OPEN  %s %s  entry=%.5f  sl=%.5f  size=%s  id=%s",
            direction.upper(), pair, entry, stop_loss, size, trade_id,
        )
        return trade_id

    # ── Candle update ─────────────────────────────────────────────────────────

    def on_candle_close(self, pair: str, candle: dict, atr_value: float = None) -> None:
        """
        Called after every candle close for each pair.
        candle keys: high, low, close. `atr_value` (price units, not pips) is
        only used when config.ATR_TRAILING_ENABLED — pass None otherwise.
        """
        high  = candle["high"]
        low   = candle["low"]
        close = candle["close"]

        to_remove = []
        evaluated_any = False
        for trade_id, t in self.open_trades.items():
            if t["pair"] != pair:
                continue
            evaluated_any = True
            fully_closed = self._eval_candle(t, high, low, close, trade_id, atr_value)
            if fully_closed:
                to_remove.append(trade_id)

        for tid in to_remove:
            del self.open_trades[tid]

        # Covers both partial fills (remaining/tp_hit/sl mutated in place) and
        # full closes (open_trades/closed_trades changed) — one save per pair
        # per candle is enough; skip entirely for pairs with nothing open.
        if evaluated_any:
            self._save_state()

    def _eval_candle(
        self, t: dict, high: float, low: float, close: float, trade_id: str,
        atr_value: float = None,
    ) -> bool:
        """Check SL and TPs in correct order. Returns True when fully closed."""
        d = t["direction"]

        # ── ATR-adaptive trailing stop: advance SL after TP2 ────────────────────
        # Mirrors PaperTrader's trailing logic; off by default
        # (config.ATR_TRAILING_ENABLED) so this doesn't change live behavior
        # until deliberately enabled. Pushed to the broker via the existing,
        # timeout-protected set_sl_tp() — no separate broker client.
        if config.ATR_TRAILING_ENABLED and atr_value and t.get("tp2_hit"):
            trail_gap = atr_value * config.ATR_TRAILING_MULTIPLIER
            ref_price = close
            new_sl = ref_price - trail_gap if d == "long" else ref_price + trail_gap
            advanced = (d == "long" and new_sl > t["sl"]) or (d == "short" and new_sl < t["sl"])
            if advanced:
                try:
                    self.connector.set_sl_tp(trade_id, sl_price=new_sl, tp_price=None)
                    t["sl"] = new_sl
                except Exception as exc:
                    logger.warning("Could not update ATR trailing SL for %s: %s", trade_id, exc)

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
            self._finalize_close(t, t["sl"], "sl")
            return True

        # ── TP1 ───────────────────────────────────────────────────────────────
        if not t["tp1_hit"]:
            tp1_hit = (d == "long" and high >= t["tp1"]) or \
                      (d == "short" and low  <= t["tp1"])
            if tp1_hit:
                self._exec_close(t, t["tp1"], _TP1_PCT, "tp1")
                t["tp1_hit"] = True
                t["remaining"] -= _TP1_PCT
                # Move SL to breakeven+buffer — gated the same way PaperTrader
                # already is (config.BREAKEVEN_ENABLED / BREAKEVEN_PER_PAIR).
                # This used to be unconditional here, which meant live trades
                # would breakeven even with BE backtest-validated off for
                # every active pair — paper and live disagreeing on behavior
                # that's supposed to be identical.
                _pair  = t.get("pair", "")
                _be_on = config.BREAKEVEN_PER_PAIR.get(_pair, config.BREAKEVEN_ENABLED)
                if _be_on:
                    _pip  = 0.01 if "JPY" in _pair else 0.0001
                    _buf  = config.BREAKEVEN_BUFFER_PIPS * _pip
                    _mult = 1 if d == "long" else -1
                    new_sl = round(t["entry"] + _mult * _buf, 5)
                    try:
                        self.connector.set_sl_tp(trade_id, sl_price=new_sl, tp_price=None)
                    except Exception as exc:
                        logger.warning("Could not update SL to BE for %s: %s", trade_id, exc)
                    t["sl"] = new_sl
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
                self._finalize_close(t, t["tp3"], "tp3")
                return True

        return False

    def _finalize_close(self, t: dict, price: float, reason: str) -> None:
        """
        Stamp close metadata, retain the trade in closed_trades (needed by
        adaptive_params and the dashboard, since this class isn't disk-
        persisted like PaperTrader), and feed the ML training data the same
        way a closed paper trade does — previously only paper trades ever
        reached learning.data_collector.record_close().
        """
        t["close_reason"] = reason
        t["exit_price"]   = price
        t["close_time"]   = datetime.now(timezone.utc)
        self.closed_trades.append(dict(t))
        self.closed_trades = self.closed_trades[-500:]
        logger.info(
            "LIVE CLOSED  %s %s  reason=%s  exit=%.5f  total_pnl=%+.2f",
            t["direction"].upper(), t["pair"], reason, price, t["realised_pnl"],
        )
        self._log_outcome(t)

    def _log_outcome(self, t: dict) -> None:
        """Best-effort — a logging failure must never break real trade closing."""
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

    def _exec_close(self, t: dict, price: float, fraction: float, label: str) -> None:
        """Issue a partial-close order to the broker."""
        original_size = t["size"]
        close_size    = original_size * fraction

        try:
            units = int(round(close_size))
            self.connector.close_trade(t["id"], units=units)
        except Exception as exc:
            logger.error("Partial close (%s) failed for %s: %s", label, t["id"], exc)
            raise

        pnl = self._pnl(t["entry"], price, t["direction"], close_size)
        t["realised_pnl"] = t.get("realised_pnl", 0.0) + pnl
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
            self.connector.close_trade(trade_id)
        except Exception as exc:
            logger.error("Manual close failed for %s: %s", trade_id, exc)
            raise
        del self.open_trades[trade_id]
        self._save_state()
        logger.info("LIVE CLOSED  %s  reason=%s", trade_id, reason)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_account(self) -> dict:
        try:
            return self.connector.get_account_summary()
        except Exception as exc:
            logger.error("Could not fetch account: %s", exc)
            return {"balance": 0, "cash": 0}

    @staticmethod
    def _pnl(entry: float, exit_price: float, direction: str, size: float) -> float:
        diff = (exit_price - entry) if direction == "long" else (entry - exit_price)
        return round(diff * size, 4)

    def open_pairs(self) -> list[str]:
        return [t["pair"] for t in self.open_trades.values()]

    def get_open_trade(self, pair: str) -> dict | None:
        for t in self.open_trades.values():
            if t["pair"] == pair:
                return t
        return None
