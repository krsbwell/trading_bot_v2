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

