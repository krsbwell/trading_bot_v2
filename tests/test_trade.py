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

import pytest
from trade.paper_trader import PaperTrader
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

    def place_market_order(self, instrument, units, sl_price, tp_prices):
        self._counter += 1
        tid = f"MOCK-{self._counter:04d}"
        self.orders.append({"id": tid, "instrument": instrument, "units": units})
        return tid

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

    def test_tp1_sl_moved_to_breakeven(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tid = tm.open_trade(_signal(entry=1.0800, sl=1.0780))
        tp1 = tm.open_trades[tid]["tp1"]
        tm.on_candle_close("EUR_USD", {"high": tp1 + 0.001, "low": 1.0795, "close": tp1})
        assert len(conn.sl_updates) == 1
        assert abs(conn.sl_updates[0]["sl"] - 1.0800) < 1e-9

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
        tm.close_trade(tid, reason="manual")
        assert tid not in tm.open_trades
        assert len(conn.closes) == 1

    def test_open_pairs_list(self):
        conn = MockForexConnector()
        tm = TradeManager(conn, "forex")
        tm.open_trade(_signal(pair="EUR_USD"))
        tm.open_trade(_signal(pair="GBP_USD", entry=1.25, sl=1.248))
        assert "EUR_USD" in tm.open_pairs()
        assert "GBP_USD" in tm.open_pairs()
