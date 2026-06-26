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


def get_tp_levels(entry: float, stop_loss: float, direction: str) -> dict:
    """
    Return TP1/TP2/TP3 at 1.0R / 2.5R / 4.0R.

    TP1 at 1.0R (changed from 1.5R):
      Hitting TP1 more reliably is more important than squeezing extra ticks from
      it. At 1.0R, TP1 fires before most reversals, banks 40% of the position,
      and moves the SL to breakeven+buffer — protecting capital while leaving
      the majority of the position open for the TP2/TP3 continuation move.

    TP2 at 2.5R / TP3 at 4.0R (tightened from 3R / 5R):
      Keeps meaningful R:R while being more achievable in a trending session,
      increasing the frequency of TP2 and TP3 hits and reducing reliance on the
      rare 5R home-run trades that were the only way to stay positive before.
    """
    dist = abs(entry - stop_loss)
    mult = 1 if direction == "long" else -1
    return {
        "tp1": round(entry + mult * dist * 1.0, 5),   # 1.0:1 RR, close 40%
        "tp2": round(entry + mult * dist * 2.5, 5),   # 2.5:1 RR, close 35%
        "tp3": round(entry + mult * dist * 4.0, 5),   # 4.0:1 RR, close 25%
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
