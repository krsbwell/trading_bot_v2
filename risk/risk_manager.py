TRADING_HALTED = False
_daily_loss = 0.0
_session_open_balance = 0.0


def calculate_position_size(account_balance: float, entry: float,
                             stop_loss: float, instrument_type: str,
                             pair: str = "") -> float:
    """
    Returns position size enforcing 1% risk hard rule.
    instrument_type: 'forex' (returns int units) or 'crypto' (returns float qty).
    pair: used to select correct pip size (JPY pairs use 0.01, others 0.0001).
    """
    risk_amount = account_balance * 0.01
    sl_distance = abs(entry - stop_loss)

    if instrument_type == "forex":
        pip = 0.01 if "JPY" in pair.upper() else 0.0001
        sl_pips = sl_distance / pip
        units = risk_amount / (sl_pips * pip)
        return int(units)

    elif instrument_type == "crypto":
        qty = risk_amount / sl_distance
        return round(qty, 6)

    raise ValueError(f"Unknown instrument_type: {instrument_type}")


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
