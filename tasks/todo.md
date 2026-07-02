# Promote EUR_AUD to active

**Goal:** Promote EUR_AUD from watch to active (trades will actually open, not
just be logged) based on a fresh same-day backtest: 42 trades, WR=33%, PF=1.42,
PnL=+$39.35, MaxDD=8.4% (3500 M30 bars, current live config: ADX(28) gate,
04:00–17:00 UTC session). Second candidate pairs (GBP_USD PF=1.11/46 trades,
EUR_CHF PF=1.70/10 trades) were considered and explicitly held back by the
user — GBP_USD's edge is too thin, EUR_CHF's sample too small to trust yet.

**Context:** signals were triggering infrequently with only 2 active pairs
(USD_CAD, NZD_USD). Rather than loosen the ADX/session gates (which were
added specifically to kill false signals), widening the pair roster with a
pair that already has a proven backtested edge increases signal frequency
without reintroducing the noise those gates were built to filter.

## Todo

- [x] `config.py`: moved `"EUR_AUD"` from `FOREX_WATCH` to `FOREX_PAIRS`
- [x] `config.py`: updated the comment blocks above both lists with today's
      fresh backtest numbers for all 7 pairs (2 active + 5 watch), not just
      EUR_AUD — the old comments were a day stale
- [x] `python -m pytest -q` — 177 passed, same 10 pre-existing Alpaca
      failures, no regressions
- [x] Rewrote `project_pair_config.md` (was dated 06-29 with a different
      session window, actively misleading) with the current roster, a
      documented PF-over-WR decision framework, and the held-back
      GBP_USD/EUR_CHF rationale for future reference

## Review

### What changed
`config.py` only: `EUR_AUD` moved from `FOREX_WATCH` to `FOREX_PAIRS`
(active — trades will now actually open for it, not just log), plus comment
updates reflecting a same-day fresh backtest re-run for every pair on both
lists (not reused from memory, since a prior comparison this session showed
GBP_USD's PnL sign flipping between the 07-01 and 07-02 backtest runs).

### Why EUR_AUD and not GBP_USD/EUR_CHF
All three watch pairs are net-positive on a fresh 3500-bar M30 backtest
under the current live gates (ADX 28, 04:00–17:00 UTC session). EUR_AUD won
on both PnL (+$39.35, best of the three) and sample size (42 trades — large
enough to trust). GBP_USD has more trades (46) but a thinner edge (PF=1.11,
barely above breakeven); EUR_CHF has the best ratio (PF=1.70) but too few
trades (10) to know if that holds up. User explicitly chose to hold both
back rather than promote on frequency alone — noted in memory as the
current decision framework (PF over win rate, sample size matters as much
as the ratio) so this doesn't need to be re-litigated next session.

### Security review
Pure data/config change — no new code paths, no user input, no secrets
touched. `EUR_AUD` already had a live OANDA connector fetch path (it was on
the watch list, meaning signals were already being fetched/scored/logged
for it); this change only flips whether `paper_trader` actually opens a
position when it fires. No new attack surface.

### Verification
`python -m pytest -q`: 177 passed / 10 pre-existing unrelated failures.
Config change takes effect on the bot's next restart (`python main.py`) or
next scheduled scan — did not restart the user's live paper-trading process
as part of this change since that's a live-system action outside the scope
of what was asked (config edit + verification only).
