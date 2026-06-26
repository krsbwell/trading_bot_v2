import os
from dotenv import load_dotenv
load_dotenv()

MODE = "paper"   # "paper" or "live" — change ONLY this line to go live

# Active trading pairs — 4500-bar backtest (2025-09-25 → 2026-06-18), BE disabled
# USD_CAD: 56% win rate  $+100  — best performer, cleanest equity curve, 4.7% DD
# AUD_USD: 38% win rate  $+44   — strong R:R, 8.4% DD
# EUR_USD: 35% win rate  $+38   — consistent, low DD
# GBP_USD: 38% win rate  $+14   — marginal but positive, 5.2% DD
FOREX_PAIRS  = ["EUR_USD", "GBP_USD", "AUD_USD", "USD_CAD"]

# Pairs under monitoring — signals shown, NO trades opened
# EUR_AUD: 37% win rate  $+34   — profitable but volatile equity curve, 6.5% DD
# GBP_JPY: 17% win rate  $-51   — removed; spike-and-revert behaviour kills R:R without BE
# USD_JPY: 48% win rate  $+1    — EMA-bounce doesn't suit JPY momentum
FOREX_WATCH  = ["EUR_AUD"]
CRYPTO_PAIRS = []   # EMA-bounce is a forex mean-reversion strategy — does not suit crypto trending behaviour
                    # BTC: 19% win rate, -$149 P&L, 29.6% DD | ETH: -$6.96 | both removed after backtest

TIMEFRAMES = {
    "primary": "H1",    # Signal generation
    "confirm": "H4",    # Trend filter gate
    "context": "D",     # Market structure context
}

RISK_PER_TRADE       = 0.01    # 1% of account balance — hard rule
MAX_OPEN_TRADES      = 3
MAX_DAILY_DRAWDOWN   = 0.03    # 3% — halt new signals if breached
MIN_CONFLUENCE_SCORE = 55      # Minimum score to trigger a trade (slider-adjustable 40–90)
ALERT_DELAY_SECONDS  = 60      # Seconds the signal popup stays visible (trade opens immediately on signal)
TRAILING_STOP_PIPS   = 15     # Trail SL 15 pips after TP2 is hit (0 = disabled)

# ── Duplicate trade protection ────────────────────────────────────────────────
ALLOW_MULTIPLE_PER_PAIR    = False  # Allow multiple open positions for the same pair
TRADE_COOLDOWN_HOURS       = 4      # Min hours after a pair's trade closes before re-entry
LIMIT_ORDER_EXPIRY_CANDLES = 4      # Cancel unfilled limit orders after N candle closes (4 = 4h on H1)

# Session filter — restrict signals to London + NY sessions (07:00–20:00 UTC)
# Avoids low-liquidity Asian-session ranging conditions for momentum strategies.
SESSION_START_UTC = 7    # 07:00 UTC — London open
SESSION_END_UTC   = 20   # 20:00 UTC — NY close

# ── Volatility gates ──────────────────────────────────────────────────────────
ATR_MIN_PIPS = 5     # Skip signals when market is too quiet (ATR < 5 pips)
ATR_MAX_PIPS = 35    # Skip signals during extreme volatility (ATR > 35 pips)

# ── H4 trend gate ────────────────────────────────────────────────────────────
H4_GATE_BLOCKING = True   # Hard-block counter-trend signals (True) vs diagnostic-only (False)

# ── Minimum SL distance ───────────────────────────────────────────────────────
MIN_SL_PIPS = 8     # Widen SL to this minimum if strategy places it tighter

# ── Breakeven buffer ──────────────────────────────────────────────────────────
# After TP1 is hit, SL moves to entry + this many pips in profit direction.
# Gives the remaining position room to avoid being stopped by a single-tick
# pullback after TP1, increasing the chance of reaching TP2.
BREAKEVEN_BUFFER_PIPS = 3
BREAKEVEN_ENABLED     = False  # BE off for all active pairs (validated by backtest)
BREAKEVEN_PER_PAIR    = {}     # No active pair needs BE — add e.g. "GBP_JPY": True if re-added

# ── News event filter (Finnhub free API) ─────────────────────────────────────
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")   # Leave blank to disable

EMA_TEST_PERIODS          = [20, 34, 50, 60, 75, 100, 110, 125, 150, 200, 250]
EMA_REFIT_EVERY_N_CANDLES = 50

CCI_PERIOD  = 20
MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIGNAL = 9

# Oanda — auto-selects practice vs live based on MODE
OANDA_API_KEY    = os.getenv("OANDA_LIVE_API_KEY" if MODE == "live" else "OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_LIVE_ACCOUNT_ID" if MODE == "live" else "OANDA_ACCOUNT_ID")
OANDA_ENV        = "live" if MODE == "live" else "practice"

# Alpaca — auto-selects paper vs live based on MODE
ALPACA_API_KEY = os.getenv("ALPACA_LIVE_KEY" if MODE == "live" else "ALPACA_PAPER_KEY")
ALPACA_SECRET  = os.getenv("ALPACA_LIVE_SECRET" if MODE == "live" else "ALPACA_PAPER_SECRET")
ALPACA_BASE_URL = (
    "https://api.alpaca.markets" if MODE == "live"
    else "https://paper-api.alpaca.markets"
)

# Telegram notifications (leave blank to disable)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")
