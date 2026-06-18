import os
from dotenv import load_dotenv
load_dotenv()

MODE = "paper"   # "paper" or "live" — change ONLY this line to go live

# Backtested pairs — only include pairs with positive Calmar ratio
# GBP_USD: Calmar 2.21  EUR_USD: Calmar 1.26  (both tested, 6-month backtest)
FOREX_PAIRS  = ["EUR_USD", "GBP_USD"]

# Pairs under monitoring — signals shown but NO trades opened
# EUR_AUD: marginal (+$11, 15% DD, needs more data)
# AUD_USD: negative (-$37, 19.6% DD)
# USD_JPY: losing (EMA-bounce doesn't suit JPY momentum character)
FOREX_WATCH  = ["EUR_AUD", "AUD_USD", "USD_JPY"]
CRYPTO_PAIRS = ["BTC/USD", "ETH/USD", "SOL/USD"]   # Alpaca format

TIMEFRAMES = {
    "primary": "H1",    # Signal generation
    "confirm": "H4",    # Trend filter gate
    "context": "D",     # Market structure context
}

RISK_PER_TRADE       = 0.01    # 1% of account balance — hard rule
MAX_OPEN_TRADES      = 3
MAX_DAILY_DRAWDOWN   = 0.03    # 3% — halt new signals if breached
MIN_CONFLUENCE_SCORE = 52      # Minimum score to trigger a trade (slider-adjustable 40–90)
ALERT_DELAY_SECONDS  = 10      # Countdown before order executes
TRAILING_STOP_PIPS   = 0      # Trail SL N pips (0 = disabled — TP1/2/3 structure handles management)

# Session filter — backtest showed EMA-bounce strategy performs better 24/7
# (mean-reversion thrives in Asian-session ranging conditions).
# Keep as config option but default to 24h.  Set 7/21 to restrict to London/NY.
SESSION_START_UTC = 0    # 0 = disabled (24/7 trading)
SESSION_END_UTC   = 24   # 24 = disabled

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
