"""
Generates actionable suggestion cards from the signal log.
Called automatically after every 10 closed trades.

Card types:
  boost    — something that's working well, do more of it
  warning  — something underperforming, consider pulling back
  optimize — a parameter tweak that could improve win rate
"""
import logging
import os

import pandas as pd

logger = logging.getLogger(__name__)

_ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(_ROOT, "data", "signal_log.csv")

_TRIGGER_EVERY = 10    # run after every N closed trades
_last_run_count = 0


def should_run(log_path: str = LOG_PATH) -> bool:
    """Return True when 10+ new trades have accumulated since the last run."""
    global _last_run_count
    df = _load_closed(log_path)
    if df is None:
        return False
    return (len(df) - _last_run_count) >= _TRIGGER_EVERY


def generate_suggestions(
    min_trades: int = 5,
    log_path: str = LOG_PATH,
) -> list[dict]:
    """
    Analyse signal_log.csv and return a list of suggestion dicts.
    Each dict: {type: "boost|warning|optimize", text: str, dismissible: True}

    Analyses performed:
      1. Pattern win rate  — boost if > 70%
      2. Pair win rate     — warning if < 40%
      3. CCI threshold opt — optimize if raising threshold helps ≥ 10%
      4. Session split     — warning if a session has < 35% win rate
      5. Timeframe split   — compare H1 primary vs H4 confirm signal counts
    """
    global _last_run_count

    df = _load_closed(log_path)
    if df is None:
        return []

    _last_run_count = len(df)
    suggestions: list[dict] = []
    base_wr = (df["outcome"] == "win").mean()

    # ── 1. Pattern win rate ───────────────────────────────────────────────────
    if "pattern_name" in df.columns:
        # Each row can have pipe-joined patterns; explode to individual rows
        patterns_df = df.copy()
        patterns_df = patterns_df[patterns_df["pattern_name"].str.len() > 0]
        if len(patterns_df):
            patterns_df = patterns_df.assign(
                pattern=patterns_df["pattern_name"].str.split("|")
            ).explode("pattern")
            for pattern, group in patterns_df.groupby("pattern"):
                if not pattern or len(group) < min_trades:
                    continue
                wr = (group["outcome"] == "win").mean()
                if wr > 0.70:
                    suggestions.append(_card(
                        "boost",
                        f"Pattern '{pattern}' wins {wr:.0%} over {len(group)} trades — prioritize entries showing this pattern.",
                    ))

    # ── 2. Pair win rate ──────────────────────────────────────────────────────
    if "pair" in df.columns:
        for pair, group in df.groupby("pair"):
            if len(group) < min_trades:
                continue
            wr = (group["outcome"] == "win").mean()
            if wr < 0.40:
                suggestions.append(_card(
                    "warning",
                    f"{pair} has only {wr:.0%} win rate over {len(group)} trades — consider pausing or reducing size.",
                ))

    # ── 3. CCI threshold optimisation ────────────────────────────────────────
    if "cci_at_signal" in df.columns:
        cci_vals = pd.to_numeric(df["cci_at_signal"], errors="coerce")
        df_cci   = df[cci_vals.notna()].copy()
        df_cci["cci_abs"] = cci_vals[cci_vals.notna()].abs().values

        for threshold in [110, 120, 130, 140]:
            subset = df_cci[df_cci["cci_abs"] >= threshold]
            if len(subset) < min_trades:
                continue
            wr = (subset["outcome"] == "win").mean()
            if wr - base_wr >= 0.10:
                suggestions.append(_card(
                    "optimize",
                    f"Raising CCI threshold to {threshold} improves win rate by {(wr - base_wr):.0%} "
                    f"({wr:.0%} vs base {base_wr:.0%}) on {len(subset)} trades.",
                ))
                break   # report only the first improvement

    # ── 4. Session split ──────────────────────────────────────────────────────
    if "session" in df.columns:
        for session, group in df.groupby("session"):
            if len(group) < min_trades:
                continue
            wr = (group["outcome"] == "win").mean()
            if wr < 0.35:
                suggestions.append(_card(
                    "warning",
                    f"Session '{session}' has only {wr:.0%} win rate over {len(group)} trades — consider filtering this session.",
                ))
            elif wr > 0.65 and len(group) >= min_trades * 2:
                suggestions.append(_card(
                    "boost",
                    f"Session '{session}' is your best window ({wr:.0%} win rate, {len(group)} trades) — prioritise these hours.",
                ))

    # ── 5. Direction split ────────────────────────────────────────────────────
    if "direction" in df.columns:
        for direction, group in df.groupby("direction"):
            if len(group) < min_trades:
                continue
            wr = (group["outcome"] == "win").mean()
            if wr < 0.35:
                suggestions.append(_card(
                    "warning",
                    f"{direction.upper()} signals are only winning {wr:.0%} over {len(group)} trades — check trend alignment.",
                ))

    return suggestions


def _card(card_type: str, text: str) -> dict:
    return {"type": card_type, "text": text, "dismissible": True}


def _load_closed(log_path: str) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(log_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return None
    closed = df[df["outcome"].isin(["win", "loss"])].copy()
    return closed if len(closed) > 0 else None
