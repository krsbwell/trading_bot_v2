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
#
# 2026-08-16 — completed the holdout treatment the 08-13 decision left
# unfinished (see tasks/todo.md): AUD_JPY promoted off watch onto
# breakout_retest@H4 (holdout PF 2.81, an exact match run 4 days apart —
# the cleanest result of that pass). EUR_AUD promoted off watch (was
# paused 2026-08-05) onto trend_retest@H4 (holdout PF 1.98 on the best
# trend_retest sample size of the batch, improved since 08-12's 1.61).
#
# 2026-08-22 — EUR_AUD removed again (back to FOREX_WATCH). The H4-vs-M30
# revert backtest (see PRIMARY_TF_PER_PAIR) found trend_retest isn't
# profitable for this pair on either timeframe (H4: PF 1.01 on 4 trades,
# essentially breakeven; M30: PF 0.70, -$13) — this isn't a timeframe
# problem, it's a "no validated edge on this pair right now" result, same
# conclusion type as EUR_JPY above.
#
# 2026-08-23 — EUR_AUD back in, running the new ML-driven adaptive
# strategy (see ADAPTIVE_STRATEGY / STRATEGY_OVERRIDE below), not
# trend_retest again. Deliberately picked because nothing else is
# currently live on it — starting the adaptive strategy's real forward-
# testing on a pair with no existing edge to disrupt. Still MODE="demo"
# (virtual OANDA money), no real capital at risk. See tasks/todo.md.
#
# 2026-09-28 — AUD_JPY and EUR_AUD paused (user-approved), moved to
# FOREX_WATCH. AUD_JPY: PF 0.70 after spread in a 3000-bar backtest (loses
# even in simulation). EUR_AUD: its adaptive strategy's entries come from the
# ML model whose P(win) was found anti-predictive (see ML_GATE_ENABLED), and
# it fired 0 trades in backtest. Re-add only with a backtest that holds up
# after spread.
#
# 2026-09-28 — FULL RE-SCREEN of all 7 majors + 21 minors on the corrected
# backtester (spread included, live-sized 250-candle window, no H4
# look-ahead), ~1.5 years of M30 (20000 bars), breakout_retest vs
# trend_retest. Criteria fixed before running: strategy picked on the FIRST
# half only; pass = 2nd-half (out-of-sample) PF >= 1.3 with >= 8 trades,
# 1st-half PF >= 1.1, max DD <= 10%. 3 of 28 passed. Per the user, every
# failure is REMOVED (not watched) — see the rejected list below.
#   GBP_USD trend_retest    1st PF 1.16 (22) | 2nd PF 2.25 (27) | DD 7.2%
#   EUR_USD trend_retest    1st PF 1.17 (22) | 2nd PF 1.55 (16) | DD 7.2%
#   AUD_JPY breakout_retest 1st PF 1.53 (30) | 2nd PF 1.34 (39) | DD 7.7%
#
# 2026-10-07 — all 25 rejected pairs added back (user's explicit call,
# overriding the 09-28 screen above) to raise trade throughput for
# statistical sample size; MODE is still "demo" (OANDA practice account,
# no real capital). The 09-28 PF/DD numbers above and in the "Rejected"
# comment below are UNCHANGED and still apply to these pairs — re-adding
# them did not re-validate them, it just lets them trade again.
# KNOWN GAP: none of these 25 have a STRATEGY_OVERRIDE entry, so they run
# on the default strategy_ema_cci_macd ("EMA-bounce") — the one strategy
# the 2026-08-13 exhaustive WFO search found NO live edge for on ANY pair
# tested. The 09-28 screen's actual per-pair winning strategy (breakout_
# retest vs trend_retest) wasn't preserved to disk, so it couldn't be
# restored here without re-running the full screen. If/when trade volume
# on these pairs is reviewed, re-running that screen to assign each pair
# its better-of-two strategy (instead of the EMA-bounce default) is the
# logical next step.
FOREX_PAIRS  = ["GBP_USD", "EUR_USD", "AUD_JPY",
                 "AUD_CAD", "AUD_CHF", "AUD_NZD", "AUD_USD",
                 "CAD_CHF", "CAD_JPY", "CHF_JPY",
                 "EUR_AUD", "EUR_CAD", "EUR_CHF", "EUR_GBP", "EUR_JPY", "EUR_NZD",
                 "GBP_AUD", "GBP_CAD", "GBP_CHF", "GBP_JPY", "GBP_NZD",
                 "NZD_CAD", "NZD_CHF", "NZD_JPY", "NZD_USD",
                 "USD_CAD", "USD_CHF", "USD_JPY"]

# Per-pair strategy override — a pair not listed here uses the default
# (strategy_ema_cci_macd / "EMA-bounce"). breakout_retest and trend_retest
# are also wired into signal_engine.py (see engine/strategy_trend_follow.py
# — shelved, backtest-only, never wired live; see
# project_trend_follow_experiment memory for why).
#
# trend_retest went live for the first time ever 2026-08-16 (CHF_JPY,
# EUR_AUD) — see TREND_RETEST_PARAMS_PER_PAIR below for its per-pair tuned
# knobs and tasks/todo.md for the two independent holdout runs (08-12 and
# 08-16, 4 days apart) that agreed on both pairs before this switch.
STRATEGY_OVERRIDE = {
    # 2026-09-28 re-screen (see FOREX_PAIRS comment); earlier routing
    # history for removed pairs is in git (commit cc94ff4 and before).
    "GBP_USD":  "trend_retest",      # was breakout_retest (2nd-half PF 2.26 but DD 10.8% >
                                      # 10% cap and 1st-half PF 1.04 < 1.1); trend_retest
                                      # won the 1st half and passed out-of-sample
    "EUR_USD":  "trend_retest",
    "AUD_JPY":  "breakout_retest",   # 2026-10-07 — user reviewed Phase 1's fire-rate/
                                      # trade-count numbers (breakout_retest: 14.3% fire,
                                      # only 2 historical trades vs ema_bounce's 21.4%/42
                                      # and trend_retest's 15.5%/7) and decided to
                                      # deprioritize breakout_retest going forward:
                                      # EMA Bounce primary, Trend-Retest secondary. AUD_JPY
                                      # is deliberately kept here rather than defaulted to
                                      # ema_bounce or moved to trend_retest — it's one of
                                      # only 3 pairs (of 28) that passed the 09-28 re-screen,
                                      # specifically ON breakout_retest (1st PF 1.53, 2nd PF
                                      # 1.34, DD 7.7%); neither of the other two strategies
                                      # has ever been validated for this pair specifically,
                                      # so reassigning it would trade a proven combination
                                      # for an untested one. Since AUD_JPY is already the
                                      # only pair using breakout_retest, "deprioritizing" it
                                      # is this comment, not a code change — strategy_
                                      # breakout_retest.py is untouched and still live for
                                      # this one pair. Don't add new pairs to it without a
                                      # fresh backtest; see tasks/todo.md "Phase 2C".
}

# Per-pair tuned knobs for strategy_trend_retest.py (h4_bias_period,
# retest_band_mult — see engine/strategy_trend_retest.py's adaptive.get()
# calls). Not WFO-tunable — WFO only searches ema_bounce's params and
# already skips any STRATEGY_OVERRIDE pair. These combos came from the
# 08-12 fair-tuning pass (16-combo grid search on a FIT window only,
# holdout never influenced the pick) and were re-confirmed identical on
# 2026-08-16's independent re-run. Merged into the live `adaptive` dict in
# engine/signal_engine.py and into backtest.runner.run_backtest()'s
# default-resolution path (so the dashboard's Backtest/Walk-Forward
# buttons reflect these too, not trend_retest's untuned defaults) — same
# "audit every call site" discipline as the 08-13 PRIMARY_TF_PER_PAIR
# rollout. See tasks/todo.md 2026-08-16.
TREND_RETEST_PARAMS_PER_PAIR: dict = {
    "CHF_JPY": {"h4_bias_period": 20, "retest_band_mult": 0.30},
    "EUR_AUD": {"h4_bias_period": 20, "retest_band_mult": 0.45},
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
# USD_CAD: paused from FOREX_PAIRS 2026-08-05 — see comment above
# FOREX_PAIRS. Not a rejection, a hold pending fresh WFO validation.
# Re-checked again 2026-08-16 (trend_retest-tuned holdout PF 2.02, but on
# only 4 holdout trades — too thin to act on yet, stays watch).
# EUR_JPY: paused from FOREX_PAIRS 2026-08-13 — see comment above FOREX_PAIRS.
# No strategy (EMA-bounce, breakout_retest, trend_retest as of 08-16) or
# timeframe (M30/H1/H4/M15) tested has shown any validated edge for this
# pair specifically; signals still shown, no trades open.
# AUD_JPY: promoted to FOREX_PAIRS 2026-08-16 — see FOREX_PAIRS comment
# above. EUR_AUD: promoted 2026-08-16, paused 2026-08-22, back in
# 2026-08-23 on the adaptive strategy — see FOREX_PAIRS comment above.
FOREX_WATCH  = []   # 2026-09-28: every watched pair failed the re-screen -> removed (user's rule)

# Rejected / removed — losing money, not watched (re-test only if the strategy or gates change)
# Rejected 2026-09-28 re-screen (corrected backtester, ~1.5y M30, spread incl.;
# best strategy by 1st half -> 2nd-half PF / DD). Includes 3 pairs that were
# LIVE until then: GBP_CAD, NZD_USD, CHF_JPY.
#   AUD_CAD 0.71/23.7%  AUD_CHF 0.51/19.2%  AUD_NZD 0.82/12.2%  AUD_USD 0.82/14.1%
#   CAD_CHF 0.76/10.5%  CAD_JPY 0.58/14.8%  CHF_JPY 0.94/24.1%  EUR_AUD 0.68/11.1%
#   EUR_CAD 0.74/15.3%  EUR_CHF 0.90/9.2%   EUR_GBP 1.07/14.0%  EUR_JPY 0.84/10.4%
#   EUR_NZD 0.72/16.8%  GBP_AUD 0.55/11.0%  GBP_CAD 1.26/26.1%  GBP_CHF 0.49/16.0%
#   GBP_JPY 0.71/16.4%  GBP_NZD 0.68/18.9%  NZD_CAD 1.09/19.6%  NZD_CHF 0.43/12.4%
#   NZD_JPY 1.42/16.6% (1st half 0.38)       NZD_USD 1.38/14.2% (1st half 0.63)
#   USD_CAD 1.26/8.2% (1st half 1.07)        USD_CHF 0.75/14.3%  USD_JPY 0.73/10.8%
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
# EUR_AUD's old H1 override (2026-07-23, EMA-bounce/M30-era finding) was
# replaced 2026-08-16 by "D" below — EUR_AUD moved to H4 primary running
# trend_retest, a completely different strategy+timeframe, so the old
# M30-primary/H1-confirm comparison no longer applies. See FOREX_PAIRS
# comment above.
CONFIRM_TF_PER_PAIR: dict = {
    # GBP_CAD is the only pair still on H4 primary (see PRIMARY_TF_PER_PAIR
    # 2026-08-22 revert) — H4 can't confirm itself, so it keeps Daily as
    # its confirm/bias TF. NZD_USD/CHF_JPY/AUD_JPY/EUR_AUD removed
    # 2026-08-22 — back to M30 primary, so they fall back to the global
    # H4 confirm TF below, same pairing the 2026-08-22 revert was
    # backtested with.
    "GBP_CAD": "D",
}


def confirm_tf_for(pair: str) -> str:
    """Resolve the confirm timeframe for a pair — CONFIRM_TF_PER_PAIR wins if set."""
    return CONFIRM_TF_PER_PAIR.get(pair, TIMEFRAMES["confirm"])


# Per-pair override of TIMEFRAMES["primary"] — a pair not listed here uses
# the global primary TF above (M30). Same pattern as CONFIRM_TF_PER_PAIR.
#
# NZD_USD/GBP_CAD/CHF_JPY moved to H4 2026-08-13, AUD_JPY/EUR_AUD 2026-08-16
# (full history in tasks/todo.md) — that move was itself real and
# backtest-driven (EMA-bounce/M30 had no holdout-validated edge on any of
# these pairs), but H4 also means 8x fewer decision points than M30, and
# after a week+ of live silence (partly OANDA API outage / crash-loop —
# see tasks/todo.md 2026-08-21 — but not entirely) it was worth checking
# whether that tradeoff was actually still worth it.
#
# 2026-08-22 — reverted per-pair based on a real backtest, each pair on
# its ACTUAL live strategy (breakout_retest/trend_retest, not EMA-bounce),
# matched ~180-day window comparing H4/D (then-current) vs M30/H4:
#   NZD_USD  H4: 6 trades  PF 1.93  +$14  1%DD  |  M30: 29 trades  PF 1.76  +$53  5%DD  -> M30 wins (more edge AND more $)
#   AUD_JPY  H4: 6 trades  PF 1.89  +$9   3%DD  |  M30: 39 trades  PF 1.28  +$30  5%DD  -> M30 wins
#   CHF_JPY  H4: 2 trades  PF 1.91  +$5   1%DD  |  M30: 30 trades  PF 1.10  +$8   5%DD  -> H4 sample too thin (2 trades) to trust; M30 wins on real sample size
#   GBP_CAD  H4: 5 trades  PF 1.45  +$7   2%DD  |  M30: 42 trades  PF 0.54  -$55  13%DD -> H4 wins clearly, stayed on H4
#   EUR_AUD  H4: 4 trades  PF 1.01  +$0.06 2%DD |  M30: 17 trades  PF 0.70  -$13  6%DD  -> neither profitable, paused (see FOREX_WATCH) rather than forced onto either
# NZD_USD/AUD_JPY/CHF_JPY reverted to M30 (removed below, falls back to
# TIMEFRAMES["primary"]). GBP_CAD stays H4 — M30 is measurably worse for
# it, not just less-tested. EUR_AUD removed from FOREX_PAIRS entirely.
PRIMARY_TF_PER_PAIR: dict = {
    "GBP_CAD": "H4",
}


def primary_tf_for(pair: str) -> str:
    """Resolve the primary (signal-generation) timeframe for a pair —
    PRIMARY_TF_PER_PAIR wins if set."""
    return PRIMARY_TF_PER_PAIR.get(pair, TIMEFRAMES["primary"])

RISK_PER_TRADE       = 0.01    # 1% of account balance — hard rule
# Raised 3->5 on 2026-09-16, alongside MAX_PYRAMID_PER_PAIR=3 below: at
# equal values, one pair fully pyramiding used the ENTIRE global budget
# and blocked every other pair from trading at all — the opposite of the
# diversification this cap exists for, across the 6 active pairs. 5 lets
# one pair pyramid to its own cap of 3 while still leaving 2 slots free
# for other pairs. Trade-off: raises worst-case simultaneous risk (all
# open trades hitting SL the same day) from 3% to 5% of account at
# RISK_PER_TRADE=1% each — MAX_DAILY_DRAWDOWN below is a REACTIVE halt on
# realized losses, not a preventive cap on simultaneous open exposure, so
# this is a real increase in tail risk, not just bookkeeping. Acceptable
# for now since MODE="demo" (virtual money) — revisit before ever going live.
MAX_OPEN_TRADES      = 5
MAX_DAILY_DRAWDOWN   = 0.03    # 3% — halt new signals if breached
MIN_CONFLUENCE_SCORE = 55      # Minimum score to trigger a trade (slider-adjustable 40–90)
ALERT_DELAY_SECONDS  = 60      # Seconds the signal popup stays visible (trade opens immediately on signal)
TRAILING_STOP_PIPS   = 15     # Trail SL 15 pips after TP2 is hit (0 = disabled)

# ── ATR-adaptive trailing stop ────────────────────────────────────────────────
# Replaces the fixed TRAILING_STOP_PIPS distance with ATR(period)*multiplier
# when enabled, so the trail widens/tightens with volatility instead of using
# one static pip distance for every market condition.
# Enabled 2026-09-16 (was off by default) — part of the trend-pyramiding
# change below: letting the final slice of a trade ride further with
# volatility, instead of exiting at a fixed TP3, is what actually captures
# more of an extended trend move within one trade. See tasks/todo.md.
ATR_TRAILING_ENABLED    = True
ATR_TRAILING_MULTIPLIER = 2.0
ATR_TRAILING_PERIOD     = 14
# 2026-09-28: let the final 25% run on the ATR trail with NO TP3 cap (live
# CHF_JPY moves of 383/433 pips were cut at TP3). Off until backtested.
RUNNER_NO_TP3_CAP       = False

# ── Duplicate trade protection / pyramiding ───────────────────────────────────
# Changed 2026-09-16 — see tasks/todo.md. User's complaint: in a sustained
# trend, a genuinely new high-scoring breakout-retest setup further along
# the same move was being blocked outright just because an earlier trade on
# that pair was still open, then blocked again for 4h after it closed.
#
# ALLOW_MULTIPLE_PER_PAIR=True on its own does NOT enable pyramiding by
# itself — risk_manager.validate_pre_trade() had its own hardcoded
# "pair already has an open trade" block that didn't consult this flag at
# all (a second, independent duplicate-guard). Fixed alongside this change
# so the flag actually does what its name says — see validate_pre_trade().
#
# PYRAMID_MIN_SCORE: an add-on entry on an already-open pair must clear this
# (stricter than the normal MIN_CONFLUENCE_SCORE=55 floor) — user wants to
# start conservative here. Same-direction-only (user's explicit choice) —
# never adds an opposite-direction position on a pair that already has one
# open, enforced in validate_pre_trade().
#
# MAX_PYRAMID_PER_PAIR: added 2026-09-16 (same day, follow-up request) — user
# initially chose no per-pair cap (relying on MAX_OPEN_TRADES alone), then
# asked to cap it explicitly at 3 consecutive trades on one pair. This is
# why MAX_OPEN_TRADES above was raised 3->5 the same day: at equal values,
# a fully pyramided pair used the whole global budget and shut every other
# pair out. Now one pair can still pyramid to this cap while 2 slots stay
# free for the rest of the roster.
#
# 2026-09-27: PYRAMID_MIN_SCORE 65 -> 55 (user's call). At 65 only 4 of 106
# triggered signals qualified, so pyramiding fired once in 8 weeks, and the
# score has no measurable link to win rate anyway. 55 = the normal entry floor,
# i.e. any same-direction signal that could open a trade can now add on.
# REVIEW CHECKPOINT — Sunday 2026-10-04 (user asked for a 7-day review):
# count add-on entries since 09-27 (same pair + direction, opened while
# another was open) and their P&L vs single entries; decide keep / revert.
ALLOW_MULTIPLE_PER_PAIR    = True   # Allow multiple open positions for the same pair
PYRAMID_MIN_SCORE          = 55     # Min score for an add-on entry (was 65 until 2026-09-27)
MAX_PYRAMID_PER_PAIR       = 3      # Max concurrent same-direction positions on one pair
TRADE_COOLDOWN_HOURS       = 0      # Min hours after a pair's trade closes before re-entry (was 4)
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

# Breakout-retest strict entry (user's rules, 2026-09-27): require retest +
# break held + confirming candlestick + EMA34/EMA100 crossed in the trade
# direction, instead of any 2 of 4 conditions earning points. Off until
# backtested — see tasks/todo.md 2026-09-27 step 9.
BREAKOUT_STRICT_ENTRY = False

# MA ribbon / momentum knobs (2026-09-28, user asked: EMA34 -> 25? EMA -> SMA?
# replace the oscillator?). Defaults = original behavior; changed only after
# a spread-adjusted backtest (tasks/todo.md step 13).
MA_FAST_PERIOD    = 34       # fast line of the 34/100/200 ribbon + MA-cross check
MA_SLOW_TYPE      = "ema"    # "ema" | "sma" for the 200 line
BREAKOUT_MOMENTUM = "macd"   # "macd" (histogram sign) | "rsi50" (RSI14 vs 50)

# Sequenced entry (2026-09-28): retest accepted on any candle after the break
# (not only the entry candle); add-ons additionally need the EMA cross. Both
# off until backtested — tasks/todo.md round 3 step 4.
SEQUENCED_RETEST         = False
PYRAMID_REQUIRE_MA_CROSS = False

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


# Volatility-scaled floor on top of the fixed one (2026-09-28, user's 2.0x):
# SL distance >= max(MIN_SL_PIPS, MIN_SL_ATR_MULT x ATR14). 0 = fixed floor
# only. Removing the floor entirely (MIN_SL_PIPS=0) was backtested worse on
# every pair (tasks/todo.md 2026-09-27 step 6); this widens stops in volatile
# conditions instead of narrowing them in quiet ones.
MIN_SL_ATR_MULT = 2.0   # applied 2026-09-28 — backtest: GBP_CAD PF 1.88->3.32, CHF_JPY 3.72->2.78, GBP_USD/NZD_USD unchanged; portfolio net $166->$183

# ── Backtest spread model (2026-09-28) ───────────────────────────────────────
# backtest/runner.py never modeled spread, so every backtest PF was
# optimistic (more so for tight stops) and losing pairs got promoted.
# Longs now fill at entry + spread, shorts at entry - spread. Values are
# typical OANDA London-session spreads in pips (the bot trades 04-17 UTC).
# Measured 2026-09-28 04:15 UTC (Asian session, wider) for comparison:
# GBP_CAD 4.9, CHF_JPY 3.1, NZD_USD 1.6, GBP_USD 0.4. Re-measure during
# London and tighten/loosen if these turn out off.
BACKTEST_SPREAD_ENABLED = True

# Candles the strategy sees per scan — live (signal_engine) AND backtest use
# the same window so they compute the same pivots/structure/EMAs. Before
# 2026-09-28 the backtest gave strategies ALL history up to each bar.
LIVE_CANDLE_WINDOW = 250
SPREAD_PIPS: dict = {
    "EUR_USD": 1.0, "GBP_USD": 1.4, "USD_JPY": 1.2, "USD_CHF": 1.6,
    "AUD_USD": 1.3, "USD_CAD": 1.8, "NZD_USD": 1.8,
    "EUR_GBP": 1.4, "EUR_JPY": 1.8, "EUR_CHF": 1.9, "EUR_AUD": 2.2,
    "EUR_CAD": 2.4, "EUR_NZD": 3.5, "GBP_JPY": 2.6, "GBP_CHF": 2.8,
    "GBP_AUD": 3.0, "GBP_CAD": 3.0, "GBP_NZD": 4.5, "AUD_JPY": 1.8,
    "AUD_CAD": 2.2, "AUD_CHF": 2.0, "AUD_NZD": 2.5, "CAD_JPY": 2.2,
    "CAD_CHF": 2.2, "CHF_JPY": 2.8, "NZD_JPY": 2.2, "NZD_CAD": 2.8,
    "NZD_CHF": 2.6,
}
SPREAD_PIPS_DEFAULT = 3.0   # anything not listed (e.g. XAU_USD needs its own)


def spread_pips_for(pair: str) -> float:
    return SPREAD_PIPS.get(pair, SPREAD_PIPS_DEFAULT)



def min_sl_distance(pair: str, pip: float, atr_val: float) -> float:
    """Minimum SL distance in PRICE units — fixed pip floor or ATR multiple, whichever is larger."""
    atr_part = MIN_SL_ATR_MULT * atr_val if atr_val and atr_val == atr_val else 0.0
    return max(pip * min_sl_pips_for(pair), atr_part)

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

# Finnhub news-event filter removed 2026-08-14 — the configured key's plan
# didn't include the economic-calendar endpoint (403, confirmed live), so
# it never contributed a real check. ForexFactory (connectors/
# forexfactory_connector.py, free, no key) already covers the real
# blackout window. See tasks/todo.md.

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
WFO_REFIT_DAYS = 7      # LEGACY (Phase 5D-5): no longer read -- freshness is weekly-cycle based, see below

# Phase 5D-5 (2026-10-27): weekly-cycle freshness. A pair is eligible again once its
# `fitted_at` is earlier than the START of the current scheduled cycle (the most recent
# WFO_CYCLE_* instant in WFO_SCHEDULE_TZ). The old `(now - fitted_at).days >= 7` skipped
# every pair saved by the previous batch (fitted_at is written AFTER the trigger, so the
# age at the next trigger is always just under 7 days) and breaks on DST weeks.
# WFO_CYCLE_* MUST match main.py's CronTrigger (day_of_week="sun", hour=2, minute=0);
# tests/test_wfo_freshness.py fails if they drift. WFO_SCHEDULE_TZ is explicit on purpose;
# the cron itself uses the machine zone, so a startup warning fires if they ever differ.
WFO_SCHEDULE_TZ        = "America/New_York"
WFO_CYCLE_DAY_OF_WEEK  = 6      # Monday=0 ... Sunday=6
WFO_CYCLE_HOUR         = 2
WFO_CYCLE_MINUTE       = 0
WFO_REFIT_CYCLES       = 1      # Scheduled cycles between re-fits per pair (integer >= 1;
                                # invalid/missing -> 1 with a warning). Replaces WFO_REFIT_DAYS.

# Fit/holdout split added 2026-08-11 — see engine/wfo_optimizer.py module
# docstring for why: picking the single best-in-sample combo out of 216 with
# no out-of-sample check is overfitting by construction (GBP_CAD's live params
# were an 83%-WR/6-trade in-sample fluke). The last WFO_HOLDOUT_FRAC of
# WFO_TRAIN_BARS is held out, untouched by the grid search, and used only to
# validate the top WFO_HOLDOUT_TOP_N in-sample candidates before one is saved.
WFO_HOLDOUT_FRAC       = 0.3   # Fraction of WFO_TRAIN_BARS reserved for validation
WFO_HOLDOUT_TOP_N      = 8     # How many top in-sample combos get OOS-checked
# 2026-10-27 (Phase 5D-2): holdout eligibility is two explicit checks made BEFORE
# ranking -- completed holdout trades >= MIN_TRADES AND holdout profit factor >=
# MIN_PF. (Before: MIN_TRADES was 3 but a hidden 5-trade floor inside the score
# applied anyway, and "PF > 0.5" was documented but only "any winner" enforced.)
# PF == 1.0 is eligible; it is a minimum bar, not evidence of a reliable edge.
# `total_trades` counts completed trades including pyramid add-ons and the
# end-of-data forced close; entry_signals/addon_entries/forced_closes are logged
# separately and gate nothing.
WFO_HOLDOUT_MIN_TRADES = 5     # Minimum COMPLETED holdout trades (was 3, effectively 5)
WFO_HOLDOUT_MIN_PF     = 1.0   # Minimum holdout profit factor (new; PF >= this is eligible)
# 2026-10-27 (Phase 5D-1B): the holdout combos are backtested on the whole
# WFO_TRAIN_BARS window with eval_start at the holdout boundary, so the fit
# period only serves as indicator / confirm-TF context. This is a WFO-only
# sanity guard, NOT a strategy signal threshold: if fewer than this many
# confirm-TF candles had actually CLOSED (by timestamp) before the first
# holdout bar, the pair's refit is skipped instead of scoring a cold-started
# holdout. 100 = EMA/MACD/ADX convergence and detect_pivots' 100-candle scan.
# 0 disables the guard.
WFO_CONTEXT_CONFIRM_CANDLES = 100

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

# ML block gate (main.py: blocks score<65 signals when P(win) < 0.50).
# OFF 2026-09-27: the model never trained on a single demo trade (id-mismatch
# bug, 07-31 → 09-27), and on 275 live+shadow signals its P(win) was
# NEGATIVELY correlated with outcome (top third 37% WR vs bottom third 48%),
# so it was blocking the slightly better setups. P(win) is still computed and
# logged per signal (ml_win_prob_at_entry). Re-enable only once the model
# has >= 100 real (source="live") trade outcomes AND its P(win) beats the
# base win rate on trades it wasn't trained on.
ML_GATE_ENABLED = False

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

# ── Adaptive strategy (engine/strategy_adaptive.py) ─────────────────────────
# Off by default — see tasks/todo.md 2026-08-21 "Adaptive AI/ML Strategy —
# Integration Plan". Even when enabled=True, no pair actually routes to it
# until "adaptive" is added to STRATEGY_OVERRIDE above, same rollout
# pattern trend_retest used (backtest-only for a month before its first
# live pair). regime_min_confidence/min_confidence gate the strategy's own
# score, independent of the global MIN_CONFLUENCE_SCORE all strategies
# still go through in engine/signal_engine.py.
ADAPTIVE_STRATEGY = {
    "enabled":               True,   # 2026-08-23 — turned on to start real forward-testing,
                                      # see STRATEGY_OVERRIDE["EUR_AUD"] below and tasks/todo.md.
                                      # Still MODE="demo" (virtual OANDA money), same as every
                                      # other live pair — no real capital at risk.
    "atr_period":             14,
    "atr_sl_mult":            1.5,
    "atr_tp_mult":            3.0,
    "min_confidence":         0.55,   # engine.decision.Decision.confidence floor to score >0
    "regime_min_confidence":  0.4,    # below this, regime is too uncertain to weight the score
}

# Per-pair override of ADAPTIVE_STRATEGY's knobs — same pattern as
# TREND_RETEST_PARAMS_PER_PAIR. Empty until a pair has actually been
# backtested/tuned on this strategy.
ADAPTIVE_STRATEGY_PARAMS_PER_PAIR: dict = {}


def adaptive_strategy_params_for(pair: str) -> dict:
    """ADAPTIVE_STRATEGY merged with any per-pair override — same
    resolution pattern as confirm_tf_for/primary_tf_for above."""
    return {**ADAPTIVE_STRATEGY, **ADAPTIVE_STRATEGY_PARAMS_PER_PAIR.get(pair, {})}


# ── Learning lifecycle / model registry / research / agents ────────────────
# All off/conservative by default, same rollout-safety rule as
# ADAPTIVE_STRATEGY above. See tasks/todo.md 2026-08-21 Phase 4.
LEARNING_ADAPTIVE = {
    "min_samples_for_candidate":       50,    # mirrors PatternLearner.MIN_SAMPLES
    "min_samples_for_rl_train_step":   500,   # see learning/reinforcement_learning.py
    "auto_advance_candidate_stages":   False,  # production promotion is always a manual call regardless
}

MODEL_REGISTRY = {
    "min_accuracy_to_validate": 0.55,
    "keep_retired_versions":    True,
}

# research/ package — reverses the Finnhub-removal decision from
# e75b937 (this month), so it stays off unless deliberately turned on, and
# news_source=None makes research/news_collector.py always return []
# regardless of the enabled flag until a real source is configured.
RESEARCH = {
    "enabled":              False,
    "news_source":           None,   # e.g. "finnhub" — see research/news_collector.py
    "news_blackout_enabled": False,  # opt-in reuse of connectors.forexfactory_connector.is_news_blackout
}

AGENTS_CONFIG = {
    "orchestrator_also_execute": False,  # read by callers of agents.orchestrator_agent, not by that module itself
    "drift_detector_enabled":    False,
}


def _load_yaml_overrides() -> None:
    """Merges configs/*.yaml into the dicts above, if present, as the
    human-editable input the source design doc asked for — config.py
    remains the single runtime source of truth either way (this only
    overrides values already defined above, never adds new unknown keys
    silently). Requires pyyaml (see requirements.txt); if it isn't
    installed, this logs once and leaves every default above untouched —
    never a hard failure, since none of this is required for the bot to
    run. NOTE: the directory is configs/, not config/ — a config/ package
    next to this config.py module would collide with `import config`
    everywhere else in this codebase.
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)
    _configs_dir = os.path.join(os.path.dirname(__file__), "configs")

    try:
        import yaml as _yaml
    except ImportError:
        if os.path.isdir(_configs_dir):
            _logger.warning(
                "configs/*.yaml present but pyyaml isn't installed — "
                "run `pip install -r requirements.txt`. Using code defaults."
            )
        return

    _targets = {
        "strategy.yaml": (ADAPTIVE_STRATEGY, {"enabled", "atr_period", "atr_sl_mult",
                                               "atr_tp_mult", "min_confidence", "regime_min_confidence"}),
        "learning.yaml": (LEARNING_ADAPTIVE, set(LEARNING_ADAPTIVE.keys())),
        "models.yaml":   (MODEL_REGISTRY,    set(MODEL_REGISTRY.keys())),
        "agents.yaml":   (AGENTS_CONFIG,     set(AGENTS_CONFIG.keys())),
    }
    for filename, (target_dict, allowed_keys) in _targets.items():
        path = os.path.join(_configs_dir, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = _yaml.safe_load(f) or {}
        except Exception as exc:
            _logger.warning("Failed to load %s — keeping defaults: %s", path, exc)
            continue
        for key, value in loaded.items():
            if key in allowed_keys:
                target_dict[key] = value
            else:
                _logger.warning("configs/%s: unknown key %r ignored", filename, key)


_load_yaml_overrides()
