import logging
import pandas as pd

import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.trades as trades
import oandapyV20.endpoints.transactions as transactions
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
            # No timeout was set here before — a stalled connection (dropped
            # network, slow DNS, unresponsive server) would hang this request
            # indefinitely. Since main.py's scheduler is a BlockingScheduler
            # running jobs synchronously in the main thread, one hung request
            # here freezes every scheduled job (including all other pairs'
            # signal scans) until it resolves. 15s is generous for a candle
            # fetch; anything longer almost certainly means the connection is
            # dead, not just slow.
            request_params={"timeout": 15},
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

    # ── Real-time price stream ────────────────────────────────────────────────

    def stream_prices(self, instruments: list[str], callback) -> None:
        """
        Stream real-time bid/ask ticks from OANDA for the given instruments.

        Calls ``callback(instrument, bid, ask)`` for every PRICE message.
        HEARTBEAT messages are silently skipped.

        This method **blocks** — run it in a daemon thread.
        Raises on connection error so the caller can reconnect.

        instruments : list of OANDA format strings, e.g. ["EUR_USD", "GBP_USD"]
        callback    : callable(instrument: str, bid: float, ask: float)
        """
        params = {"instruments": ",".join(instruments), "snapshot": "True"}
        req = oanda_pricing.PricingStream(accountID=self.account_id, params=params)
        for msg in self.client.request(req):
            if msg.get("type") != "PRICE":
                continue
            instrument = msg.get("instrument", "")
            asks = msg.get("asks", [])
            bids = msg.get("bids", [])
            if not asks or not bids:
                continue
            try:
                ask = float(asks[0]["price"])
                bid = float(bids[0]["price"])
                callback(instrument, bid, ask)
            except (KeyError, ValueError, TypeError):
                continue

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

    # ── Price formatting helper ───────────────────────────────────────────────

    @staticmethod
    def _fmt(instrument: str, price: float) -> str:
        """
        Format a price for OANDA API submission.
        XAU_USD needs 2 decimal places (OANDA displayPrecision=2), JPY pairs
        need 3, all others need 5. Using wrong precision causes
        PRICE_PRECISION_EXCEEDED rejections.
        """
        if "XAU" in instrument:
            decimals = 2
        elif "JPY" in instrument:
            decimals = 3
        else:
            decimals = 5
        return f"{price:.{decimals}f}"

    # ── Open positions (for live reconciliation) ──────────────────────────────

    def get_open_trades(self) -> list:
        """
        Return all open trades from the broker as a list of dicts with keys:
            id, instrument, units, price (avg), unrealizedPL, stopLoss, takeProfit
        """
        import oandapyV20.endpoints.trades as _trades_ep
        req  = _trades_ep.OpenTrades(accountID=self.account_id)
        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.error("get_open_trades failed: %s", exc)
            return []

        result = []
        for t in resp.get("trades", []):
            sl = tp = None
            if t.get("stopLossOrder"):
                try:
                    sl = float(t["stopLossOrder"]["price"])
                except Exception:
                    pass
            if t.get("takeProfitOrder"):
                try:
                    tp = float(t["takeProfitOrder"]["price"])
                except Exception:
                    pass
            result.append({
                "id":          t["id"],
                "instrument":  t["instrument"],
                "units":       int(t["currentUnits"]),
                "price":       float(t["price"]),
                "unrealizedPL": float(t.get("unrealizedPL", 0)),
                "stopLoss":    sl,
                "takeProfit":  tp,
            })
        return result

    def get_trade_details(self, trade_id: str) -> dict:
        """
        Return the full lifecycle of one trade (open or closed) as a dict:
            state, open_time, close_time, avg_close_price, realized_pl,
            close_reason ("sl" | "tp" | "unknown")

        Used for reconciliation when a local open trade no longer appears
        in get_open_trades() — this fetches what actually happened to it
        (a real SL/TP fill fires off bid/ask on the broker's side, which
        can close a position before our own mid-price candle data ever
        shows the level being crossed).
        """
        req = trades.TradeDetails(accountID=self.account_id, tradeID=str(trade_id))
        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.error("get_trade_details failed — trade_id=%s: %s", trade_id, exc)
            raise

        t = resp.get("trade", {})
        close_reason = "unknown"
        if t.get("stopLossOrder", {}).get("state") == "FILLED":
            close_reason = "sl"
        elif t.get("takeProfitOrder", {}).get("state") == "FILLED":
            close_reason = "tp"

        return {
            "state":            t.get("state", ""),
            "open_time":        t.get("openTime"),
            "close_time":       t.get("closeTime"),
            "avg_close_price":  float(t["averageClosePrice"]) if t.get("averageClosePrice") else None,
            "realized_pl":      float(t.get("realizedPL", 0) or 0),
            "close_reason":     close_reason,
        }

    _CLOSE_REASON_MAP = {
        "STOP_LOSS_ORDER":          "sl",
        "TRAILING_STOP_LOSS_ORDER": "sl",
        "TAKE_PROFIT_ORDER":        "tp",
        "MARKET_ORDER_TRADE_CLOSE": "manual",
        "MARGIN_CLOSEOUT":          "margin_closeout",
    }

    def get_trade_close_from_transactions(self, trade_id: str) -> dict | None:
        """
        Fallback for get_trade_details() when OANDA's TradeDetails endpoint
        fails to return a closed trade — confirmed happening on this
        practice account (a trade closed for hours still 404s with
        NO_SUCH_TRADE). The transaction log doesn't have that gap: OANDA
        assigns the trade its ID from the opening ORDER_FILL transaction,
        so scanning transactions from that ID forward for the ORDER_FILL
        that lists this trade in tradesClosed gets the real exit price,
        realized PnL, and close reason straight from the ground truth,
        instead of reconcile_open_trades() guessing from a stale cached
        price.

        Returns None if no closing transaction is found (caller should
        keep its own last-resort fallback for that case).
        """
        req = transactions.TransactionsSinceID(
            accountID=self.account_id, params={"id": str(trade_id)},
        )
        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.error(
                "get_trade_close_from_transactions failed — trade_id=%s: %s",
                trade_id, exc,
            )
            return None

        for txn in resp.get("transactions", []):
            for closed in txn.get("tradesClosed", []):
                if str(closed.get("tradeID")) != str(trade_id):
                    continue
                return {
                    "exit_price":   float(txn.get("price") or txn.get("fullVWAP") or 0),
                    "realized_pl":  float(closed.get("realizedPL", 0) or 0),
                    "close_reason": self._CLOSE_REASON_MAP.get(txn.get("reason", ""), "unknown"),
                }
        return None

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
                    "price": self._fmt(instrument, sl_price),
                    "timeInForce": "GTC",
                },
                "timeInForce": "FOK",    # Fill-or-Kill — standard for market orders
                "positionFill": "DEFAULT",
            }
        }

        # Attach TP1 if provided
        if tp_prices:
            order_body["order"]["takeProfitOnFill"] = {
                "price": self._fmt(instrument, tp_prices[0]),
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

    def place_limit_order(
        self,
        instrument: str,
        units: int,
        limit_price: float,
        sl_price: float,
        tp_prices: list,
        expiry_minutes: int = None,
    ) -> str:
        """
        Place a limit order with a stop-loss and TP1 attached (mirrors
        place_market_order — same SL/TP1-only convention, tp2/tp3 managed
        by trade_manager on subsequent candles once filled).

        units          : positive = long, negative = short
        limit_price    : order triggers when market reaches this price
        expiry_minutes : GTD (good-till-date) window — OANDA cancels the
                         order itself if unfilled by then, so expiry is
                         enforced broker-side rather than depending on our
                         own process being alive to notice and cancel it.
                         Defaults to config.LIMIT_ORDER_EXPIRY_CANDLES ×
                         the primary timeframe's minutes.

        Returns the OANDA orderID (NOT a tradeID — the order hasn't filled
        yet; use get_pending_orders()/get_open_trades() to detect when it
        does).
        Raises V20Error on API failure or ValueError if the order was rejected.
        """
        if expiry_minutes is None:
            candles = getattr(config, "LIMIT_ORDER_EXPIRY_CANDLES", 8)
            expiry_minutes = candles * 30   # primary timeframe is M30

        from datetime import datetime, timezone, timedelta
        gtd_time = (datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)) \
            .strftime("%Y-%m-%dT%H:%M:%S.000000000Z")

        order_body: dict = {
            "order": {
                "type": "LIMIT",
                "instrument": instrument,
                "units": str(units),
                "price": self._fmt(instrument, limit_price),
                "timeInForce": "GTD",
                "gtdTime": gtd_time,
                "stopLossOnFill": {
                    "price": self._fmt(instrument, sl_price),
                    "timeInForce": "GTC",
                },
                "positionFill": "DEFAULT",
            }
        }
        if tp_prices:
            order_body["order"]["takeProfitOnFill"] = {
                "price": self._fmt(instrument, tp_prices[0]),
                "timeInForce": "GTC",
            }

        req = orders.OrderCreate(accountID=self.account_id, data=order_body)

        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.error(
                "place_limit_order failed — %s units=%s limit=%.5f: %s",
                instrument, units, limit_price, exc,
            )
            raise

        if "orderRejectTransaction" in resp:
            reason = resp["orderRejectTransaction"].get("rejectReason", "unknown")
            logger.error("Limit order rejected for %s: %s", instrument, reason)
            raise ValueError(f"Oanda rejected limit order for {instrument}: {reason}")

        create_txn = resp.get("orderCreateTransaction", {})
        order_id   = create_txn.get("id")
        if not order_id:
            ids = resp.get("relatedTransactionIDs", [])
            order_id = ids[-1] if ids else "unknown"
            logger.warning(
                "orderCreateTransaction not in response; guessing order_id=%s", order_id
            )

        direction = "BUY" if units > 0 else "SELL"
        logger.info(
            "%s LIMIT %s  units=%s  limit=%.5f  SL=%.5f  TP1=%s  expires=%s  → order_id=%s",
            direction, instrument, units, limit_price, sl_price,
            f"{tp_prices[0]:.5f}" if tp_prices else "none",
            gtd_time, order_id,
        )
        return str(order_id)

    def cancel_order(self, order_id: str) -> dict:
        """Cancel a pending (unfilled) order."""
        req = orders.OrderCancel(accountID=self.account_id, orderID=str(order_id))
        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.error("cancel_order failed — order_id=%s: %s", order_id, exc)
            raise
        logger.info("Order %s cancelled", order_id)
        return resp

    def get_pending_orders(self) -> list:
        """
        Return all pending (unfilled) orders from the broker as a list of
        dicts with keys: id, instrument, units, price, state.
        """
        req = orders.OrdersPending(accountID=self.account_id)
        try:
            resp = self.client.request(req)
        except V20Error as exc:
            logger.error("get_pending_orders failed: %s", exc)
            return []

        result = []
        for o in resp.get("orders", []):
            try:
                result.append({
                    "id":         o["id"],
                    "instrument": o.get("instrument", ""),
                    "units":      int(o.get("units", 0)),
                    "price":      float(o.get("price", 0)),
                    "state":      o.get("state", ""),
                })
            except (KeyError, ValueError):
                continue
        return result

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
        self, trade_id: str, sl_price: float,
        tp_price: float = None, instrument: str = "",
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
                "price": self._fmt(instrument, sl_price),
                "timeInForce": "GTC",
            }
        }
        if tp_price is not None:
            crcdo_data["takeProfit"] = {
                "price": self._fmt(instrument, tp_price),
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
