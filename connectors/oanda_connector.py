import logging
import pandas as pd

import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.trades as trades
import oandapyV20.endpoints.accounts as accounts
import oandapyV20.endpoints.pricing as oanda_pricing
from oandapyV20.exceptions import V20Error

import config

logger = logging.getLogger(__name__)


class OandaConnector:
    """
    Wraps oandapyV20 for all broker interactions.
    Reads credentials and environment (practice/live) from config.py / .env.
    """

    def __init__(self):
        if not config.OANDA_API_KEY:
            raise ValueError(
                "OANDA_API_KEY is not set. "
                "Copy .env.example to .env and fill in your practice credentials."
            )
        if not config.OANDA_ACCOUNT_ID:
            raise ValueError(
                "OANDA_ACCOUNT_ID is not set. "
                "Copy .env.example to .env and fill in your practice account ID."
            )

        self.account_id = config.OANDA_ACCOUNT_ID
        self.client = oandapyV20.API(
            access_token=config.OANDA_API_KEY,
            environment=config.OANDA_ENV,   # "practice" or "live"
        )
        logger.info(
            "OandaConnector ready — env=%s  account=%s",
            config.OANDA_ENV, self.account_id,
        )

    # ── Live quote (bid / ask / spread) ──────────────────────────────────────

    def get_current_quote(self, instrument: str) -> dict:
        """
        Return current bid, ask, mid, and spread (in pips) for one instrument.
        Falls back to {bid:0, ask:0, mid:0, spread_pips:0} on any error.
        """
        try:
            req  = oanda_pricing.PricingInfo(
                accountID=self.account_id,
                params={"instruments": instrument},
            )
            resp = self.client.request(req)
            p    = resp["prices"][0]
            ask  = float(p["asks"][0]["price"])
            bid  = float(p["bids"][0]["price"])
            pip  = 0.01 if "JPY" in instrument else 0.0001
            return {
                "bid":         bid,
                "ask":         ask,
                "mid":         round((bid + ask) / 2, 5),
                "spread_pips": round((ask - bid) / pip, 1),
            }
        except Exception as exc:
            logger.debug("get_current_quote %s: %s", instrument, exc)
            return {"bid": 0.0, "ask": 0.0, "mid": 0.0, "spread_pips": 0.0}

    # ── Candles ───────────────────────────────────────────────────────────────

    def get_candles(self, instrument: str, granularity: str, count: int) -> pd.DataFrame:
        """
        Fetch completed OHLCV candles.

        instrument  : Oanda format, e.g. "EUR_USD"
        granularity : "H1" | "H4" | "D"  (also accepts "M15" etc.)
        count       : number of completed candles to return

        Returns DataFrame indexed by UTC datetime (tz-naive) with columns:
            open, high, low, close, volume
        """
        # Request slightly more than needed to guarantee `count` completed candles
        params = {
            "granularity": granularity,
            "count": count + 1,   # +1 because the current in-progress candle is included
            "price": "M",         # midpoint (bid/ask average)
        }
        req = instruments.InstrumentsCandles(instrument=instrument, params=params)

        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.error(
                "get_candles failed — instrument=%s granularity=%s count=%d: %s",
                instrument, granularity, count, exc,
            )
            raise

        raw = resp.get("candles", [])
        completed = [c for c in raw if c.get("complete", False)]

        if not completed:
            logger.warning(
                "No completed candles returned for %s %s", instrument, granularity
            )
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        # Take the most-recent `count` completed candles
        completed = completed[-count:]

        rows = [
            {
                "time":   pd.to_datetime(c["time"]),
                "open":   float(c["mid"]["o"]),
                "high":   float(c["mid"]["h"]),
                "low":    float(c["mid"]["l"]),
                "close":  float(c["mid"]["c"]),
                "volume": int(c["volume"]),
            }
            for c in completed
        ]

        df = pd.DataFrame(rows).set_index("time")
        df.index = df.index.tz_localize(None)  # strip tz — pandas-ta compatibility

        logger.debug(
            "get_candles %s %s → %d rows  [%s … %s]",
            instrument, granularity, len(df),
            df.index[0], df.index[-1],
        )
        return df

    def get_live_candles(self, instrument: str, granularity: str, count: int = 3) -> pd.DataFrame:
        """
        Fetch the last `count` candles **including the current in-progress bar**,
        giving a live price on every 60-second dashboard refresh.
        Unlike get_candles(), the final row may be incomplete (partial candle).
        """
        params = {"granularity": granularity, "count": count, "price": "M"}
        req = instruments.InstrumentsCandles(instrument=instrument, params=params)

        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.warning(
                "get_live_candles failed — %s %s: %s", instrument, granularity, exc
            )
            raise

        raw = resp.get("candles", [])
        if not raw:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        rows = [
            {
                "time":   pd.to_datetime(c["time"]),
                "open":   float(c["mid"]["o"]),
                "high":   float(c["mid"]["h"]),
                "low":    float(c["mid"]["l"]),
                "close":  float(c["mid"]["c"]),
                "volume": int(c["volume"]),
            }
            for c in raw
        ]

        df = pd.DataFrame(rows).set_index("time")
        df.index = df.index.tz_localize(None)
        return df

    def get_spread(self, instrument: str) -> tuple:
        """
        Fetch live bid/ask for instrument.
        Returns (bid, ask, spread_in_price) as floats.
        """
        r = oanda_pricing.PricingInfo(
            accountID=self.account_id,
            params={"instruments": instrument},
        )
        try:
            resp = self.client.request(r)
        except V20Error as exc:
            logger.warning("get_spread failed — %s: %s", instrument, exc)
            raise
        prices = resp.get("prices", [])
        if not prices:
            raise ValueError(f"No pricing data for {instrument}")
        p   = prices[0]
        bid = float(p["bids"][0]["price"])
        ask = float(p["asks"][0]["price"])
        return bid, ask, ask - bid

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_market_order(
        self,
        instrument: str,
        units: int,
        sl_price: float,
        tp_prices: list,
    ) -> str:
        """
        Place a market order with a stop-loss and TP1 attached.

        units      : positive = long, negative = short
        sl_price   : stop-loss level
        tp_prices  : [tp1, tp2, tp3] — only tp1 is attached to the broker order;
                     tp2/tp3 are managed by trade_manager on subsequent candles

        Returns trade_id string.
        Raises V20Error on API failure or ValueError if order was rejected.
        """
        order_body: dict = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "stopLossOnFill": {
                    "price": f"{sl_price:.5f}",
                    "timeInForce": "GTC",
                },
                "timeInForce": "FOK",    # Fill-or-Kill — standard for market orders
                "positionFill": "DEFAULT",
            }
        }

        # Attach TP1 if provided
        if tp_prices:
            order_body["order"]["takeProfitOnFill"] = {
                "price": f"{tp_prices[0]:.5f}",
                "timeInForce": "GTC",
            }

        req = orders.OrderCreate(accountID=self.account_id, data=order_body)

        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.error(
                "place_market_order failed — %s units=%s: %s",
                instrument, units, exc,
            )
            raise

        # Market order rejected (e.g. market closed, insufficient margin)
        if "orderRejectTransaction" in resp:
            reason = resp["orderRejectTransaction"].get("rejectReason", "unknown")
            logger.error("Order rejected for %s: %s", instrument, reason)
            raise ValueError(f"Oanda rejected order for {instrument}: {reason}")

        fill = resp.get("orderFillTransaction", {})
        trade_opened = fill.get("tradeOpened", {})
        trade_id = trade_opened.get("tradeID")

        if not trade_id:
            # Fall back to relatedTransactionIDs if tradeOpened not present
            ids = resp.get("relatedTransactionIDs", [])
            trade_id = ids[-1] if ids else "unknown"
            logger.warning(
                "tradeOpened not in fill response; guessing trade_id=%s", trade_id
            )

        direction = "BUY" if units > 0 else "SELL"
        logger.info(
            "%s %s  units=%s  SL=%.5f  TP1=%s  → trade_id=%s",
            direction, instrument, units, sl_price,
            f"{tp_prices[0]:.5f}" if tp_prices else "none",
            trade_id,
        )
        return str(trade_id)

    # ── Trade management ──────────────────────────────────────────────────────

    def close_trade(self, trade_id: str, units: int = None) -> dict:
        """
        Close a trade fully or partially.

        units=None : full close
        units=N    : close exactly N units (absolute value used)

        Returns the trade close transaction dict.
        """
        data = {}
        if units is not None:
            data = {"unitsToClose": str(abs(units))}

        req = trades.TradeClose(
            accountID=self.account_id,
            tradeID=str(trade_id),
            data=data if data else None,
        )

        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.error("close_trade failed — trade_id=%s: %s", trade_id, exc)
            raise

        logger.info(
            "Trade %s closed — units=%s", trade_id, units if units is not None else "all"
        )
        return resp

    def set_sl_tp(
        self, trade_id: str, sl_price: float, tp_price: float = None
    ) -> dict:
        """
        Update the stop-loss (and optionally take-profit) on an open trade.

        Called by trade_manager to:
        - Move SL to breakeven after TP1 is hit
        - Set TP2 as new take-profit after TP1 partial close
        - Set TP3 as new take-profit after TP2 partial close
        """
        crcdo_data: dict = {
            "stopLoss": {
                "price": f"{sl_price:.5f}",
                "timeInForce": "GTC",
            }
        }
        if tp_price is not None:
            crcdo_data["takeProfit"] = {
                "price": f"{tp_price:.5f}",
                "timeInForce": "GTC",
            }

        req = trades.TradeCRCDO(
            accountID=self.account_id,
            tradeID=str(trade_id),
            data=crcdo_data,
        )

        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.error(
                "set_sl_tp failed — trade_id=%s SL=%.5f TP=%s: %s",
                trade_id, sl_price,
                f"{tp_price:.5f}" if tp_price else "none",
                exc,
            )
            raise

        logger.info(
            "SL/TP updated — trade_id=%s  SL=%.5f  TP=%s",
            trade_id, sl_price,
            f"{tp_price:.5f}" if tp_price else "unchanged",
        )
        return resp

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account_summary(self) -> dict:
        """
        Returns:
            balance         : float — cash balance (realised P&L included)
            nav             : float — net asset value (balance + unrealised P&L)
            unrealized_pnl  : float — sum of unrealised P&L across all open trades
            currency        : str   — account currency (e.g. "USD")
            open_trade_count: int
        """
        req = accounts.AccountSummary(accountID=self.account_id)

        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.error("get_account_summary failed: %s", exc)
            raise

        acct = resp.get("account", {})
        summary = {
            "balance":          float(acct.get("balance", 0)),
            "nav":              float(acct.get("NAV", 0)),
            "unrealized_pnl":   float(acct.get("unrealizedPL", 0)),
            "currency":         acct.get("currency", ""),
            "open_trade_count": int(acct.get("openTradeCount", 0)),
        }
        logger.debug("Account summary: %s", summary)
        return summary
