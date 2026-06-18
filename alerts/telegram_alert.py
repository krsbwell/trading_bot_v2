"""
Telegram alert sender for Apex Trading Bot.

Setup:
  1. Create a bot via @BotFather on Telegram → get TELEGRAM_BOT_TOKEN
  2. Start a chat with your bot, then visit:
       https://api.telegram.org/bot<TOKEN>/getUpdates
     to find your TELEGRAM_CHAT_ID.
  3. Add to .env:
       TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
       TELEGRAM_CHAT_ID=987654321
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID",   "")


def is_configured() -> bool:
    return bool(_TOKEN and _CHAT_ID)


def _send(text: str) -> bool:
    if not is_configured():
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
        if r.status_code != 200:
            logger.warning("Telegram HTTP %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def send_signal(signal: dict) -> bool:
    """Send a new signal notification (watching or full trade signal)."""
    pair      = signal.get("pair", "")
    direction = signal.get("direction", "").upper()
    score     = signal.get("score", 0)
    entry     = signal.get("entry") or 0
    tf        = signal.get("timeframe", "H1")
    watching  = signal.get("watching", False)
    sl        = signal.get("stop_loss")
    tp_lvls   = signal.get("tp_levels") or {}
    tp1       = tp_lvls.get("tp1")
    ema_s     = signal.get("ema_score", 0)
    str_s     = signal.get("structure_score", 0)
    pa_s      = signal.get("pa_score", 0)

    dec   = 3 if "JPY" in pair else 5
    emoji = "🟢" if direction == "LONG" else "🔴"
    tag   = "👀 WATCHING" if watching else "⚡ SIGNAL"

    lines = [
        f"{emoji} <b>{tag}</b>  {pair}",
        f"Direction: <b>{direction}</b>  |  TF: <b>{tf}</b>  |  Score: <b>{score}/100</b>",
        f"Sub-scores: EMA {ema_s:.0f}  STR {str_s:.0f}  PA {pa_s:.0f}",
    ]
    if entry:
        lines.append(f"Entry: <code>{entry:.{dec}f}</code>")
    if sl:
        lines.append(f"Stop Loss: <code>{sl:.{dec}f}</code>")
    if tp1:
        lines.append(f"TP1: <code>{tp1:.{dec}f}</code>")

    return _send("\n".join(lines))


def send_trade_opened(trade: dict) -> bool:
    """Notify when a paper/live trade is opened."""
    pair  = trade.get("pair", "")
    d     = trade.get("direction", "").upper()
    entry = trade.get("entry", 0)
    sl    = trade.get("sl", 0)
    tp1   = trade.get("tp1", 0)
    size  = trade.get("size", 0)
    tid   = trade.get("id", "")
    dec   = 3 if "JPY" in pair else 5
    emoji = "📈" if d == "LONG" else "📉"

    text = (
        f"{emoji} <b>TRADE OPENED</b>  {pair}\n"
        f"Direction: <b>{d}</b>  |  ID: <code>{tid}</code>\n"
        f"Entry: <code>{entry:.{dec}f}</code>  "
        f"SL: <code>{sl:.{dec}f}</code>  "
        f"TP1: <code>{tp1:.{dec}f}</code>\n"
        f"Size: <code>{size}</code>"
    )
    return _send(text)


def send_trade_closed(trade: dict) -> bool:
    """Notify when a paper/live trade is fully closed."""
    pair   = trade.get("pair", "")
    reason = trade.get("close_reason", "manual").upper()
    pnl    = trade.get("realised_pnl", 0)
    exit_p = trade.get("exit_price", 0)
    tid    = trade.get("id", "")
    dec    = 3 if "JPY" in pair else 5
    emoji  = "✅" if pnl >= 0 else "❌"
    pnl_s  = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

    text = (
        f"{emoji} <b>TRADE CLOSED ({reason})</b>  {pair}\n"
        f"Exit: <code>{exit_p:.{dec}f}</code>  P&amp;L: <b>{pnl_s}</b>\n"
        f"ID: <code>{tid}</code>"
    )
    return _send(text)


def send_drawdown_halt() -> bool:
    """Alert when the 3% daily drawdown halt is triggered."""
    return _send(
        "⛔ <b>TRADING HALTED</b>\n"
        "Daily drawdown limit (3%) has been breached.\n"
        "No new trades will be opened until midnight UTC."
    )
