import os
from dotenv import load_dotenv
load_dotenv()

MODE = "paper"   # "paper" or "live" — change ONLY this line to go live

# Active trading pairs — updated 2026-06-26 after backtest review
# USD_CAD: 52% win rate  — best performer, confirmed by backtest and live data
# USD_CHF: 42% win rate  $+37.60  2.1% DD — confirmed; excellent R:R and low drawdown
# GBP_CHF: 50% win rate  $+40.19  2.8% DD — promoted 2026-06-26; highest WR of all pairs tested
# AUD_USD: 12% win rate  — removed; unacceptable live performance
FOREX_PAIRS  = ["USD_CAD", "USD_CHF", "GBP_CHF"]

# Pairs under monitoring — signals shown, NO trades opened
# GBP_USD: 33% win rate  — below break-even threshold; watching for improvement before re-activating
# EUR_USD: 27% win rate  — structurally mismatched with EMA-bounce; strong trend pair
# NZD_USD: 39% win rate  $+22.68  4.6% DD — profitable but full SL hits dominate; equity underwater too long
# AUD_NZD: 19% win rate  -$23.38  6.6% DD — rejected; EMA-bounce has no edge on this cross
# EUR_CHF: 43% win rate  $+4.94  1.7% DD — best DD of all pairs but only 7 trades/3mo; too few signals
# GBP_CHF: 50% win rate  $+40.19  2.8% DD — promoted to active 2026-06-26
# GBP_JPY: 17% win rate  $-51   — removed; spike-and-revert behaviour kills R:R without BE
# USD_JPY: 48% win rate  $+1    — EMA-bounce doesn't suit JPY momentum
# EUR_AUD: removed 2026-06-29 — 9.1% max DD too volatile; signals were watch-only and scoring below threshold
FOREX_WATCH  = ["GBP_USD", "EUR_USD", "NZD_USD", "EUR_CHF"]
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

# Session filter — London + London/NY overlap only (07:00–16:00 UTC)
# NY solo (16:00–21:00) and Asian (21:00–07:00) both show 23% WR — excluded.
SESSION_START_UTC = 7    # 07:00 UTC — London open
SESSION_END_UTC   = 16   # 16:00 UTC — end of London/NY overlap

# ── Volatility gates ──────────────────────────────────────────────────────────
ATR_MIN_PIPS = 5     # Skip signals when market is too quiet (ATR < 5 pips)
ATR_MAX_PIPS = 35    # Skip signals during extreme volatility (ATR > 35 pips)

# ── H4 trend gate ────────────────────────────────────────────────────────────
H4_GATE_BLOCKING = True   # Hard-block counter-trend signals (True) vs diagnostic-only (False)

# ── Minimum SL distance ───────────────────────────────────────────────────────
MIN_SL_PIPS = 25    # Minimum SL distance in pips — 25 validated by backtest (20 caught H1 noise)

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

# ── Walk-Forward Optimization ────────────────────────────────────────────────
# Weekly grid-search over CCI period, MACD settings, and min_score using the
# last WFO_TRAIN_BARS of live candle data. Runs in a background thread.
WFO_ENABLED    = True   # Set False to disable the weekly re-fit entirely
WFO_TRAIN_BARS = 720    # H1 bars used for fitting (~30 days of data)
WFO_REFIT_DAYS = 7      # Days between re-fits per pair

# ── Adaptive parameter tuning ─────────────────────────────────────────────────
# Adjusts CCI threshold, EMA touch band, and MACD bar count per pair based on
# recent win rate. See engine/adaptive_params.py for tier definitions.
ADAPTIVE_PARAMS_ENABLED  = True   # Set False to lock all pairs at base thresholds
ADAPTIVE_LOOKBACK_TRADES = 20     # How many recent closed trades to measure win rate from
ADAPTIVE_REFIT_EVERY_N   = 5      # Minimum new trades before recalculating thresholds

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
