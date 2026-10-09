"""
Connector tests for Oanda.

Structural tests (no credentials) always run.

Live tests:
  Oanda — requires OANDA_API_KEY + OANDA_ACCOUNT_ID in .env
"""
import os
import sys
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_has_oanda = bool(os.getenv("OANDA_API_KEY") and os.getenv("OANDA_ACCOUNT_ID"))

oanda_required = pytest.mark.skipif(not _has_oanda,
    reason="Set OANDA_API_KEY and OANDA_ACCOUNT_ID in .env")


# ═══════════════════════════════════════════════════════════════════════════════
# OANDA
# ═══════════════════════════════════════════════════════════════════════════════

class TestOandaConnectorStructural:
    def test_module_imports(self):
        from connectors.oanda_connector import OandaConnector
        assert OandaConnector is not None

    def test_instantiation_fails_without_credentials(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "OANDA_API_KEY", None)
        monkeypatch.setattr(cfg, "OANDA_ACCOUNT_ID", None)
        from connectors.oanda_connector import OandaConnector
        with pytest.raises(ValueError, match="OANDA_API_KEY"):
            OandaConnector()

    def test_instantiation_fails_without_account_id(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "OANDA_API_KEY", "fake-key")
        monkeypatch.setattr(cfg, "OANDA_ACCOUNT_ID", None)
        from connectors.oanda_connector import OandaConnector
        with pytest.raises(ValueError, match="OANDA_ACCOUNT_ID"):
            OandaConnector()

    def test_client_has_request_timeout(self, monkeypatch):
        """
        A stalled HTTP call must not hang forever — main.py's scheduler runs
        jobs synchronously (BlockingScheduler), so one hung request would
        freeze every scheduled job, not just the current one. See
        bugs_scheduler_reliability memory: this was the likely cause of a
        multi-hour silent outage before request_params={"timeout": ...} was
        added. Regression test — fails if the timeout is ever removed.
        """
        import config as cfg
        monkeypatch.setattr(cfg, "OANDA_API_KEY", "fake-key")
        monkeypatch.setattr(cfg, "OANDA_ACCOUNT_ID", "fake-account")
        from connectors.oanda_connector import OandaConnector
        conn = OandaConnector()
        assert conn.client.request_params.get("timeout") is not None
        assert conn.client.request_params["timeout"] <= 30, (
            "timeout should be short enough that a stalled connection can't "
            "block the scheduler for an unreasonable amount of time"
        )


class TestOandaConnectorTransactionFallback:
    """
    get_trade_close_from_transactions() — the fallback reconcile_open_trades()
    uses when OANDA's TradeDetails endpoint 404s NO_SUCH_TRADE on a trade
    that's actually closed (confirmed live 2026-08-12: trade #77, closed
    hours earlier by a normal SL fill, still 404'd on TradeDetails when
    queried directly). Payload shape below is the real TransactionsSinceID
    response for that trade, trimmed to the fields the parser reads.
    """
    @pytest.fixture
    def connector(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "OANDA_API_KEY", "fake-key")
        monkeypatch.setattr(cfg, "OANDA_ACCOUNT_ID", "fake-account")
        from connectors.oanda_connector import OandaConnector
        return OandaConnector()

    def test_finds_the_real_sl_close_among_other_transactions(self, connector, monkeypatch):
        # Mirrors the real account: unrelated transactions before/after the
        # one that actually closed trade 77, plus the closing fill itself.
        monkeypatch.setattr(connector.client, "request", lambda req: {
            "transactions": [
                {"id": "78", "type": "STOP_LOSS_ORDER", "tradeID": "77"},
                {"id": "99", "type": "ORDER_FILL", "instrument": "EUR_JPY",
                 "reason": "STOP_LOSS_ORDER", "price": "182.129",
                 "tradesClosed": [{"tradeID": "73", "realizedPL": "-5.7753"}]},
                {"id": "101", "type": "ORDER_FILL", "instrument": "NZD_USD",
                 "reason": "STOP_LOSS_ORDER", "price": "0.58583",
                 "tradesClosed": [{"tradeID": "77", "realizedPL": "-5.0224"}]},
            ]
        })
        result = connector.get_trade_close_from_transactions("77")
        assert result == {
            "exit_price": 0.58583, "realized_pl": -5.0224, "close_reason": "sl",
        }

    def test_take_profit_close_maps_to_tp(self, connector, monkeypatch):
        monkeypatch.setattr(connector.client, "request", lambda req: {
            "transactions": [
                {"id": "50", "type": "ORDER_FILL", "reason": "TAKE_PROFIT_ORDER",
                 "price": "1.0900",
                 "tradesClosed": [{"tradeID": "42", "realizedPL": "12.50"}]},
            ]
        })
        result = connector.get_trade_close_from_transactions("42")
        assert result["close_reason"] == "tp"
        assert result["realized_pl"] == 12.50

    def test_no_matching_transaction_returns_none(self, connector, monkeypatch):
        monkeypatch.setattr(connector.client, "request", lambda req: {
            "transactions": [
                {"id": "50", "type": "ORDER_FILL", "reason": "STOP_LOSS_ORDER",
                 "price": "1.0900", "tradesClosed": [{"tradeID": "99", "realizedPL": "1.0"}]},
            ]
        })
        assert connector.get_trade_close_from_transactions("42") is None

    def test_request_failure_returns_none_not_raises(self, connector, monkeypatch):
        from oandapyV20.exceptions import V20Error
        def _boom(req):
            raise V20Error(500, "server error")
        monkeypatch.setattr(connector.client, "request", _boom)
        assert connector.get_trade_close_from_transactions("77") is None


class TestOandaConnectorPlaceMarketOrder:
    """
    place_market_order() — fixed 2026-10-08 after a live incident: at
    28-pair scale, FOK market orders started getting cancelled by OANDA
    for INSUFFICIENT_MARGIN. The code only checked for
    orderRejectTransaction, not orderCancelTransaction, so a cancellation
    fell through to a fallback that guessed a trade_id from
    relatedTransactionIDs[-1] — which is the CANCEL transaction's own ID,
    not a trade ID. That phantom "trade" then got stored in
    TradeManager.open_trades as if real (see
    bugs_phantom_trade_from_order_cancel memory for the full incident).

    Response handling order is deliberate and tested explicitly below: a
    successful fill is checked FIRST, before reject or cancel, so it can
    never be shadowed even if a response hypothetically carried more than
    one of these keys at once.
    """
    @pytest.fixture
    def connector(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "OANDA_API_KEY", "fake-key")
        monkeypatch.setattr(cfg, "OANDA_ACCOUNT_ID", "fake-account")
        from connectors.oanda_connector import OandaConnector
        return OandaConnector()

    def test_successful_fill_returns_real_trade_id(self, connector, monkeypatch):
        monkeypatch.setattr(connector.client, "request", lambda req: {
            "orderFillTransaction": {"tradeOpened": {"tradeID": "336"}},
            "relatedTransactionIDs": ["335", "336"],
        })
        trade_id = connector.place_market_order("EUR_USD", 1000, 1.0800, [1.0850])
        assert trade_id == "336"

    def test_order_cancelled_insufficient_margin_raises_not_phantom_id(self, connector, monkeypatch):
        """The exact live incident: a cancelled FOK order must raise, never
        return a trade_id — the OANDA transaction ID of the cancellation
        itself (334 in the real case) must never be mistaken for a trade."""
        monkeypatch.setattr(connector.client, "request", lambda req: {
            "orderCancelTransaction": {"id": "334", "reason": "INSUFFICIENT_MARGIN"},
            "relatedTransactionIDs": ["333", "334"],
        })
        with pytest.raises(ValueError, match="INSUFFICIENT_MARGIN"):
            connector.place_market_order("EUR_AUD", 2497, 1.6100, [1.6050])

    def test_order_rejected_still_raises_as_before(self, connector, monkeypatch):
        """Regression — the pre-existing reject path must keep working
        unchanged."""
        monkeypatch.setattr(connector.client, "request", lambda req: {
            "orderRejectTransaction": {"rejectReason": "MARKET_HALTED"},
        })
        with pytest.raises(ValueError, match="MARKET_HALTED"):
            connector.place_market_order("EUR_USD", 1000, 1.0800, [1.0850])

    def test_fill_takes_priority_over_reject_and_cancel_if_both_present(self, connector, monkeypatch):
        """The ordering safety requirement: if a response ever carried BOTH
        a real fill AND a reject/cancel-shaped key (shouldn't normally
        happen, but the check order must not assume it can't), the fill
        must win — a real opened trade can never be shadowed into a raise
        by an unrelated/stale key also being present."""
        monkeypatch.setattr(connector.client, "request", lambda req: {
            "orderFillTransaction": {"tradeOpened": {"tradeID": "500"}},
            "orderCancelTransaction": {"id": "499", "reason": "LINKED_TRADE_CLOSED"},
            "orderRejectTransaction": {"rejectReason": "STALE_DATA"},
        })
        trade_id = connector.place_market_order("GBP_USD", 1000, 1.3000, [1.3050])
        assert trade_id == "500"

    def test_unrecognized_response_shape_raises_and_does_not_guess(self, connector, monkeypatch):
        """No fill, no reject, no cancel — this is the dangerous fallback
        the incident came from (trade_id = ids[-1] if ids else "unknown").
        Must now raise rather than manufacture a trade_id, even though
        relatedTransactionIDs is populated and would have supplied one
        under the old code."""
        monkeypatch.setattr(connector.client, "request", lambda req: {
            "relatedTransactionIDs": ["900", "901"],
        })
        with pytest.raises(RuntimeError, match="no known outcome"):
            connector.place_market_order("USD_JPY", 1000, 150.00, [150.50])

    def test_unrecognized_response_with_no_related_ids_also_raises(self, connector, monkeypatch):
        """Even the emptiest possible response (what the old code would
        have returned trade_id="unknown" for) must raise, not succeed with
        a placeholder ID."""
        monkeypatch.setattr(connector.client, "request", lambda req: {})
        with pytest.raises(RuntimeError, match="no known outcome"):
            connector.place_market_order("USD_JPY", 1000, 150.00, [150.50])


class TestOandaConnectorPagination:
    """2026-08-17 — get_candles() transparently pages past OANDA's 4999-
    candle single-request cap. This silently limited every M15/M30 backtest
    in this project's history to a short calendar window (~74-146 days) even
    when the underlying strategy fired often — the real reason M15/M30
    samples kept coming back "too thin to trust," not strategy rarity. See
    tasks/todo.md 2026-08-17. Mocked here (not @oanda_required) so this is
    fast and deterministic; TestOandaConnectorLive's real-API tests cover
    the single-chunk path already."""

    @pytest.fixture
    def connector(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "OANDA_API_KEY", "fake-key")
        monkeypatch.setattr(cfg, "OANDA_ACCOUNT_ID", "fake-account")
        from connectors.oanda_connector import OandaConnector
        return OandaConnector()

    @staticmethod
    def _make_candles(n, step_minutes=15):
        """n completed candles, newest-last, spaced step_minutes apart,
        ending one step before "now"."""
        import pandas as pd
        times = pd.date_range(
            end=pd.Timestamp.now('UTC').tz_localize(None) - pd.Timedelta(minutes=step_minutes),
            periods=n, freq=f"{step_minutes}min",
        )
        return [
            {"time": t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"), "complete": True,
             "mid": {"o": "1.0", "h": "1.1", "l": "0.9", "c": "1.05"}, "volume": 100}
            for t in times
        ]

    def test_single_chunk_request_unchanged(self, connector, monkeypatch):
        """count <= 4999 must still be exactly one request (no behavior
        change / no wasted extra round-trip for the common case)."""
        calls = []

        def _request(req):
            calls.append(req)
            return {"candles": self._make_candles(101)}

        monkeypatch.setattr(connector.client, "request", _request)
        df = connector.get_candles("EUR_USD", "M15", 100)
        assert len(calls) == 1
        assert len(df) == 100

    def test_multi_chunk_request_pages_and_stitches_in_order(self, connector, monkeypatch):
        """count > 4999 must issue multiple requests and return them
        stitched into one chronological, gap-free (call-count-wise) frame."""
        import pandas as pd
        total_needed = 11000  # at the real 4999-per-request cap: 4999 + 4999 + 1002 = 3 chunks

        calls = []

        def _request(req):
            calls.append(req)
            to_param = req.params.get("to")
            count_requested = req.params["count"] - 1
            if to_param is None:
                end = pd.Timestamp.now('UTC').tz_localize(None) - pd.Timedelta(minutes=15)
            else:
                end = pd.to_datetime(to_param.replace("Z", ""))
            times = pd.date_range(end=end, periods=count_requested, freq="15min")
            return {"candles": [
                {"time": t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"), "complete": True,
                 "mid": {"o": "1.0", "h": "1.1", "l": "0.9", "c": "1.05"}, "volume": 100}
                for t in times
            ]}

        monkeypatch.setattr(connector.client, "request", _request)
        df = connector.get_candles("EUR_USD", "M15", total_needed)

        assert len(calls) == 3, "11000 candles at a 4999 cap should take exactly 3 requests"
        assert len(df) == total_needed
        assert df.index.is_monotonic_increasing
        assert not df.index.duplicated().any()

    def test_stops_cleanly_when_history_is_exhausted(self, connector, monkeypatch):
        """A pair with less history than requested (e.g. a newly-listed
        instrument) must return whatever's actually available instead of
        looping forever or raising. A short chunk (fewer candles than asked
        for) is itself the signal that history ran out — no wasted extra
        request needed to confirm it."""
        call_count = {"n": 0}

        def _request(req):
            call_count["n"] += 1
            return {"candles": self._make_candles(500)}  # always short of the 4999 asked for

        monkeypatch.setattr(connector.client, "request", _request)
        df = connector.get_candles("EUR_USD", "M15", 10000)
        assert len(df) == 500
        assert call_count["n"] == 1

    def test_empty_chunk_mid_pagination_stops_without_error(self, connector, monkeypatch):
        """A full-sized first chunk followed by a genuinely empty second
        chunk (history ran out right at a page boundary) must also stop
        cleanly rather than raising or looping."""
        call_count = {"n": 0}

        def _request(req):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"candles": self._make_candles(4999)}
            return {"candles": []}

        monkeypatch.setattr(connector.client, "request", _request)
        df = connector.get_candles("EUR_USD", "M15", 10000)
        assert len(df) == 4999
        assert call_count["n"] == 2


class TestOandaConnectorLive:
    @pytest.fixture(scope="class")
    def connector(self):
        from connectors.oanda_connector import OandaConnector
        return OandaConnector()

    @oanda_required
    def test_get_candles_returns_dataframe(self, connector):
        df = connector.get_candles("EUR_USD", "H1", 10)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 10

    @oanda_required
    def test_candles_have_correct_columns(self, connector):
        df = connector.get_candles("EUR_USD", "H1", 10)
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}

    @oanda_required
    def test_candles_index_is_datetime(self, connector):
        df = connector.get_candles("EUR_USD", "H1", 10)
        assert pd.api.types.is_datetime64_any_dtype(df.index)

    @oanda_required
    def test_candles_ohlcv_are_valid(self, connector):
        df = connector.get_candles("EUR_USD", "H1", 10)
        assert df["high"].ge(df["low"]).all()
        assert df["high"].ge(df["open"]).all()
        assert df["high"].ge(df["close"]).all()
        assert df["open"].gt(0).all()
        assert df["volume"].ge(0).all()

    @oanda_required
    def test_candles_are_chronological(self, connector):
        df = connector.get_candles("EUR_USD", "H1", 10)
        assert df.index.is_monotonic_increasing

    @oanda_required
    def test_get_account_summary_keys(self, connector):
        summary = connector.get_account_summary()
        for key in ("balance", "nav", "unrealized_pnl", "currency", "open_trade_count"):
            assert key in summary

    @oanda_required
    def test_account_balance_is_positive(self, connector):
        assert connector.get_account_summary()["balance"] > 0

    @oanda_required
    def test_account_currency_is_string(self, connector):
        currency = connector.get_account_summary()["currency"]
        assert isinstance(currency, str) and len(currency) == 3

