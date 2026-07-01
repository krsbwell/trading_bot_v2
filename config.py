import os
from dotenv import load_dotenv
load_dotenv()

MODE = "paper"   # "paper" or "live" — change ONLY this line to go live

# Active trading pairs — updated 2026-07-01 (SESSION_START=04:00, ADX(28), 3500 M30 bars)
# USD_CAD: 48% WR  $+71.96  2.5% DD (29 trades) — WFO-optimised: CCI=28, MACD=8/21/5 → 55.6% WR on last 30 days
# NZD_USD: 38% WR  $+39.05  5.0% DD (26 trades) — PF=1.58; 2:1 R:R makes 38% WR profitable; promoted 2026-07-01
# USD_CHF: 27% win rate  $-12.38   8.9% DD — demoted to watch 2026-06-30; loses money on M30
# GBP_CHF: 24% win rate  $-26.65   8.9% DD — removed 2026-06-30; worst M30 performer; CHF low-vol kills M30 edge
# AUD_USD: 12% win rate  — removed; unacceptable live performance
FOREX_PAIRS  = ["USD_CAD", "NZD_USD"]

# Pairs under monitoring — signals shown, NO trades opened
# All results below: SESSION_START=04:00, SESSION_END=17:00, ADX(28), 3500 M30 bars
# NZD_USD: promoted to active 2026-07-01 — PF=1.58, +$39.05, 5.0% DD
# EUR_AUD: 37% WR  $+47.20  8.4% DD (43 trades) — re-added 2026-07-01; positive PnL, high trade count; WFO candidate
# EUR_CHF: 27% WR  $+1.10   4.0% DD (11 trades) — nearly breakeven; 04:00 start recovered some London-open edge
# GBP_USD: 25% WR  $-2.85   7.8% DD (47 trades) — below break-even; marginal
# EUR_USD: 24% WR  $-28.63  9.7% DD (33 trades) — losing; high DD
# USD_CHF: 21% WR  $-37.41  8.7% DD (33 trades) — pre-London CHF (04:00-07:00) very noisy; SNB wicks; watch-only
# AUD_NZD: 19% win rate  -$23.38  6.6% DD — rejected; EMA-bounce has no edge on this cross
# GBP_JPY: 17% win rate  $-51   — removed; spike-and-revert behaviour kills R:R without BE
# USD_JPY: 48% win rate  $+1    — EMA-bounce doesn't suit JPY momentum
FOREX_WATCH  = ["USD_CHF", "GBP_USD", "EUR_USD", "EUR_CHF", "EUR_AUD"]
CRYPTO_PAIRS = []   # EMA-bounce is a forex mean-reversion strategy — does not suit crypto trending behaviour
                    # BTC: 19% win rate, -$149 P&L, 29.6% DD | ETH: -$6.96 | both removed after backtest

TIMEFRAMES = {
    "primary": "M30",   # Signal generation (was H1 — M30 gives 2× decision points per hour)
    "confirm": "H1",    # Trend filter gate (was H4 — maintains 2:1 confirm:primary ratio)
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
LIMIT_ORDER_EXPIRY_CANDLES = 8      # Cancel unfilled limit orders after N candle closes (8 M30 = 4h, was 4 H1)

# ── Volatility gates ──────────────────────────────────────────────────────────
ATR_MIN_PIPS = 5     # Skip signals when market is too quiet (ATR < 5 pips)
ATR_MAX_PIPS = 35    # Skip signals during extreme volatility (ATR > 35 pips)

# ── ADX regime gate ───────────────────────────────────────────────────────────
# ADX(14) measures trend strength (not direction). EMA-bounce is mean-reversion
# and only works when the market is ranging. When ADX > threshold the market is
# trending and EMA bounces fail — price continues instead of reversing.
ADX_THRESHOLD = 28   # Hard-block signals when ADX(14) > this value (strong trend)
                     # 28 is standard; WFO will tune per pair (grid: 22, 28, 33)

# ── Session gate ─────────────────────────────────────────────────────────────
# 04:00 UTC captures European pre-market positioning (Frankfurt/Paris banks begin
# at ~05:00 UTC) and Sydney overlap — important for EUR/AUD and EUR/CHF.
# ADX filter handles ranging vs trending; session gate just cuts deep-Asian hours.
SESSION_START_UTC = 4   # 04:00 UTC — European pre-market / Sydney overlap
SESSION_END_UTC   = 17  # 17:00 UTC — NY close

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
EMA_REFIT_EVERY_N_CANDLES = 100  # was 50 on H1; 100 M30 bars ≈ same 50-hour wall-clock refit cadence

# ── Walk-Forward Optimization ────────────────────────────────────────────────
# Weekly grid-search over CCI period, MACD settings, and min_score using the
# last WFO_TRAIN_BARS of live candle data. Runs in a background thread.
WFO_ENABLED    = True   # Set False to disable the weekly re-fit entirely
WFO_TRAIN_BARS = 1440   # M30 bars used for fitting (~30 days: 48 bars/day × 30; was 720 H1)
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
