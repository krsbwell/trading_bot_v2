TRADING_HALTED = False
_daily_loss = 0.0
_session_open_balance = 0.0

# Approximate USD value of 1 unit of each quote currency — used as a
# backtesting fallback (and a safety net if a live quote lookup fails)
# where point-in-time FX-rate accuracy doesn't matter, only getting
# position size roughly the right scale. Not refreshed automatically;
# revisit if a currency moves a lot from these levels.
_QUOTE_CCY_APPROX_USD = {
    "JPY": 1 / 150.0,
    "CAD": 1 / 1.37,
    "CHF": 1 / 0.88,
    "AUD": 0.66,
    "NZD": 0.60,
    "GBP": 1.27,
    "EUR": 1.08,
}

# Quote currencies that OANDA quotes as "<CCY> per 1 USD" (need inverting
# to get USD per 1 unit of the currency) vs "USD per 1 <CCY>" (already
# the rate we want, no inversion).
_INVERTED_USD_CROSSES = {"JPY", "CAD", "CHF"}


def quote_currency(pair: str) -> str:
    """Quote (right-hand) currency of a pair like 'EUR_JPY' -> 'JPY'."""
    parts = pair.upper().split("_")
    return parts[1] if len(parts) == 2 else "USD"


def get_quote_to_usd_rate(pair: str, connector=None) -> float:
    """
    USD value of 1 unit of `pair`'s quote currency — the conversion
    calculate_position_size() needs so a pair not quoted directly in USD
    still risks the intended dollar amount, not that many units of
    whatever currency it happens to be quoted in.

    1.0 for pairs already quoted in USD (GBP_USD, NZD_USD, ...) — no
    lookup, no behavior change. For everything else: a live OANDA quote
    when `connector` is given (real trading), otherwise the approximate
    fixed table above (backtesting doesn't need point-in-time accuracy,
    just roughly-correct scale).

    Found 2026-08-04: calculate_position_size() previously assumed quote
    currency == USD unconditionally. Confirmed against OANDA's own
    realized_pl on two live EUR_JPY/CHF_JPY trades that this undersized
    JPY-quoted pairs by ~150x relative to the intended 1% risk — a $5
    target risk was actually only ~$0.03-0.04 of real exposure.
    """
    ccy = quote_currency(pair)
    if ccy == "USD":
        return 1.0

    if connector is not None:
        try:
            instrument = f"USD_{ccy}" if ccy in _INVERTED_USD_CROSSES else f"{ccy}_USD"
            mid = connector.get_current_quote(instrument).get("mid", 0)
            if mid:
                return (1.0 / mid) if ccy in _INVERTED_USD_CROSSES else mid
        except Exception:
            pass   # fall through to the approximate constant below

    return _QUOTE_CCY_APPROX_USD.get(ccy, 1.0)


def calculate_position_size(account_balance: float, entry: float,
                             stop_loss: float, pair: str = "",
                             quote_to_usd: float = None) -> int:
    """
    Returns position size (units) enforcing 1% risk hard rule.

    quote_to_usd: USD value of 1 unit of `pair`'s quote currency (see
    get_quote_to_usd_rate). Defaults to looking it up via the approximate
    table when not passed explicitly — callers with a live connector
    should pass get_quote_to_usd_rate(pair, connector) instead for a real
    quote.
    """
    if quote_to_usd is None:
        quote_to_usd = get_quote_to_usd_rate(pair)

    risk_amount = account_balance * 0.01
    sl_distance = abs(entry - stop_loss)
    if sl_distance <= 0 or quote_to_usd <= 0:
        return 0

    units = risk_amount / (sl_distance * quote_to_usd)
    return int(units)


def get_tp_levels(entry: float, stop_loss: float, direction: str, pair: str = "") -> dict:
    """
    Return TP1/TP2/TP3, default 1.5R / 2.5R / 3.5R, overridable per pair via
    config.TP_RR_PER_PAIR.

    Global default changed 2026-07-22 from 1.0R/2.5R/4.0R after backtesting
    both across the 5 active pairs (USD_CAD, GBP_CAD, NZD_USD, EUR_AUD,
    GBP_USD) on 3500 bars each: 4 of 5 pairs improved on PnL (GBP_USD and
    GBP_CAD notably so, PF 2.26->2.56 and PnL +11% respectively), only
    EUR_AUD's profit factor declined (2.09->1.83, PnL roughly flat). Max
    drawdown ticked up slightly on every pair (a wider TP1 means more
    open-risk time before the first partial close), but not by enough to
    offset the PnL gains. See tasks/todo.md's 2026-07-22 entries for the
    full comparison table.

    EUR_AUD given its own override the same day (1.0R/3.0R/4.5R) after a
    7-way sweep found it beats both the old and new global defaults on every
    metric for that pair specifically (PF 2.63 vs 1.83, PnL +28%, MaxDD down
    to 6.2%) — TP1 at 1.5R was the specific problem for this pair.
    """
    from config import TP_RR_PER_PAIR

    r1, r2, r3 = TP_RR_PER_PAIR.get(pair, (1.5, 2.5, 3.5))
    dist = abs(entry - stop_loss)
    mult = 1 if direction == "long" else -1
    return {
        "tp1": round(entry + mult * dist * r1, 5),   # close 40%
        "tp2": round(entry + mult * dist * r2, 5),   # close 35%
        "tp3": round(entry + mult * dist * r3, 5),   # close 25%
    }


def validate_pre_trade(score: int, open_trade_count: int,
                        pair: str, open_pairs: list) -> tuple[bool, str]:
    """Return (ok, reason). Checks score, open count, halt flag, duplicate pair."""
    from config import MIN_CONFLUENCE_SCORE, MAX_OPEN_TRADES

    if TRADING_HALTED:
        return False, "TRADING_HALTED: daily drawdown breached"
    if score < MIN_CONFLUENCE_SCORE:
        return False, f"Score {score} < minimum {MIN_CONFLUENCE_SCORE}"
    if open_trade_count >= MAX_OPEN_TRADES:
        return False, f"Max open trades ({MAX_OPEN_TRADES}) reached"
    if pair in open_pairs:
        return False, f"{pair} already has an open trade"
    return True, ""


def update_daily_loss(pnl: float, session_balance: float) -> None:
    """Track daily loss and set TRADING_HALTED if 3% drawdown breached."""
    global _daily_loss, TRADING_HALTED
    from config import MAX_DAILY_DRAWDOWN

    _daily_loss += pnl
    if abs(_daily_loss) / session_balance >= MAX_DAILY_DRAWDOWN:
        TRADING_HALTED = True


def reset_daily_state(current_balance: float) -> None:
    """Call at midnight UTC to reset daily counters."""
    global _daily_loss, _session_open_balance, TRADING_HALTED
    _daily_loss = 0.0
    _session_open_balance = current_balance
    TRADING_HALTED = False
