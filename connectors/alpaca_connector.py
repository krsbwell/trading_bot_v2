import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from alpaca.trading.client import TradingClient
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.requests import MarketOrderRequest, ClosePositionRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.common.exceptions import APIError

import config

logger = logging.getLogger(__name__)

# Maps spec timeframe strings → alpaca-py TimeFrame objects
_TIMEFRAME_MAP: dict[str, TimeFrame] = {
    "15Min": TimeFrame(15, TimeFrameUnit.Minute),
    "1Hour": TimeFrame(1,  TimeFrameUnit.Hour),
    "4Hour": TimeFrame(4,  TimeFrameUnit.Hour),
    "1Day":  TimeFrame(1,  TimeFrameUnit.Day),
}


class AlpacaConnector:
    """
    Wraps alpaca-py for crypto trading and historical data.
    Reads credentials and mode (paper/live) from config.py / .env.

    TradingClient  — orders, positions, account  (needs API keys)
    CryptoHistoricalDataClient — OHLCV bars      (public, no auth)
    """

    def __init__(self):
        if not config.ALPACA_API_KEY:
            raise ValueError(
                "ALPACA_PAPER_KEY is not set. "
                "Copy .env.example to .env and fill in your Alpaca paper credentials."
            )
        if not config.ALPACA_SECRET:
            raise ValueError(
                "ALPACA_PAPER_SECRET is not set. "
                "Copy .env.example to .env and fill in your Alpaca paper credentials."
            )

        self._paper = config.MODE == "paper"
        self._trading = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET,
            paper=self._paper,
        )
        # Authenticated client — without credentials Alpaca returns only ~12 recent bars
        self._data = CryptoHistoricalDataClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET,
        )

        logger.info(
            "AlpacaConnector ready — mode=%s",
            "paper" if self._paper else "live",
        )

    # ── Candles ───────────────────────────────────────────────────────────────

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """
        Fetch completed OHLCV bars.

        symbol    : Alpaca crypto format, e.g. "BTC/USD"
        timeframe : "15Min" | "1Hour" | "4Hour" | "1Day"
        limit     : number of bars to return

        Returns DataFrame indexed by UTC datetime (tz-naive) with columns:
            open, high, low, close, volume
        """
        tf = _TIMEFRAME_MAP.get(timeframe)
        if tf is None:
            raise ValueError(
                f"Unknown timeframe '{timeframe}'. "
                f"Valid: {list(_TIMEFRAME_MAP.keys())}"
            )

        # Hours per bar — used to calculate a start date far enough back.
        # Without an explicit start, Alpaca may return far fewer bars than `limit`.
        _hours = {"15Min": 0.25, "1Hour": 1.0, "4Hour": 4.0, "1Day": 24.0}
        lookback_hours = _hours.get(timeframe, 1.0) * limit * 2  # 2× buffer
        start = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

        request = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            limit=limit,
        )

        try:
            bars = self._data.get_crypto_bars(request)
        except APIError as exc:
            logger.error(
                "get_candles failed — symbol=%s timeframe=%s limit=%d: %s",
                symbol, timeframe, limit, exc,
            )
            raise

        df = bars.df

        if df.empty:
            logger.warning("No bars returned for %s %s", symbol, timeframe)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        # bars.df may carry a MultiIndex (symbol, timestamp)
        if isinstance(df.index, pd.MultiIndex):
            level0 = df.index.get_level_values(0).unique()
            key = symbol if symbol in level0 else (level0[0] if len(level0) else symbol)
            if key != symbol:
                logger.debug("Symbol key in response: %s (requested: %s)", key, symbol)
            df = df.loc[key].copy()

        # Keep only OHLCV — drop vwap, trade_count etc.
        df = df[["open", "high", "low", "close", "volume"]].copy()

        # Strip timezone — consistent with OandaConnector and pandas-ta compatibility
        if df.index.tz is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)
        df.index = pd.to_datetime(df.index)
        df.index.name = "time"

        logger.debug(
            "get_candles %s %s → %d rows  [%s … %s]",
            symbol, timeframe, len(df),
            df.index[0], df.index[-1],
        )
        return df

    def get_current_quote(self, symbol: str) -> dict:
        """
        Return current bid, ask, mid, and spread (in USD) for a crypto symbol.
        Falls back to zeros on error.
        """
        try:
            from alpaca.data.requests import CryptoLatestQuoteRequest
            req    = CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self._data.get_crypto_latest_quote(req)
            key    = symbol if symbol in quotes else (list(quotes.keys())[0] if quotes else None)
            if not key:
                return {"bid": 0.0, "ask": 0.0, "mid": 0.0, "spread_pips": 0.0}
            q   = quotes[key]
            ask = float(q.ask_price)
            bid = float(q.bid_price)
            return {
                "bid":         bid,
                "ask":         ask,
                "mid":         round((bid + ask) / 2, 4),
                "spread_pips": round(ask - bid, 4),   # raw $ spread for crypto
            }
        except Exception as exc:
            logger.debug("get_current_quote %s: %s", symbol, exc)
            return {"bid": 0.0, "ask": 0.0, "mid": 0.0, "spread_pips": 0.0}

    def get_live_candles(self, symbol: str, timeframe: str, count: int = 3) -> pd.DataFrame:
        """
        Return the last `count` bars including the current in-progress bar.
        Alpaca's historical endpoint already returns the latest partial bar,
        so this is a thin wrapper around get_candles.
        """
        return self.get_candles(symbol, timeframe, count)

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        sl_price: float,
        tp_prices: list,
    ) -> str:
        """
        Place a bracket market order with SL and TP1 attached.

        symbol    : "BTC/USD"
        qty       : fractional float, e.g. 0.00145
        side      : "long" or "short"
        sl_price  : stop-loss level
        tp_prices : [tp1, tp2, tp3] — only tp1 attached to broker order;
                    tp2/tp3 managed by trade_manager on subsequent candles

        Returns order_id string.
        Raises APIError on failure.
        """
        order_side = OrderSide.BUY if side == "long" else OrderSide.SELL
        tp1 = tp_prices[0] if tp_prices else None

        # Build price strings — Alpaca expects string prices in nested dicts
        stop_loss_args = {"stop_price": f"{sl_price:.4f}"}

        if tp1 is not None:
            order_request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.GTC,
                order_class=OrderClass.BRACKET,
                stop_loss=stop_loss_args,
                take_profit={"limit_price": f"{tp1:.4f}"},
            )
        else:
            # No TP provided — simple market order with stop-loss only
            order_request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.GTC,
                order_class=OrderClass.OTO,
                stop_loss=stop_loss_args,
            )

        try:
            order = self._trading.submit_order(order_request)
        except APIError as exc:
            logger.error(
                "place_market_order failed — %s %s qty=%s: %s",
                side.upper(), symbol, qty, exc,
            )
            raise

        logger.info(
            "%s %s  qty=%s  SL=%.4f  TP1=%s  → order_id=%s",
            side.upper(), symbol, qty, sl_price,
            f"{tp1:.4f}" if tp1 else "none",
            order.id,
        )
        return str(order.id)

    # ── Position management ───────────────────────────────────────────────────

    def close_position(self, symbol: str, qty: float = None) -> dict:
        """
        Close a crypto position fully or partially.

        symbol : "BTC/USD"
        qty    : fractional float to close partially, None = full close

        Returns confirmation dict.
        """
        try:
            if qty is not None:
                result = self._trading.close_position(
                    symbol_or_asset_id=symbol,
                    close_options=ClosePositionRequest(qty=str(qty)),
                )
            else:
                result = self._trading.close_position(symbol_or_asset_id=symbol)
        except APIError as exc:
            logger.error(
                "close_position failed — %s qty=%s: %s", symbol, qty, exc
            )
            raise

        logger.info("Position closed — %s qty=%s", symbol, qty if qty else "all")
        return result.model_dump() if hasattr(result, "model_dump") else {"status": "closed"}

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        """
        Returns:
            cash            : float — settled cash
            portfolio_value : float — total portfolio value including open positions
            buying_power    : float — available buying power
            currency        : str   — "USD"
        """
        try:
            account = self._trading.get_account()
        except APIError as exc:
            logger.error("get_account failed: %s", exc)
            raise

        summary = {
            "cash":            float(account.cash),
            "portfolio_value": float(account.portfolio_value),
            "buying_power":    float(account.buying_power),
            "currency":        str(account.currency),
        }
        logger.debug("Account: %s", summary)
        return summary
