"""
Connector tests for Oanda and Alpaca.

Structural tests (no credentials) always run.

Live tests:
  Oanda  — requires OANDA_API_KEY + OANDA_ACCOUNT_ID in .env
  Alpaca — requires ALPACA_PAPER_KEY + ALPACA_PAPER_SECRET in .env
           Candle tests use the public CryptoHistoricalDataClient
           (no credentials needed for market data).
"""
import os
import sys
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

_has_oanda   = bool(os.getenv("OANDA_API_KEY") and os.getenv("OANDA_ACCOUNT_ID"))
_has_alpaca  = bool(os.getenv("ALPACA_PAPER_KEY") and os.getenv("ALPACA_PAPER_SECRET"))

oanda_required  = pytest.mark.skipif(not _has_oanda,
    reason="Set OANDA_API_KEY and OANDA_ACCOUNT_ID in .env")
alpaca_required = pytest.mark.skipif(not _has_alpaca,
    reason="Set ALPACA_PAPER_KEY and ALPACA_PAPER_SECRET in .env")


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


# ═══════════════════════════════════════════════════════════════════════════════
# ALPACA
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlpacaConnectorStructural:
    def test_module_imports(self):
        from connectors.alpaca_connector import AlpacaConnector
        assert AlpacaConnector is not None

    def test_instantiation_fails_without_api_key(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "ALPACA_API_KEY", None)
        monkeypatch.setattr(cfg, "ALPACA_SECRET", None)
        from connectors.alpaca_connector import AlpacaConnector
        with pytest.raises(ValueError, match="ALPACA_PAPER_KEY"):
            AlpacaConnector()

    def test_instantiation_fails_without_secret(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "ALPACA_API_KEY", "fake-key")
        monkeypatch.setattr(cfg, "ALPACA_SECRET", None)
        from connectors.alpaca_connector import AlpacaConnector
        with pytest.raises(ValueError, match="ALPACA_PAPER_SECRET"):
            AlpacaConnector()

    def test_invalid_timeframe_raises(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "ALPACA_API_KEY", "fake")
        monkeypatch.setattr(cfg, "ALPACA_SECRET", "fake")
        # Patch TradingClient so we don't hit the network
        import alpaca.trading.client as tc
        monkeypatch.setattr(tc, "TradingClient", lambda **kw: object())
        from connectors.alpaca_connector import AlpacaConnector
        conn = AlpacaConnector.__new__(AlpacaConnector)
        conn._paper = True
        conn._trading = None
        from alpaca.data.historical import CryptoHistoricalDataClient
        conn._data = CryptoHistoricalDataClient()
        with pytest.raises(ValueError, match="Unknown timeframe"):
            conn.get_candles("BTC/USD", "BadTF", 5)


class TestAlpacaConnectorLive:
    @pytest.fixture(scope="class")
    def connector(self):
        from connectors.alpaca_connector import AlpacaConnector
        return AlpacaConnector()

    @alpaca_required
    def test_get_candles_returns_dataframe(self, connector):
        df = connector.get_candles("BTC/USD", "1Hour", 10)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 10

    @alpaca_required
    def test_candles_have_correct_columns(self, connector):
        df = connector.get_candles("BTC/USD", "1Hour", 10)
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}

    @alpaca_required
    def test_candles_index_is_datetime(self, connector):
        df = connector.get_candles("BTC/USD", "1Hour", 10)
        assert pd.api.types.is_datetime64_any_dtype(df.index)

    @alpaca_required
    def test_candles_index_is_tz_naive(self, connector):
        df = connector.get_candles("BTC/USD", "1Hour", 10)
        assert df.index.tz is None, "Index must be tz-naive for pandas-ta compatibility"

    @alpaca_required
    def test_candles_ohlcv_are_valid(self, connector):
        df = connector.get_candles("BTC/USD", "1Hour", 10)
        assert df["high"].ge(df["low"]).all()
        assert df["high"].ge(df["open"]).all()
        assert df["high"].ge(df["close"]).all()
        assert df["open"].gt(0).all()
        assert df["volume"].ge(0).all()

    @alpaca_required
    def test_candles_are_chronological(self, connector):
        df = connector.get_candles("BTC/USD", "1Hour", 10)
        assert df.index.is_monotonic_increasing

    @alpaca_required
    def test_candles_eth_usd(self, connector):
        df = connector.get_candles("ETH/USD", "1Hour", 5)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 5

    @alpaca_required
    def test_get_account_keys(self, connector):
        account = connector.get_account()
        for key in ("cash", "portfolio_value", "buying_power", "currency"):
            assert key in account

    @alpaca_required
    def test_account_buying_power_is_positive(self, connector):
        account = connector.get_account()
        assert account["buying_power"] > 0

    @alpaca_required
    def test_account_currency_is_usd(self, connector):
        account = connector.get_account()
        assert account["currency"] == "USD"
