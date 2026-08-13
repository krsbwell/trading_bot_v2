import os
from dotenv import load_dotenv
load_dotenv()

MODE = "demo"   # "paper" (pure simulation, no OANDA orders) / "demo" (real
                # orders on OANDA practice account) / "live" (real orders on
                # real-money OANDA account) — change ONLY this line

# Active trading pairs — updated 2026-07-02 (SESSION_START=04:00, ADX(28), 3500 M30 bars)
# NOTE 2026-07-20: WR/PF/DD figures below are from the 2026-07-01/02 backtests and
# have not been re-run since. Also, a merge-order bug in adaptive_params.get() was
# silently discarding WFO's per-pair cci_threshold in live scoring (always fell back
# to the hardcoded 20) — fixed 2026-07-20 (engine/adaptive_params.py). CCI settings
# quoted below predate that fix and predate several since-run WFO cycles; check
# data/wfo_params.json for what's actually live now rather than trusting these
# inline numbers going forward.
# USD_CAD: 46% WR  $+70.81  3.1% DD (28 trades) PF=2.08 — WFO-optimised: CCI=28, MACD=8/21/5 → 55.6% WR on last 30 days
# NZD_USD: 36% WR  $+26.46  5.0% DD (25 trades) PF=1.43 — 2:1 R:R makes sub-50% WR profitable; promoted 2026-07-01
# EUR_AUD: 33% WR  $+39.35  8.4% DD (42 trades) PF=1.42 — promoted 2026-07-02; best watch-pair PnL, solid sample size
# GBP_CAD: 41% WR  $+71.82  4.8% DD (46 trades) PF=1.66 — promoted 2026-07-02; found via 15-pair screen, as strong as the original active pairs
# GBP_USD: promoted 2026-07-02 running strategy_breakout_retest (see STRATEGY_OVERRIDE below),
#          NOT EMA-bounce. 4999-bar backtest: WR=45% PF=1.94 $+42.63 (20 trades) 3.1% DD — held up
#          and improved with more data (was PF=1.80/$30.00 at 3500 bars). EMA-bounce edge on this
#          pair was only PF=1.11 (thin) — breakout-retest is a genuinely stronger, independent signal here.
# GBP_CHF: 24% win rate  $-26.65   8.9% DD — removed 2026-06-30; worst M30 performer; CHF low-vol kills M30 edge
# AUD_USD: 12% win rate  — removed; unacceptable live performance
#
# CHF_JPY + EUR_JPY promoted 2026-07-29 from a fresh 3500-bar re-run (both
# EMA-bounce): CHF_JPY 32 trades PF=1.67 $+51.37; EUR_JPY (first-ever
# backtest for this pair) 25 trades PF=1.67 $+41.85. Promoted specifically
# for trade-frequency: both have a large enough sample to trust (established
# rule: ~30+ trades reliable, <15 not) and add real trade volume without
# touching any existing pair's gates/thresholds. See tasks/todo.md 2026-07-29.
# USD_CAD and EUR_AUD paused 2026-08-05 (moved to FOREX_WATCH below, not
# removed): fresh walk-forward on both showed OOS PF=0.00, 0% win rate in
# the most recent test window (2026-07-15 -> 08-05) — matches this week's
# live losses on both pairs (USD_CAD -$5.20, EUR_AUD -$5.36). Their
# wfo_params.json fits are also stale (EUR_AUD last fit 2026-07-01, 35
# days; USD_CAD 2026-07-19, 17 days). Signals still shown, no trades open,
# while a fresh refit is validated against this same OOS window before
# either pair goes back to FOREX_PAIRS.
#
# 2026-08-13 — strategy overhaul, see tasks/todo.md for the full multi-day
# investigation: live EMA-bounce was checked with an exhaustive, holdout-
# validated 216-combo WFO search and found NO parameter combination that
# held up out-of-sample for ANY of the then-5 active pairs — not a tuning
# gap, no detectable live edge. breakout_retest (previously GBP_USD/M30
# only) was tested broadly at H4 and survived a real fit/holdout split on
# 6 of 8 pairs tried, more consistent than EMA-bounce at the same
# timeframe. NZD_USD/GBP_CAD/CHF_JPY switched to breakout_retest at H4
# (see PRIMARY_TF_PER_PAIR/CONFIRM_TF_PER_PAIR). GBP_USD unchanged — already
# proven, don't fix what isn't broken. EUR_JPY paused (see FOREX_WATCH) —
# no strategy/timeframe tested showed any validated edge for it.
FOREX_PAIRS  = ["NZD_USD", "GBP_CAD", "GBP_USD", "CHF_JPY"]

# Per-pair strategy override — a pair not listed here uses the default
# (strategy_ema_cci_macd / "EMA-bounce"). breakout_retest and trend_retest
# are also wired into signal_engine.py (see engine/strategy_trend_follow.py
# — shelved, backtest-only, never wired live; see
# project_trend_follow_experiment memory for why. trend_retest — backtest-
# only as of 2026-08-13, never validated well enough to go live, see
# tasks/todo.md).
STRATEGY_OVERRIDE = {
    "GBP_USD":  "breakout_retest",   # promoted 2026-07-02 — see FOREX_PAIRS comment above.
    "NZD_USD":  "breakout_retest",   # 2026-08-13 — holdout-validated at H4, see FOREX_PAIRS comment.
    "GBP_CAD":  "breakout_retest",   # 2026-08-13 — holdout-validated at H4 (PF improved 1.59->3.59
                                      # out-of-sample); previously tested under breakout-retest at M30
                                      # in 2026-07 and showed a persistent conflict there (PF 0.44) —
                                      # that finding was M30-specific, not a blanket rejection of the
                                      # strategy for this pair; H4 is a genuinely different result.
    "CHF_JPY":  "breakout_retest",   # 2026-08-13 — holdout-validated at H4 (stayed >1 both windows).
    # XAU_USD/"gold_trend" removed 2026-08-01 — shelved, see tasks/todo.md.
    # engine/strategy_gold_trend.py and its STRATEGY_FNS entry are left in
    # place (unused, harmless) in case gold is revisited later.
}

# Stop-loss method for strategy_gold_trend.py — "dynamic" (behind the 50 EMA
# pullback level, ATR fallback) or "swing" (behind the previous calendar
# week's high/low). Neither is validated yet; backtest both before trusting
# this default. See engine/strategy_gold_trend.get_stop_loss docstring.
GOLD_STOP_METHOD = "dynamic"

# Pairs under monitoring — signals shown, NO trades opened. Only pairs with
# POSITIVE backtested PnL belong here; a losing pair isn't worth watching.
# All results below: SESSION_START=04:00, SESSION_END=17:00, ADX(28), 3500 M30 bars (re-run 2026-07-02)
# EUR_CHF: 30% WR  $+10.46  4.0% DD (10 trades) PF=1.70 — best PF but too few trades to be confident it holds; held back 2026-07-02
# AUD_JPY: 36% WR  $+36.84  7.1% DD (44 trades) PF=1.32 — added 2026-07-02 from 15-pair screen; solid sample, positive
# EUR_CAD: 35% WR  $+36.08  5.4% DD (34 trades) PF=1.44 — added 2026-07-02; re-run 2026-07-29 came back ~breakeven
#          (PF=1.00, $+0.24) — left on watch, not promoted, numbers drifted since original add.
# EUR_GBP: 50% WR  $+21.00  2.0% DD (6 trades)  PF=3.93 — added 2026-07-02; best ratio of the screen but too few trades to trust yet
# CAD_CHF: 33% WR  $+13.69  3.1% DD (9 trades)  PF=1.96 — added 2026-07-02; good ratio, too few trades to trust yet
# NZD_CHF: 36% WR  $+6.92   5.1% DD (11 trades) PF=1.30 — added 2026-07-02; modest, too few trades to trust yet
# CHF_JPY: promoted to FOREX_PAIRS 2026-07-29 — see above.
# USD_CAD, EUR_AUD: paused from FOREX_PAIRS 2026-08-05 — see comment above
# FOREX_PAIRS. Not a rejection, a hold pending fresh WFO validation.
# EUR_JPY: paused from FOREX_PAIRS 2026-08-13 — see comment above FOREX_PAIRS.
# No strategy (EMA-bounce, breakout_retest) or timeframe (M30/H1/H4/M15)
# tested that day showed any validated edge for this pair specifically;
# signals still shown, no trades open, pending real evidence one way or
# the other.
FOREX_WATCH  = ["EUR_CHF", "AUD_JPY", "EUR_CAD", "EUR_GBP", "CAD_CHF", "NZD_CHF",
                "USD_CAD", "EUR_AUD", "EUR_JPY"]

# Rejected / removed — losing money, not watched (re-test only if the strategy or gates change)
# EUR_USD: 23% WR  $-16.51  9.7% DD (30 trades) PF=0.80 — removed from watch 2026-07-02; unsuited to EMA mean-reversion regardless of filters
# USD_CHF: 29% WR  $-9.19   7.9% DD (31 trades) PF=0.89 — removed from watch 2026-07-02; pre-London CHF session still noisy
# AUD_NZD: 19% win rate  -$23.38  6.6% DD — rejected; EMA-bounce has no edge on this cross
# GBP_JPY: 17% win rate  $-51   — removed; spike-and-revert behaviour kills R:R without BE
# USD_JPY: 48% win rate  $+1    — EMA-bounce doesn't suit JPY momentum (near-zero P&L despite high WR)
# Rejected 2026-07-02 (15-pair screen, all losing money):
# EUR_NZD -$31.26/14.4%DD | GBP_AUD -$1.58/9.1%DD | GBP_NZD -$35.27/17.4%DD (worst) |
# AUD_CHF -$38.01/PF=0.23 (worst ratio) | AUD_CAD -$14.23 | NZD_CAD -$15.43 | CAD_JPY -$15.51 | NZD_JPY -$8.32
TIMEFRAMES = {
    "primary": "M30",   # Signal generation (was H1 — M30 gives 2× decision points per hour)
    "confirm": "H4",    # Trend filter gate. Changed 2026-07-23 from H1 back to a real
                         # H4 — H1 was only ever a 2:1 ratio above M30 primary (too close
                         # to give independent confirmation) and the "H4" label/variable
                         # names across the codebase (h4_trend, "C2 H4 aligned", the
                         # Signal Monitor's "H4:" column) had silently kept referring to
                         # H1 data since that switch. Backtested real H4 vs the H1 confirm
                         # across all 5 active pairs, 3500 M30 bars each: 4/5 improved,
                         # several substantially (GBP_CAD PF 2.24->3.91, USD_CAD PF
                         # 1.57->2.31, GBP_USD PF 1.01->1.32); EUR_AUD declined (PF
                         # 2.42->1.63) — same pair that was the outlier for the TP R:R
                         # change too. Net positive — adopted. See tasks/todo.md.
    "context": "D",     # Market structure context
}

# Per-pair override of TIMEFRAMES["confirm"] — a pair not listed here uses
# the global confirm TF above. Same pattern as STRATEGY_OVERRIDE /
# BREAKEVEN_PER_PAIR / TP_RR_PER_PAIR.
#
# EUR_AUD kept on H1 (2026-07-23): declined under the global H1->H4 switch
# (PF 2.42->1.63, PnL $75.32->$33.87). Backtest-confirmed real H4 vs real H1
# confirm for EUR_AUD specifically, both fetched through this same per-pair
# resolver: H1 clearly better (higher WR/PF/PnL, trades held up), H4 only
# had a slightly lower MaxDD. See tasks/todo.md 2026-07-23.
CONFIRM_TF_PER_PAIR: dict = {
    "EUR_AUD": "H1",
    # NZD_USD/GBP_CAD/CHF_JPY moved to H4 primary 2026-08-13 (see
    # PRIMARY_TF_PER_PAIR below) — H4 can't confirm itself, so these get
    # Daily as their confirm/bias TF instead, matching the "Daily sets bias,
    # H4 structure, lower TF entry" pattern from that session's web research.
    "NZD_USD": "D",
    "GBP_CAD": "D",
    "CHF_JPY": "D",
}


def confirm_tf_for(pair: str) -> str:
    """Resolve the confirm timeframe for a pair — CONFIRM_TF_PER_PAIR wins if set."""
    return CONFIRM_TF_PER_PAIR.get(pair, TIMEFRAMES["confirm"])


# Per-pair override of TIMEFRAMES["primary"] — a pair not listed here uses
# the global primary TF above (M30). Same pattern as CONFIRM_TF_PER_PAIR.
#
# NZD_USD/GBP_CAD/CHF_JPY moved to H4 2026-08-13: an exhaustive, holdout-
# validated 216-combo WFO search found NO parameter combination that held
# up out-of-sample for any of the 5 pairs then live on EMA-bounce/M30 —
# not a tuning gap, the strategy has no detectable edge on that data right
# now. Separately, breakout_retest (previously only live on GBP_USD/M30)
# was backtested at H4 across the wider roster and held up in a genuine
# fit/holdout split on 6 of 8 tested pairs, including these 3 — more
# consistent than EMA-bounce was at the same timeframe. See tasks/todo.md
# for the full multi-day investigation (Tests #1-3, holdout validation,
# fair tuning pass, WFO averaging-bug fix, exhaustive per-pair optimize).
PRIMARY_TF_PER_PAIR: dict = {
    "NZD_USD": "H4",
    "GBP_CAD": "H4",
    "CHF_JPY": "H4",
}


def primary_tf_for(pair: str) -> str:
    """Resolve the primary (signal-generation) timeframe for a pair —
    PRIMARY_TF_PER_PAIR wins if set."""
    return PRIMARY_TF_PER_PAIR.get(pair, TIMEFRAMES["primary"])

RISK_PER_TRADE       = 0.01    # 1% of account balance — hard rule
MAX_OPEN_TRADES      = 3
MAX_DAILY_DRAWDOWN   = 0.03    # 3% — halt new signals if breached
MIN_CONFLUENCE_SCORE = 55      # Minimum score to trigger a trade (slider-adjustable 40–90)
ALERT_DELAY_SECONDS  = 60      # Seconds the signal popup stays visible (trade opens immediately on signal)
TRAILING_STOP_PIPS   = 15     # Trail SL 15 pips after TP2 is hit (0 = disabled)

# ── ATR-adaptive trailing stop ────────────────────────────────────────────────
# Replaces the fixed TRAILING_STOP_PIPS distance with ATR(period)*multiplier
# when enabled, so the trail widens/tightens with volatility instead of using
# one static pip distance for every market condition. Off by default — same
# convention as BREAKEVEN_ENABLED below: a new risk mechanism shouldn't
# silently change live behavior until deliberately tested/enabled per pair.
ATR_TRAILING_ENABLED    = False
ATR_TRAILING_MULTIPLIER = 2.0
ATR_TRAILING_PERIOD     = 14

# ── Duplicate trade protection ────────────────────────────────────────────────
ALLOW_MULTIPLE_PER_PAIR    = False  # Allow multiple open positions for the same pair
TRADE_COOLDOWN_HOURS       = 4      # Min hours after a pair's trade closes before re-entry
LIMIT_ORDER_EXPIRY_CANDLES = 8      # Cancel unfilled limit orders after N candle closes (8 M30 = 4h, was 4 H1)

# ── Volatility gates ──────────────────────────────────────────────────────────
ATR_MIN_PIPS = 5     # Skip signals when market is too quiet (ATR < 5 pips)
ATR_MAX_PIPS = 35    # Skip signals during extreme volatility (ATR > 35 pips)

# Per-pair override — the two constants above assume FX-scale pip sizes
# (0.0001, or 0.01 for JPY). XAU_USD's pip is also 0.01 (see _get_pip in the
# strategy modules) but gold's price moves are ~$10-20 per H1 bar, i.e.
# ~1000+ "pips" — the global 5-35 bound would permanently trip ATR_TOO_HIGH
# and block every gold signal forever. Placeholder below is grounded in one
# real 200-bar H1 sample fetched 2026-08-01 (ATR range $11.40-$23.22, median
# $14.50 -> ~1140-2320 pips, median ~1450), NOT a validated range from a full
# backtest — retune once engine/strategy_gold_trend.py has real backtest data.
ATR_MIN_PIPS_PER_PAIR: dict = {
    "XAU_USD": 200,    # ~$2 floor — skip only truly dead/holiday markets
}
ATR_MAX_PIPS_PER_PAIR: dict = {
    "XAU_USD": 4000,   # ~$40 — above the observed sample max, room for news spikes
}


def atr_min_pips_for(pair: str) -> float:
    """Resolve ATR_MIN_PIPS for a pair — ATR_MIN_PIPS_PER_PAIR wins if set."""
    return ATR_MIN_PIPS_PER_PAIR.get(pair, ATR_MIN_PIPS)


def atr_max_pips_for(pair: str) -> float:
    """Resolve ATR_MAX_PIPS for a pair — ATR_MAX_PIPS_PER_PAIR wins if set."""
    return ATR_MAX_PIPS_PER_PAIR.get(pair, ATR_MAX_PIPS)

# ── ADX regime gate ───────────────────────────────────────────────────────────
# ADX(14) measures trend strength (not direction). EMA-bounce is mean-reversion
# and only works when the market is ranging. When ADX > threshold the market is
# trending and EMA bounces fail — price continues instead of reversing.
ADX_THRESHOLD = 28   # Hard-block signals when ADX(14) > this value (strong trend)
                     # 28 is standard; WFO will tune per pair (grid: 22, 28, 33)

# ── Breakout-retest consolidation gate ──────────────────────────────────────
# Opposite intent from the ADX gate above: breakout-retest is a trend
# strategy, so it should skip entries when the market is compressed/ranging
# instead of trending. Measures the same 34/100/200 EMA ribbon the chart
# shows — when the three EMAs sit close together (small ATR-normalized
# spread), the market is consolidating and a "break of structure" is more
# likely a fakeout than a real trend start. Off by default until backtested;
# see tasks/todo.md 2026-07-29.
BREAKOUT_CONSOLIDATION_FILTER_ENABLED = False
BREAKOUT_MIN_MA_SPREAD_ATR = 0.5   # (max-min of EMA34/100/200) / ATR(14) must clear this

# ── Session gate ─────────────────────────────────────────────────────────────
# 04:00 UTC captures European pre-market positioning (Frankfurt/Paris banks begin
# at ~05:00 UTC) and Sydney overlap — important for EUR/AUD and EUR/CHF.
# ADX filter handles ranging vs trending; session gate just cuts deep-Asian hours.
SESSION_START_UTC = 4   # 04:00 UTC — European pre-market / Sydney overlap
SESSION_END_UTC   = 17  # 17:00 UTC — NY close

# ── Weekend close ────────────────────────────────────────────────────────────
# Force-close every open trade at SESSION_END_UTC on Fridays — avoids holding
# through the weekend gap (FX markets close after Friday and reopen Sunday
# evening; a large weekend news gap can blow straight through a normal SL).
# Added 2026-07-29, user request. Python weekday(): Monday=0 … Friday=4.
WEEKEND_CLOSE_ENABLED     = True
WEEKEND_CLOSE_WEEKDAY_UTC = 4   # Friday
# Starting this many hours before SESSION_END_UTC on Friday, close a trade
# as soon as it's in profit rather than waiting for the hard deadline —
# "try to end trades in profit as best as possible" (user request
# 2026-07-29). A trade that's still not in profit rides to the hard
# SESSION_END_UTC cutoff and force-closes there regardless — the weekend-gap
# safety guarantee is absolute, the profit-seeking is best-effort on top of it.
WEEKEND_CLOSE_PROFIT_WINDOW_HOURS = 3

# ── Watchdog (scripts/watchdog.py) ──────────────────────────────────────────
# Scan cadence is 30 min (M30 close); 60 min gives one full cycle of buffer
# for a slow API call before flagging the process as dead.
WATCHDOG_STALE_MINUTES = 60

# ── H4 trend gate ────────────────────────────────────────────────────────────
# Briefly softened 2026-08-02 as part of the filter-cleanup merge (theory:
# redundant with the ADX gate, was killing signals ADX said were fine).
# Reverted same day, alongside the CCI-touch gate revert above — backtesting
# the merged result showed H4 softening made every single one of the 6
# active pairs worse (3500 M30 bars each, holding EMA-autofit/adaptive-params
# at their new post-merge values): USD_CAD PF 1.73->2.07, EUR_AUD PF 1.82->
# 2.10, GBP_CAD PF 3.51->4.04, CHF_JPY PF 1.19->1.56, EUR_JPY PF 1.51->1.62
# when hard-blocking was restored (NZD_USD unchanged — it already runs H4
# loosened via NZD_USD: False below). Total PnL across the roster: $302.37
# soft-scored -> $368.56 hard-blocked, a clean win with zero exceptions,
# unlike the CCI-touch gate above which needed a per-pair carve-out. ADX
# alone is evidently not a sufficient substitute for the H4 gate on this
# roster — keep both.
H4_GATE_BLOCKING = True

# Per-pair override of H4_GATE_BLOCKING — a pair not listed here uses the
# global value above. Same pattern as CONFIRM_TF_PER_PAIR/TP_RR_PER_PAIR.
#
# NZD_USD loosened (2026-07-24): backtested blocking vs loosened across all
# 5 active pairs, 3500 M30 bars each, under current confirm-TF/TP-R:R
# settings. NZD_USD clearly benefits from loosening (PF 1.31->1.84, PnL
# $15.85->$45.07, WR 35.3%->45.0%); USD_CAD, GBP_CAD, and EUR_AUD all got
# meaningfully worse under the same change (e.g. EUR_AUD PF 2.42->1.29) —
# confirms this needs to be per-pair, not a global flip. See tasks/todo.md.
H4_GATE_BLOCKING_PER_PAIR: dict = {
    "NZD_USD": False,
}

# ── CCI-at-touch gate (c4) ───────────────────────────────────────────────────
# c4 requires CCI to be sufficiently extreme (oversold/overbought) at the exact
# EMA-touch bar. This is the single weakest condition across all logged
# signals (~28% pass rate, the persistent "bottleneck" in the Signal Quality
# panel).
#
# Briefly hard-blocked 2026-08-02 as part of the filter-cleanup merge (same
# day as H4_GATE_BLOCKING softening, EMA auto-fit disable, adaptive-params
# disable) — reverted same day after backtesting the merged result showed a
# real regression on 5 of 6 active pairs (3500 M30 bars each, all four
# changes' combined effect): total PnL across USD_CAD/NZD_USD/EUR_AUD/
# GBP_CAD/CHF_JPY/EUR_JPY dropped from $345.62 (pre-merge baseline) to
# $54.50. Bisecting by reverting just this one flag (holding the other
# three at their new post-merge values) recovered to $284.13 — GBP_CAD
# PF 4.40->1.52->3.51, EUR_JPY PF 1.61->0.66->1.51, USD_CAD PF 2.07->0.94->
# 1.74, NZD_USD PF 1.54->2.09->1.54 (exact match to baseline). Root cause:
# hard-blocking a condition with only a ~28% pass rate discards roughly 3
# of every 4 signals outright — that outweighs the extra volume the other
# three changes' loosening was supposed to add, the opposite of
# filter-cleanup's stated goal (trade counts fell on every single pair,
# not just PF).
#
# CHF_JPY is the one exception found — it does WORSE with this gate soft
# (PF 1.18->0.95), i.e. it specifically benefits from the hard block. Kept
# via CCI_TOUCH_GATE_BLOCKING_PER_PAIR below rather than forcing one global
# answer on a pair where the data disagrees with it.
CCI_TOUCH_GATE_BLOCKING = False

# Per-pair override — same pattern as H4_GATE_BLOCKING_PER_PAIR above.
CCI_TOUCH_GATE_BLOCKING_PER_PAIR: dict = {
    "CHF_JPY": True,
}


def h4_gate_blocking_for(pair: str) -> bool:
    """Resolve H4_GATE_BLOCKING for a pair — H4_GATE_BLOCKING_PER_PAIR wins if set."""
    return H4_GATE_BLOCKING_PER_PAIR.get(pair, H4_GATE_BLOCKING)


def cci_touch_gate_blocking_for(pair: str) -> bool:
    """Resolve CCI_TOUCH_GATE_BLOCKING for a pair — CCI_TOUCH_GATE_BLOCKING_PER_PAIR wins if set."""
    return CCI_TOUCH_GATE_BLOCKING_PER_PAIR.get(pair, CCI_TOUCH_GATE_BLOCKING)

# ── Minimum SL distance ───────────────────────────────────────────────────────
MIN_SL_PIPS = 25    # Minimum SL distance in pips — 25 validated by backtest (20 caught H1 noise)

# Per-pair override — same reasoning as ATR_MIN/MAX_PIPS_PER_PAIR above: the
# global 25-pip floor is $0.25 at gold's 0.01 pip size, meaningless next to
# gold's real volatility. Placeholder pending backtest tuning, not validated.
MIN_SL_PIPS_PER_PAIR: dict = {
    "XAU_USD": 300,   # ~$3
}


def min_sl_pips_for(pair: str) -> float:
    """Resolve MIN_SL_PIPS for a pair — MIN_SL_PIPS_PER_PAIR wins if set."""
    return MIN_SL_PIPS_PER_PAIR.get(pair, MIN_SL_PIPS)

# ── Breakeven buffer ──────────────────────────────────────────────────────────
# After TP1 is hit, SL moves to entry + this many pips in profit direction.
# Gives the remaining position room to avoid being stopped by a single-tick
# pullback after TP1, increasing the chance of reaching TP2.
BREAKEVEN_BUFFER_PIPS = 3
BREAKEVEN_ENABLED     = False  # BE off for all active pairs (validated by backtest)
BREAKEVEN_PER_PAIR    = {}     # No active pair needs BE — add e.g. "GBP_JPY": True if re-added

# ── Per-pair TP R:R override ─────────────────────────────────────────────────
# get_tp_levels() uses (1.5, 2.5, 3.5) for any pair not listed here.
TP_RR_PER_PAIR = {
    # EUR_AUD got worse under the 2026-07-22 global TP change (PF 2.09->1.83).
    # 7-way sweep found 1.0R/3.0R/4.5R beats both the old and new global
    # defaults on every metric for this pair specifically (PF 2.63, PnL +28%,
    # MaxDD down to 6.2% vs 7.6%) — TP1 at 1.5R was the specific problem.
    "EUR_AUD": (1.0, 3.0, 4.5),
}

# ── News event filter (Finnhub free API) ─────────────────────────────────────
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")   # Leave blank to disable

EMA_TEST_PERIODS          = [20, 34, 50, 60, 75, 100, 110, 125, 150, 200, 250]
EMA_REFIT_EVERY_N_CANDLES = 100  # was 50 on H1; 100 M30 bars ≈ same 50-hour wall-clock refit cadence

# ── EMA auto-fit toggle ──────────────────────────────────────────────────────
# Disabled 2026-08-02: auto-fit was curve-fitting to the last 200 bars and
# re-selecting EMAs every ~2 days, causing constant behavior drift. Fixed
# periods are stable and testable. Re-enable only if out-of-sample backtest
# proves auto-fit beats fixed periods (not just in-sample).
EMA_AUTOFIT_ENABLED = False
EMA_FIXED_PERIODS   = (20, 50, 200)   # short, mid, long

# ── Walk-Forward Optimization ────────────────────────────────────────────────
# Weekly grid-search over CCI period, MACD settings, and min_score using the
# last WFO_TRAIN_BARS of live candle data. Runs in a background thread.
WFO_ENABLED    = True   # Set False to disable the weekly re-fit entirely
WFO_TRAIN_BARS = 1440   # M30 bars used for fitting (~30 days: 48 bars/day × 30; was 720 H1)
WFO_REFIT_DAYS = 7      # Days between re-fits per pair

# Fit/holdout split added 2026-08-11 — see engine/wfo_optimizer.py module
# docstring for why: picking the single best-in-sample combo out of 216 with
# no out-of-sample check is overfitting by construction (GBP_CAD's live params
# were an 83%-WR/6-trade in-sample fluke). The last WFO_HOLDOUT_FRAC of
# WFO_TRAIN_BARS is held out, untouched by the grid search, and used only to
# validate the top WFO_HOLDOUT_TOP_N in-sample candidates before one is saved.
WFO_HOLDOUT_FRAC       = 0.3   # Fraction of WFO_TRAIN_BARS reserved for validation
WFO_HOLDOUT_TOP_N      = 8     # How many top in-sample combos get OOS-checked
WFO_HOLDOUT_MIN_TRADES = 3     # Minimum holdout trades for a combo to be trusted

# ── Adaptive parameter tuning ─────────────────────────────────────────────────
# Adjusts CCI threshold, EMA touch band, and MACD bar count per pair based on
# recent win rate. See engine/adaptive_params.py for tier definitions.
ADAPTIVE_PARAMS_ENABLED  = False  # Disabled 2026-08-02: was fighting WFO for the same params. WFO is the sole optimizer now.
ADAPTIVE_LOOKBACK_TRADES = 20     # How many recent closed trades to measure win rate from
ADAPTIVE_REFIT_EVERY_N   = 5      # Minimum new trades before recalculating thresholds

# ── ML score boost ────────────────────────────────────────────────────────────
# confluence_scorer.score_signal() boosts the score ×1.10 when ml_win_prob > 0.65
# (and penalizes ×0.70 when < 0.35), but ml_win_prob was always None at scoring
# time — the real prediction was computed after the score, only for the post-hoc
# block gate. Off by default pending backtest validation (2026-07-20), same
# convention as ATR_TRAILING_ENABLED: this changes live trigger rate/frequency,
# so it shouldn't flip on without a backtest comparison first.
ML_SCORE_BOOST_ENABLED = False

CCI_PERIOD  = 20
MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIGNAL = 9

# Oanda — auto-selects practice vs live based on MODE
OANDA_API_KEY    = os.getenv("OANDA_LIVE_API_KEY" if MODE == "live" else "OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_LIVE_ACCOUNT_ID" if MODE == "live" else "OANDA_ACCOUNT_ID")
OANDA_ENV        = "live" if MODE == "live" else "practice"

# Telegram notifications (leave blank to disable)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")
