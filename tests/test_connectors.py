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

