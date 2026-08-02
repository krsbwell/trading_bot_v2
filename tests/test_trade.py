"""
Tests for PaperTrader and TradeManager.
No broker API calls — TradeManager tests use a mock connector.

Note on PaperTrader.update() signature: (pair, candle_high, candle_low, candle_close)
Tests use positional arguments to match.

Note on breakeven SL: after TP1 the SL moves to entry (e.g. 1.0800).
Subsequent candles must use low > entry so the breakeven SL is not triggered.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import threading
import time
from pathlib import Path

import pytest
from trade.paper_trader import PaperTrader, _InstrumentedLock
from trade.trade_manager import TradeManager
import risk.risk_manager as rm
import config


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tp(entry=1.0800, sl=1.0780, direction="long"):
    from risk.risk_manager import get_tp_levels
    return get_tp_levels(entry, sl, direction)


def _open_long(pt, pair="EUR_USD", entry=1.0800, sl=1.0780, size=10_000):
    tp = _tp(entry, sl, "long")
    return pt.open_trade(pair, "long", entry, sl, tp, size)


def _open_short(pt, pair="EUR_USD", entry=1.0800, sl=1.0820, size=10_000):
    tp = _tp(entry, sl, "short")
    return pt.open_trade(pair, "short", entry, sl, tp, size)


# ═══════════════════════════════════════════════════════════════════════════════
# PaperTrader — open / account
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperTraderOpen:
    def test_open_trade_returns_id(self):
        pt = PaperTrader(500)
        tid = _open_long(pt)
        assert isinstance(tid, str) and len(tid) > 0

    def test_open_trade_appears_in_open_trades(self):
        pt = PaperTrader(500)
        tid = _open_long(pt)
        assert any(t["id"] == tid for t in pt.open_trades)

    def test_initial_remaining_is_1(self):
        pt = PaperTrader(500)
        _open_long(pt)
        assert pt.open_trades[0]["remaining"] == 1.0

    def test_balance_unchanged_at_open(self):
        pt = PaperTrader(500)
        _open_long(pt)
        assert pt.balance == 500.0

    def test_multiple_pairs_tracked(self):
        pt = PaperTrader(5000)
        _open_long(pt, "EUR_USD")
        _open_long(pt, "GBP_USD", entry=1.2500, sl=1.2480)
        assert len(pt.open_trades) == 2

    def test_get_account_keys(self):
        pt = PaperTrader(500)
        acc = pt.get_account()
        assert {"balance", "unrealized_pnl", "nav", "open_trade_count"} <= acc.keys()

    def test_unrealized_pnl_zero_at_entry(self):
        pt = PaperTrader(500)
        _open_long(pt, entry=1.0800, sl=1.0780, size=10_000)
        assert abs(pt.get_account()["unrealized_pnl"]) < 0.01

    def test_get_open_trade_by_pair(self):
        pt = PaperTrader(500)
        tid = _open_long(pt)
        t = pt.get_open_trade("EUR_USD")
        assert t is not None and t["id"] == tid

    def test_get_open_trade_missing_pair_returns_none(self):
        pt = PaperTrader(500)
        assert pt.get_open_trade("XYZ_ABC") is None


# ═══════════════════════════════════════════════════════════════════════════════
# PaperTrader — SL behaviour
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperTraderSL:
    def test_long_sl_closes_on_low(self):
        pt = PaperTrader(10_000)
        _open_long(pt, entry=1.0800, sl=1.0780, size=10_000)
        pt.update("EUR_USD", 1.0790, 1.0775, 1.0778)      # low hits SL
        assert len(pt.open_trades) == 0
        assert pt.closed_trades[0]["close_reason"] == "sl"

    def test_short_sl_closes_on_high(self):
        pt = PaperTrader(10_000)
        _open_short(pt, entry=1.0800, sl=1.0820, size=10_000)
        pt.update("EUR_USD", 1.0825, 1.0795, 1.0798)      # high hits SL
        assert len(pt.open_trades) == 0
        assert pt.closed_trades[0]["close_reason"] == "sl"

    def test_sl_not_triggered_above_level(self):
        pt = PaperTrader(10_000)
        _open_long(pt, entry=1.0800, sl=1.0780, size=10_000)
        pt.update("EUR_USD", 1.0810, 1.0785, 1.0808)      # low above SL
        assert len(pt.open_trades) == 1

    def test_sl_reduces_balance(self):
        pt = PaperTrader(10_000)
        _open_long(pt, entry=1.0800, sl=1.0780, size=10_000)
        before = pt.balance
        pt.update("EUR_USD", 1.0785, 1.0775, 1.0778)
        assert pt.balance < before

    def test_sl_loss_correct_amount(self):
        pt = PaperTrader(10_000)
        entry, sl, size = 1.0800, 1.0780, 10_000
        _open_long(pt, entry=entry, sl=sl, size=size)
        pt.update("EUR_USD", 1.0785, 1.0775, 1.0778)
        expected = (sl - entry) * size     # negative (loss on long)
        assert abs((pt.balance - 10_000) - expected) < 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# PaperTrader — TP sequence
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperTraderTP:
    @pytest.fixture(autouse=True)
    def _patch_trade_config(self, monkeypatch):
        monkeypatch.setattr(config, "BREAKEVEN_ENABLED", True)
        monkeypatch.setattr(config, "TRAILING_STOP_PIPS", 0)
        monkeypatch.setattr(config, "BREAKEVEN_BUFFER_PIPS", 0)

    def _setup(self, balance=10_000, entry=1.0800, sl=1.0780, size=10_000, direction="long"):
        pt = PaperTrader(balance)
        tp = _tp(entry, sl, direction)
        tid = pt.open_trade("EUR_USD", direction, entry, sl, tp, size)
        return pt, tid, tp

    # Candle helpers — lows are safely above the 1.0800 breakeven for candles 2+
    def _c(self, high, low, close):
        return (high, low, close)

    def test_tp1_closes_40pct(self):
        pt, tid, tp = self._setup()
        pt.update("EUR_USD", tp["tp1"], 1.0795, tp["tp1"])
        t = pt.open_trades[0]
        assert t["tp1_hit"] is True
        assert abs(t["remaining"] - 0.60) < 1e-9

    def test_tp1_sets_sl_to_breakeven(self):
        pt, tid, tp = self._setup(entry=1.0800, sl=1.0780)
        pt.update("EUR_USD", tp["tp1"] + 0.001, 1.0795, tp["tp1"])
        t = pt.open_trades[0]
        assert t["breakeven_set"] is True
        assert abs(t["sl"] - 1.0800) < 1e-9

    def test_tp2_closes_35pct(self):
        pt, tid, tp = self._setup()
        pt.update("EUR_USD", tp["tp1"] + 0.001, 1.0795, tp["tp1"])   # TP1
        # low=1.0802 keeps us safely above the 1.0800 breakeven SL
        pt.update("EUR_USD", tp["tp2"] + 0.001, 1.0802, tp["tp2"])   # TP2
        t = pt.open_trades[0]
        assert t["tp2_hit"] is True
        assert abs(t["remaining"] - 0.25) < 1e-9

    def test_tp3_fully_closes_trade(self):
        pt, tid, tp = self._setup()
        pt.update("EUR_USD", tp["tp1"] + 0.001, 1.0795, tp["tp1"])
        pt.update("EUR_USD", tp["tp2"] + 0.001, 1.0802, tp["tp2"])
        pt.update("EUR_USD", tp["tp3"] + 0.001, 1.0810, tp["tp3"])
        assert len(pt.open_trades) == 0
        assert pt.closed_trades[0]["close_reason"] == "tp3"

    def test_full_tp_sequence_increases_balance(self):
        pt, tid, tp = self._setup(balance=10_000)
        pt.update("EUR_USD", tp["tp1"] + 0.001, 1.0795, tp["tp1"])
        pt.update("EUR_USD", tp["tp2"] + 0.001, 1.0802, tp["tp2"])
        pt.update("EUR_USD", tp["tp3"] + 0.001, 1.0810, tp["tp3"])
        assert pt.balance > 10_000

    def test_sl_checked_before_tp1_same_candle(self):
        """When SL and TP1 are both within the candle range, SL wins (worst-case)."""
        pt, tid, tp = self._setup(entry=1.0800, sl=1.0780)
        # low pierces SL (1.0775 < 1.0780) AND high reaches TP1
        pt.update("EUR_USD", tp["tp1"], 1.0775, 1.0790)
        assert pt.closed_trades[0]["close_reason"] == "sl"

    def test_tp_not_triggered_on_wrong_side(self):
        """Long TP1 is checked against HIGH — candle must reach tp1."""
        pt, tid, tp = self._setup()
        pt.update("EUR_USD", tp["tp1"] - 0.0001, 1.0795, 1.0802)   # high just misses TP1
        assert not pt.open_trades[0]["tp1_hit"]

    def test_short_tp1_triggered_by_low(self):
        """Short TP is below entry — triggered by candle LOW going below tp1."""
        pt, _, tp = self._setup(direction="short", entry=1.0800, sl=1.0820)
        pt.update("EUR_USD", 1.0798, tp["tp1"] - 0.001, tp["tp1"])
        assert pt.open_trades[0]["tp1_hit"] is True

    def test_candle_for_other_pair_ignored(self):
        pt = PaperTrader(10_000)
        tp = _tp(1.0800, 1.0780, "long")
        pt.open_trade("EUR_USD", "long", 1.0800, 1.0780, tp, 10_000)
        pt.update("GBP_USD", 9999, 0.0001, 0.001)    # wrong pair
        assert len(pt.open_trades) == 1               # EUR_USD untouched

    def test_breakeven_sl_triggers_correctly(self):
        """After TP1, a candle whose low = breakeven should close the trade."""
        pt, _, tp = self._setup(entry=1.0800, sl=1.0780)
        pt.update("EUR_USD", tp["tp1"] + 0.001, 1.0795, tp["tp1"])  # TP1 → BE set
        pt.update("EUR_USD", 1.0810, 1.0800, 1.0805)                 # low == BE → SL fires
        assert len(pt.open_trades) == 0
        assert pt.closed_trades[0]["close_reason"] == "sl"


# ═══════════════════════════════════════════════════════════════════════════════
# PaperTrader — ATR-adaptive trailing stop
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperTraderATRTrailing:
    @pytest.fixture(autouse=True)
    def _patch_trade_config(self, monkeypatch):
        monkeypatch.setattr(config, "BREAKEVEN_ENABLED", False)
        monkeypatch.setattr(config, "TRAILING_STOP_PIPS", 15)
        monkeypatch.setattr(config, "ATR_TRAILING_ENABLED", False)
        monkeypatch.setattr(config, "ATR_TRAILING_MULTIPLIER", 2.0)

    def _to_tp2(self, pt, tp, direction="long"):
        """Drive a fresh trade through TP1 and TP2 so trailing can activate."""
        if direction == "long":
            pt.update("EUR_USD", tp["tp1"] + 0.001, 1.0795, tp["tp1"])
            pt.update("EUR_USD", tp["tp2"] + 0.001, 1.0802, tp["tp2"])
        else:
            pt.update("EUR_USD", 1.0805, tp["tp1"] - 0.001, tp["tp1"])
            pt.update("EUR_USD", 1.0803, tp["tp2"] - 0.001, tp["tp2"])

    # tp3 for these entry/sl combos sits at 1.0870 (long) / 1.0730 (short) —
    # trailing-candle highs/lows below deliberately stay clear of it so the
    # remaining 25% isn't fully closed by TP3 before trailing can be observed.

    def test_atr_trailing_disabled_falls_back_to_pip_trail(self):
        """Flag off: an atr_value is passed in but ignored — the existing
        fixed-pip trail still applies, unchanged behavior."""
        pt = PaperTrader(10_000)
        tp = _tp(1.0800, 1.0780, "long")
        pt.open_trade("EUR_USD", "long", 1.0800, 1.0780, tp, 10_000)
        self._to_tp2(pt, tp)
        sl_before = pt.open_trades[0]["sl"]
        pt.update("EUR_USD", 1.0865, 1.0855, 1.0860, atr_value=0.0020)
        t = pt.open_trades[0]
        # Fixed 15-pip trail from close 1.0860 = 1.0860 - 0.0015 = 1.0845
        assert abs(t["sl"] - 1.0845) < 1e-9
        assert t["sl"] > sl_before

    def test_atr_trailing_enabled_long(self, monkeypatch):
        monkeypatch.setattr(config, "ATR_TRAILING_ENABLED", True)
        pt = PaperTrader(10_000)
        tp = _tp(1.0800, 1.0780, "long")
        pt.open_trade("EUR_USD", "long", 1.0800, 1.0780, tp, 10_000)
        self._to_tp2(pt, tp)
        sl_before = pt.open_trades[0]["sl"]
        pt.update("EUR_USD", 1.0865, 1.0855, 1.0860, atr_value=0.0020)
        t = pt.open_trades[0]
        # ATR(0.0020) * multiplier(2.0) = 0.0040 trail from close 1.0860
        assert abs(t["sl"] - 1.0820) < 1e-9   # 1.0860 - 0.0040
        assert t["sl"] > sl_before

    def test_atr_trailing_enabled_short(self, monkeypatch):
        monkeypatch.setattr(config, "ATR_TRAILING_ENABLED", True)
        pt = PaperTrader(10_000)
        tp = _tp(1.0800, 1.0820, "short")
        pt.open_trade("EUR_USD", "short", 1.0800, 1.0820, tp, 10_000)
        self._to_tp2(pt, tp, direction="short")
        sl_before = pt.open_trades[0]["sl"]
        pt.update("EUR_USD", 1.0745, 1.0735, 1.0740, atr_value=0.0020)
        t = pt.open_trades[0]
        assert abs(t["sl"] - 1.0780) < 1e-9   # 1.0740 + 0.0040
        assert t["sl"] < sl_before

    def test_atr_trailing_only_advances_never_retreats(self, monkeypatch):
        monkeypatch.setattr(config, "ATR_TRAILING_ENABLED", True)
        pt = PaperTrader(10_000)
        tp = _tp(1.0800, 1.0780, "long")
        pt.open_trade("EUR_USD", "long", 1.0800, 1.0780, tp, 10_000)
        self._to_tp2(pt, tp)
        pt.update("EUR_USD", 1.0865, 1.0855, 1.0860, atr_value=0.0020)   # SL -> 1.0820
        sl_after_first_trail = pt.open_trades[0]["sl"]
        assert abs(sl_after_first_trail - 1.0820) < 1e-9
        # A much wider ATR now would compute 1.0865 - 0.0200 = 1.0665, well
        # below the current 1.0820 SL — must not retreat. High kept at 1.0868,
        # clear of the 1.0870 tp3 (see class comment above), so the remaining
        # 25% stays open and trailing can actually be observed.
        pt.update("EUR_USD", 1.0868, 1.0860, 1.0865, atr_value=0.0100)
        assert pt.open_trades[0]["sl"] == sl_after_first_trail

    def test_atr_trailing_inactive_before_tp2(self, monkeypatch):
        monkeypatch.setattr(config, "ATR_TRAILING_ENABLED", True)
        pt = PaperTrader(10_000)
        tp = _tp(1.0800, 1.0780, "long")
        pt.open_trade("EUR_USD", "long", 1.0800, 1.0780, tp, 10_000)
        sl_before = pt.open_trades[0]["sl"]
        pt.update("EUR_USD", 1.0805, 1.0795, 1.0800, atr_value=0.0050)   # no TP hit yet
        assert pt.open_trades[0]["sl"] == sl_before


# ═══════════════════════════════════════════════════════════════════════════════
# PaperTrader — manual close & stats
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperTraderMisc:
    def test_manual_close(self):
        pt = PaperTrader(10_000)
        tid = _open_long(pt, entry=1.0800, sl=1.0780, size=10_000)
        assert pt.manual_close(tid, exit_price=1.0850) is True
        assert len(pt.open_trades) == 0

    def test_manual_close_wrong_id_returns_false(self):
        pt = PaperTrader(10_000)
        assert pt.manual_close("NOTREAL", 1.0850) is False

    def test_win_rate_no_trades(self):
        assert PaperTrader(500).win_rate() == 0.0

    def test_win_rate_all_wins(self):
        pt = PaperTrader(10_000)
        tp = _tp(1.0800, 1.0780, "long")
        tid = pt.open_trade("EUR_USD", "long", 1.0800, 1.0780, tp, 10_000)
        pt.manual_close(tid, 1.0820)
        assert pt.win_rate() == 1.0

    def test_daily_pnl_starts_zero(self):
        assert PaperTrader(10_000).daily_pnl() == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# TradeManager — with mock connector
# ═══════════════════════════════════════════════════════════════════════════════

class MockForexConnector:
    def __init__(self):
        self.orders, self.closes, self.sl_updates = [], [], []
        self._balance = 10_000.0
        self._counter = 0
        # Limit-order simulation: tests mutate these directly to simulate
        # the broker's async fill/expiry, same way OANDA itself would.
        self.pending       = []   # list of order dicts, as get_pending_orders() returns
        self.broker_trades = []   # list of trade dicts, as get_open_trades() returns
        # reconcile_open_trades() simulation: keyed by trade_id, as
        # get_trade_details() would return. Tests populate this to control
        # what "the broker says happened" to a trade that's no longer open.
        self.trade_details = {}
        self.trade_details_raises = set()   # trade_ids where the fetch itself fails

    def place_market_order(self, instrument, units, sl_price, tp_prices):
        self._counter += 1
        tid = f"MOCK-{self._counter:04d}"
        self.orders.append({"id": tid, "instrument": instrument, "units": units})
        return tid

    def place_limit_order(self, instrument, units, limit_price, sl_price,
                          tp_prices, expiry_minutes=None):
        self._counter += 1
        oid = f"MOCK-ORD-{self._counter:04d}"
        self.pending.append({
            "id": oid, "instrument": instrument, "units": units,
            "price": limit_price, "state": "PENDING",
        })
        return oid

    def cancel_order(self, order_id):
        self.pending = [o for o in self.pending if o["id"] != order_id]
        return {"status": "cancelled"}

    def get_pending_orders(self):
        return list(self.pending)

    def get_open_trades(self):
        return list(self.broker_trades)

    def get_trade_details(self, trade_id):
        if trade_id in self.trade_details_raises:
            raise RuntimeError(f"mock fetch failure for {trade_id}")
        return self.trade_details.get(trade_id, {
            "state": "CLOSED", "open_time": None, "close_time": None,
            "avg_close_price": None, "realized_pl": 0.0, "close_reason": "unknown",
        })

    def close_trade(self, trade_id, units=None):
        self.closes.append({"trade_id": trade_id, "units": units})
        return {"status": "closed"}

    def set_sl_tp(self, trade_id, sl_price, tp_price=None):
        self.sl_updates.append({"trade_id": trade_id, "sl": sl_price})
        return {"status": "updated"}

    def get_account_summary(self):
        return {"balance": self._balance, "nav": self._balance, "unrealized_pnl": 0.0}


def _signal(pair="EUR_USD", direction="long", entry=1.0800, sl=1.0780, score=80):
    from risk.risk_manager import get_tp_levels
    return {
        "pair": pair, "direction": direction, "entry": entry,
        "stop_loss": sl, "tp_levels": get_tp_levels(entry, sl, direction),
        "score": score, "market": "forex",
    }


class TestTradeManagerForex:
    def setup_method(self):
        rm.TRADING_HALTED = False
        rm._daily_loss = 0.0

    def test_open_trade_returns_id(self):
        tm = TradeManager(MockForexConnector(), "forex")
        tid = tm.open_trade(_signal())
        assert tid is not None and tid in tm.open_trades

    def test_order_placed_on_connector(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tm.open_trade(_signal(direction="long"))
        assert len(conn.orders) == 1 and conn.orders[0]["units"] > 0

    def test_short_order_negative_units(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tm.open_trade(_signal(direction="short", entry=1.0800, sl=1.0820))
        assert conn.orders[0]["units"] < 0

    def test_rejected_low_score(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        assert tm.open_trade(_signal(score=50)) is None
        assert len(conn.orders) == 0

    def test_rejected_duplicate_pair(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tm.open_trade(_signal())
        assert tm.open_trade(_signal()) is None   # same pair
        assert len(conn.orders) == 1

    def test_rejected_max_trades(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tm.open_trade(_signal(pair="EUR_USD"))
        tm.open_trade(_signal(pair="GBP_USD", entry=1.2500, sl=1.2480))
        tm.open_trade(_signal(pair="AUD_USD", entry=0.6500, sl=0.6480))
        assert tm.open_trade(_signal(pair="USD_JPY", entry=150.0, sl=149.8)) is None

    def test_sl_triggers_close(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(entry=1.0800, sl=1.0780))
        tm.on_candle_close("EUR_USD", {"high": 1.0785, "low": 1.0772, "close": 1.0775})
        assert tid not in tm.open_trades
        assert len(conn.closes) >= 1

    def test_tp1_partial_close_issued(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(entry=1.0800, sl=1.0780))
        tp1 = tm.open_trades[tid]["tp1"]
        tm.on_candle_close("EUR_USD", {"high": tp1 + 0.001, "low": 1.0795, "close": tp1})
        assert tm.open_trades[tid]["tp1_hit"] is True
        assert len(conn.closes) == 1

    def test_tp1_sl_moved_to_breakeven(self, monkeypatch):
        monkeypatch.setattr(config, "BREAKEVEN_ENABLED", True)
        monkeypatch.setattr(config, "BREAKEVEN_BUFFER_PIPS", 0)
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(entry=1.0800, sl=1.0780))
        tp1 = tm.open_trades[tid]["tp1"]
        tm.on_candle_close("EUR_USD", {"high": tp1 + 0.001, "low": 1.0795, "close": tp1})
        assert len(conn.sl_updates) == 1
        assert abs(conn.sl_updates[0]["sl"] - 1.0800) < 1e-9

    def test_tp1_breakeven_off_by_default_leaves_sl_unchanged(self):
        """BREAKEVEN_ENABLED defaults False (backtest-validated) — TP1 must
        not move the SL when it's off, matching PaperTrader's behavior."""
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(entry=1.0800, sl=1.0780))
        tp1 = tm.open_trades[tid]["tp1"]
        tm.on_candle_close("EUR_USD", {"high": tp1 + 0.001, "low": 1.0795, "close": tp1})
        assert len(conn.sl_updates) == 0
        assert abs(tm.open_trades[tid]["sl"] - 1.0780) < 1e-9

    def test_full_tp_sequence_removes_trade(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(entry=1.0800, sl=1.0780))
        t = tm.open_trades[tid]
        # Candle 1: TP1 hit
        tm.on_candle_close("EUR_USD", {"high": t["tp1"] + 0.001, "low": 1.0795, "close": t["tp1"]})
        # Candle 2: TP2 hit — low=1.0802 safely above 1.0800 breakeven SL
        tm.on_candle_close("EUR_USD", {"high": t["tp2"] + 0.001, "low": 1.0802, "close": t["tp2"]})
        # Candle 3: TP3 hit — low=1.0810 above breakeven
        tm.on_candle_close("EUR_USD", {"high": t["tp3"] + 0.001, "low": 1.0810, "close": t["tp3"]})
        assert tid not in tm.open_trades
        assert len(conn.closes) == 3

    def test_manual_close(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal())
        tm.close_trade(tid, 1.0800, reason="manual")
        assert tid not in tm.open_trades
        assert len(conn.closes) == 1

    def test_open_pairs_list(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tm.open_trade(_signal(pair="EUR_USD"))
        tm.open_trade(_signal(pair="GBP_USD", entry=1.25, sl=1.248))
        assert "EUR_USD" in tm.open_pairs()
        assert "GBP_USD" in tm.open_pairs()

    def test_get_open_trade_by_pair(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(pair="EUR_USD"))
        t = tm.get_open_trade("EUR_USD")
        assert t is not None and t["id"] == tid

    def test_get_open_trade_missing_pair_returns_none(self):
        tm = TradeManager(MockForexConnector(), "forex")
        assert tm.get_open_trade("XYZ_ABC") is None

    def test_sl_close_added_to_closed_trades(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(entry=1.0800, sl=1.0780))
        tm.on_candle_close("EUR_USD", {"high": 1.0785, "low": 1.0772, "close": 1.0775})
        assert len(tm.closed_trades) == 1
        assert tm.closed_trades[0]["id"] == tid
        assert tm.closed_trades[0]["close_reason"] == "sl"
        assert tm.closed_trades[0]["realised_pnl"] < 0   # loss on a long SL hit

    def test_tp3_full_close_realised_pnl_positive(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(entry=1.0800, sl=1.0780))
        t = tm.open_trades[tid]
        tm.on_candle_close("EUR_USD", {"high": t["tp1"] + 0.001, "low": 1.0795, "close": t["tp1"]})
        tm.on_candle_close("EUR_USD", {"high": t["tp2"] + 0.001, "low": 1.0802, "close": t["tp2"]})
        tm.on_candle_close("EUR_USD", {"high": t["tp3"] + 0.001, "low": 1.0810, "close": t["tp3"]})
        assert len(tm.closed_trades) == 1
        assert tm.closed_trades[0]["close_reason"] == "tp3"
        assert tm.closed_trades[0]["realised_pnl"] > 0

    def test_record_close_called_on_full_close(self, monkeypatch):
        """Previously only PaperTrader closes ever reached the ML training
        data — a live close must feed learning.data_collector too."""
        calls = []
        import learning.data_collector as dc
        monkeypatch.setattr(dc, "record_close", lambda **kw: calls.append(kw))
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(entry=1.0800, sl=1.0780))
        tm.on_candle_close("EUR_USD", {"high": 1.0785, "low": 1.0772, "close": 1.0775})
        assert len(calls) == 1
        assert calls[0]["trade_id"] == tid
        assert calls[0]["outcome"] == "loss"

    def test_atr_trailing_via_on_candle_close(self, monkeypatch):
        monkeypatch.setattr(config, "ATR_TRAILING_ENABLED", True)
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(entry=1.0800, sl=1.0780))
        t = tm.open_trades[tid]
        tm.on_candle_close("EUR_USD", {"high": t["tp1"] + 0.001, "low": 1.0795, "close": t["tp1"]})
        tm.on_candle_close("EUR_USD", {"high": t["tp2"] + 0.001, "low": 1.0802, "close": t["tp2"]})
        # Trailing candle — kept clear of tp3 (1.0870) so it isn't closed early.
        tm.on_candle_close(
            "EUR_USD", {"high": 1.0865, "low": 1.0855, "close": 1.0860}, atr_value=0.0020,
        )
        # ATR(0.0020) * multiplier(2.0) = 0.0040 trail from close 1.0860 -> 1.0820
        assert abs(tm.open_trades[tid]["sl"] - 1.0820) < 1e-9
        assert any(abs(u["sl"] - 1.0820) < 1e-9 for u in conn.sl_updates)

    def test_atr_trailing_disabled_by_default_via_on_candle_close(self):
        """Flag off (default): TP2 alone must not move the SL at all — this
        class has no fixed-pip trailing fallback (unlike PaperTrader), it's
        purely additive and only active when explicitly enabled."""
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(entry=1.0800, sl=1.0780))
        t = tm.open_trades[tid]
        tm.on_candle_close("EUR_USD", {"high": t["tp1"] + 0.001, "low": 1.0795, "close": t["tp1"]})
        tm.on_candle_close("EUR_USD", {"high": t["tp2"] + 0.001, "low": 1.0802, "close": t["tp2"]})
        sl_updates_before = len(conn.sl_updates)
        tm.on_candle_close(
            "EUR_USD", {"high": 1.0865, "low": 1.0855, "close": 1.0860}, atr_value=0.0020,
        )
        assert len(conn.sl_updates) == sl_updates_before
        assert abs(tm.open_trades[tid]["sl"] - 1.0780) < 1e-9   # unchanged


class TestTradeManagerLimitOrders:
    """
    Real limit orders fill on the broker's server automatically — unlike
    PaperTrader, which only fills a pending order when its own candle-check
    notices. reconcile_pending_orders() is how we find out; these tests
    drive MockForexConnector's pending/broker_trades lists directly to
    simulate the broker having already resolved an order one way or another,
    exactly the way OANDA itself would between polls.
    """
    def setup_method(self):
        rm.TRADING_HALTED = False
        rm._daily_loss = 0.0

    def test_open_limit_order_returns_id_and_stores_pending(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        oid = tm.open_limit_order(_signal(), limit_price=1.0750)
        assert oid is not None
        assert oid in tm.pending_orders
        assert tm.pending_orders[oid]["pair"] == "EUR_USD"
        assert len(conn.pending) == 1

    def test_open_limit_order_rejects_low_score(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        assert tm.open_limit_order(_signal(score=50), limit_price=1.0750) is None
        assert len(conn.pending) == 0

    def test_open_limit_order_rejects_duplicate_pending_pair(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        tm.open_limit_order(_signal(pair="EUR_USD"), limit_price=1.0750)
        # Same pair already has a pending order — should be rejected even
        # though open_trades is still empty (open_trade()'s own duplicate
        # check wouldn't catch this, since it only looks at open_trades).
        oid2 = tm.open_limit_order(_signal(pair="EUR_USD"), limit_price=1.0760)
        assert oid2 is None
        assert len(conn.pending) == 1

    def test_cancel_limit_order(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        oid = tm.open_limit_order(_signal(), limit_price=1.0750)
        assert tm.cancel_limit_order(oid) is True
        assert oid not in tm.pending_orders
        assert len(conn.pending) == 0

    def test_cancel_limit_order_not_found(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        assert tm.cancel_limit_order("nonexistent") is False

    def test_reconcile_still_pending_is_a_no_op(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        oid = tm.open_limit_order(_signal(), limit_price=1.0750)
        tm.reconcile_pending_orders()
        assert oid in tm.pending_orders   # broker still lists it — untouched

    def test_reconcile_promotes_filled_order_to_open_trade(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        oid = tm.open_limit_order(_signal(pair="EUR_USD", direction="long"),
                                  limit_price=1.0750)
        # Simulate the broker having filled it: order no longer pending,
        # a new trade appears with matching instrument + direction.
        conn.pending = []
        conn.broker_trades = [{
            "id": "BROKER-99", "instrument": "EUR_USD", "units": 2000,
            "price": 1.0751, "unrealizedPL": 0.0, "stopLoss": 1.0780, "takeProfit": None,
        }]
        tm.reconcile_pending_orders()
        assert oid not in tm.pending_orders
        assert "BROKER-99" in tm.open_trades
        t = tm.open_trades["BROKER-99"]
        assert t["pair"] == "EUR_USD" and t["direction"] == "long"
        assert t["entry"] == 1.0751
        assert t["remaining"] == 1.0

    def test_modify_pending_order_returns_new_id(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        oid = tm.open_limit_order(_signal(pair="EUR_USD"), limit_price=1.0750)
        new_oid = tm.modify_pending_order(oid, limit_price=1.0760)
        assert new_oid is not None and new_oid != oid
        assert oid not in tm.pending_orders
        assert tm.pending_orders[new_oid]["limit_price"] == 1.0760
        # Old broker order cancelled, new one placed
        assert len(conn.pending) == 1
        assert conn.pending[0]["price"] == 1.0760

    def test_modify_pending_order_partial_update_keeps_other_fields(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        oid = tm.open_limit_order(_signal(pair="EUR_USD"), limit_price=1.0750)
        original_tp1 = tm.pending_orders[oid]["tp1"]
        new_oid = tm.modify_pending_order(oid, sl=1.0700)   # only SL changed
        updated = tm.pending_orders[new_oid]
        assert updated["sl"] == 1.0700
        assert updated["limit_price"] == 1.0750   # unchanged
        assert updated["tp1"] == original_tp1      # unchanged

    def test_modify_pending_order_not_found(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        assert tm.modify_pending_order("nonexistent", sl=1.07) is None

    def test_reconcile_drops_expired_order_without_promoting(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        oid = tm.open_limit_order(_signal(pair="EUR_USD"), limit_price=1.0750)
        # Simulate GTD expiry: order gone from pending, no matching trade appeared.
        conn.pending = []
        conn.broker_trades = []
        tm.reconcile_pending_orders()
        assert oid not in tm.pending_orders
        assert len(tm.open_trades) == 0

    def test_pending_orders_survive_restart(self, tmp_path):
        path = tmp_path / "live_state.json"
        conn = MockForexConnector()
        tm1 = TradeManager(conn, "forex", save_path=path)
        oid = tm1.open_limit_order(_signal(), limit_price=1.0750)

        tm2 = TradeManager(MockForexConnector(), "forex", save_path=path)
        assert oid in tm2.pending_orders
        assert tm2.pending_orders[oid]["pair"] == "EUR_USD"

    def test_load_state_without_pending_orders_key_is_backward_compatible(self, tmp_path):
        """Old state files saved before this feature existed have no
        'pending_orders' key at all — must load cleanly, not crash."""
        import json
        path = tmp_path / "live_state.json"
        path.write_text(json.dumps({"open_trades": {}, "closed_trades": []}), encoding="utf-8")
        tm = TradeManager(MockForexConnector(), "forex", save_path=path)
        assert tm.pending_orders == {}


class TestTradeManagerReconcileOpenTrades:
    """
    A real broker SL/TP fires off bid/ask; our own SL/TP check
    (_eval_candle) only ever sees mid-price M30 candle data — so a close
    within a pip or two of the recorded level can happen broker-side
    without our own candle check ever independently detecting it, no
    matter how often it runs. reconcile_open_trades() is the only
    reliable backstop: ask the broker directly what it still has.
    """
    def setup_method(self):
        rm.TRADING_HALTED = False
        rm._daily_loss = 0.0

    def test_still_open_broker_side_is_a_no_op(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        tid = tm.open_trade(_signal(pair="EUR_USD"))
        conn.broker_trades = [{"id": tid, "instrument": "EUR_USD", "units": 2000,
                               "price": 1.0800, "unrealizedPL": 0.0,
                               "stopLoss": 1.0780, "takeProfit": None}]
        tm.reconcile_open_trades()
        assert tid in tm.open_trades

    def test_reconciles_real_sl_close_our_own_candle_check_missed(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        tid = tm.open_trade(_signal(pair="EUR_USD", entry=1.0800, sl=1.0780))
        # Broker has nothing for this pair anymore — position already closed.
        conn.broker_trades = []
        conn.trade_details[tid] = {
            "state": "CLOSED", "open_time": None, "close_time": None,
            "avg_close_price": 1.0779, "realized_pl": -42.0, "close_reason": "sl",
        }
        tm.reconcile_open_trades()
        assert tid not in tm.open_trades
        closed = tm.closed_trades[-1]
        assert closed["id"] == tid
        assert closed["close_reason"] == "sl"
        assert closed["exit_price"] == 1.0779
        assert closed["realised_pnl"] == -42.0

    def test_reconciles_real_tp_close(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        tid = tm.open_trade(_signal(pair="EUR_USD"))
        conn.broker_trades = []
        conn.trade_details[tid] = {
            "state": "CLOSED", "open_time": None, "close_time": None,
            "avg_close_price": 1.0850, "realized_pl": 55.0, "close_reason": "tp",
        }
        tm.reconcile_open_trades()
        assert tm.closed_trades[-1]["close_reason"] == "tp"

    def test_detail_fetch_failure_still_finalizes_with_best_estimate(self):
        """The trade is definitely gone broker-side either way — don't leave
        a permanent phantom just because the follow-up detail fetch also failed."""
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        tid = tm.open_trade(_signal(pair="EUR_USD", entry=1.0800, sl=1.0780))
        conn.broker_trades = []
        conn.trade_details_raises.add(tid)
        tm.reconcile_open_trades()
        assert tid not in tm.open_trades
        assert tm.closed_trades[-1]["close_reason"] == "unknown_broker_close"

    def test_get_open_trades_failure_leaves_state_untouched(self):
        """Can't tell what's missing if the broker poll itself fails —
        must not guess and drop real open trades."""
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        tid = tm.open_trade(_signal(pair="EUR_USD"))

        def _boom():
            raise RuntimeError("network error")
        conn.get_open_trades = _boom
        tm.reconcile_open_trades()
        assert tid in tm.open_trades

    def test_no_open_trades_is_a_cheap_no_op(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex", save_path=False)
        tm.reconcile_open_trades()   # must not raise with nothing to check

    def test_reconciled_close_survives_restart(self, tmp_path):
        path = tmp_path / "live_state.json"
        conn = MockForexConnector()
        tm1 = TradeManager(conn, "forex", save_path=path)
        tid = tm1.open_trade(_signal(pair="EUR_USD", entry=1.0800, sl=1.0780))
        conn.broker_trades = []
        conn.trade_details[tid] = {
            "state": "CLOSED", "open_time": None, "close_time": None,
            "avg_close_price": 1.0779, "realized_pl": -42.0, "close_reason": "sl",
        }
        tm1.reconcile_open_trades()

        tm2 = TradeManager(MockForexConnector(), "forex", save_path=path)
        assert tid not in tm2.open_trades
        assert any(c["id"] == tid and c["close_reason"] == "sl" for c in tm2.closed_trades)


# ── _InstrumentedLock (scheduler-freeze diagnostic, tasks/todo_scheduler_freeze.md) ──

class TestInstrumentedLock:
    def test_behaves_as_a_normal_mutual_exclusion_lock(self):
        lock = _InstrumentedLock()
        order = []

        def worker():
            with lock:
                order.append("worker-in")
                time.sleep(0.05)
                order.append("worker-out")

        t = threading.Thread(target=worker)
        with lock:
            t.start()
            time.sleep(0.15)   # worker must block here, not interleave
            order.append("main-holds")
        t.join(timeout=2)
        assert order == ["main-holds", "worker-in", "worker-out"]

    def test_reentrant_use_across_sequential_with_blocks_does_not_deadlock(self):
        lock = _InstrumentedLock()
        with lock:
            pass
        with lock:   # second, independent acquisition must succeed
            pass

    def test_warns_when_acquisition_is_slow(self, caplog):
        lock = _InstrumentedLock()
        lock._SLOW_S = 0.05   # instance override, doesn't touch the class default
        acquired = threading.Event()

        def hold():
            with lock:
                acquired.set()
                time.sleep(0.15)   # holds well past _SLOW_S while main tries to acquire

        t = threading.Thread(target=hold)
        t.start()
        assert acquired.wait(timeout=2)   # don't race the acquire itself
        with caplog.at_level(logging.WARNING):
            with lock:   # blocks ~0.15s waiting for `hold` to release
                pass
        t.join(timeout=2)
        assert any("lock acquisition took" in r.message for r in caplog.records)

    def test_warns_when_held_too_long(self, caplog):
        lock = _InstrumentedLock()
        lock._SLOW_S = 0.05
        with caplog.at_level(logging.WARNING):
            with lock:
                time.sleep(0.1)
        assert any("lock held for" in r.message for r in caplog.records)

    def test_no_warning_on_fast_acquire_and_release(self, caplog):
        lock = _InstrumentedLock()
        with caplog.at_level(logging.WARNING):
            with lock:
                pass
        assert not any("lock" in r.message for r in caplog.records)


# ── TradeManager disk persistence (tasks/todo.md, added 2026-07-20) ─────────────

class TestTradeManagerPersistence:
    def test_open_trade_survives_restart(self, tmp_path):
        path = tmp_path / "live_state.json"
        tm1 = TradeManager(MockForexConnector(), "forex", save_path=path)
        tid = tm1.open_trade(_signal(entry=1.0800, sl=1.0780))

        tm2 = TradeManager(MockForexConnector(), "forex", save_path=path)
        assert tid in tm2.open_trades
        assert tm2.open_trades[tid]["pair"]      == "EUR_USD"
        assert tm2.open_trades[tid]["entry"]     == 1.0800
        assert tm2.open_trades[tid]["direction"] == "long"

    def test_partial_close_state_survives_restart(self, tmp_path):
        path = tmp_path / "live_state.json"
        tm1 = TradeManager(MockForexConnector(), "forex", save_path=path)
        tid = tm1.open_trade(_signal(entry=1.0800, sl=1.0780))
        t = tm1.open_trades[tid]
        tm1.on_candle_close("EUR_USD", {"high": t["tp1"] + 0.001, "low": 1.0795, "close": t["tp1"]})
        assert tm1.open_trades[tid]["tp1_hit"] is True

        tm2 = TradeManager(MockForexConnector(), "forex", save_path=path)
        restored = tm2.open_trades[tid]
        assert restored["tp1_hit"] is True
        assert abs(restored["remaining"] - 0.60) < 1e-9
        assert restored["realised_pnl"] != 0.0

    def test_full_close_moves_trade_to_closed_and_survives_restart(self, tmp_path):
        path = tmp_path / "live_state.json"
        tm1 = TradeManager(MockForexConnector(), "forex", save_path=path)
        tid = tm1.open_trade(_signal(entry=1.0800, sl=1.0780))
        t = tm1.open_trades[tid]
        # Drive price through TP1 -> TP2 -> TP3 so the trade fully closes
        tm1.on_candle_close("EUR_USD", {"high": t["tp1"] + 0.001, "low": 1.0795, "close": t["tp1"]})
        tm1.on_candle_close("EUR_USD", {"high": t["tp2"] + 0.001, "low": 1.0802, "close": t["tp2"]})
        tm1.on_candle_close("EUR_USD", {"high": t["tp3"] + 0.001, "low": 1.0804, "close": t["tp3"]})
        assert tid not in tm1.open_trades
        assert any(c["id"] == tid for c in tm1.closed_trades)

        tm2 = TradeManager(MockForexConnector(), "forex", save_path=path)
        assert tid not in tm2.open_trades
        assert any(c["id"] == tid for c in tm2.closed_trades)
        assert tm2.closed_trades[-1]["close_reason"] == "tp3"

    def test_manual_close_survives_restart(self, tmp_path):
        path = tmp_path / "live_state.json"
        tm1 = TradeManager(MockForexConnector(), "forex", save_path=path)
        tid = tm1.open_trade(_signal())
        tm1.close_trade(tid, 1.0800)
        assert tid not in tm1.open_trades

        tm2 = TradeManager(MockForexConnector(), "forex", save_path=path)
        assert tid not in tm2.open_trades

    def test_save_path_false_disables_disk_io(self, tmp_path, monkeypatch):
        import trade.trade_manager as tm_mod
        monkeypatch.setattr(tm_mod, "_DEFAULT_SAVE_DIR", tmp_path)
        tm = TradeManager(MockForexConnector(), "forex", save_path=False)
        tm.open_trade(_signal())
        assert list(tmp_path.iterdir()) == []   # nothing written anywhere

    def test_missing_state_file_starts_empty_not_erroring(self, tmp_path):
        tm = TradeManager(MockForexConnector(), "forex", save_path=tmp_path / "does_not_exist.json")
        assert tm.open_trades == {}
        assert tm.closed_trades == []

    def test_corrupt_state_file_starts_empty_not_erroring(self, tmp_path):
        path = tmp_path / "live_state.json"
        path.write_text("{not valid json", encoding="utf-8")
        tm = TradeManager(MockForexConnector(), "forex", save_path=path)
        assert tm.open_trades == {}
        assert tm.closed_trades == []

    def test_default_save_path_is_per_market(self, tmp_path, monkeypatch):
        import trade.trade_manager as tm_mod
        monkeypatch.setattr(tm_mod, "_DEFAULT_SAVE_DIR", tmp_path)
        TradeManager(MockForexConnector(), "forex").open_trade(_signal())
        assert (tmp_path / "live_state_forex.json").exists()
