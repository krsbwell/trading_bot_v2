"""
Formats OHLCV data and indicators as plain dicts for TradingView Lightweight Charts v5.
Returned dict goes into chart-data-store and is consumed by dashboard/assets/chart.js.
All timestamps are UNIX seconds (UTC integer).
"""
import pandas as pd

from engine.indicators import ema as calc_ema, cci as calc_cci, macd_histogram
import config

_TF_LABEL   = {"H1": "1H", "H4": "4H", "D": "D"}
_EMA_COLORS = ["#ffd700", "#ff9900", "#ff3366"]


def build_chart_data(
    df: pd.DataFrame,
    pair: str,
    tf: str             = "H1",
    ema_periods: tuple  = (34, 100, 200),
    ema_colors: list    = None,
    ema_widths: list    = None,
    signal_levels: dict = None,   # {entry, sl, tp1, tp2, tp3, direction} or None
    open_trades: list   = None,
) -> dict:
    if ema_colors is None:
        ema_colors = list(_EMA_COLORS)
    while len(ema_colors) < len(ema_periods):
        ema_colors.append(_EMA_COLORS[len(ema_colors) % len(_EMA_COLORS)])

    if ema_widths is None:
        ema_widths = [1] * len(ema_periods)
    while len(ema_widths) < len(ema_periods):
        ema_widths.append(1)
    tf_label = _TF_LABEL.get(tf, tf)

    if df is None or df.empty:
        return {
            "candlestick": [], "emas": [], "cci": [], "macd": [],
            "pair": pair, "tf": tf_label, "empty": True,
        }

    def ts(idx):
        return int(idx.timestamp())

    # ── Candlestick ──────────────────────────────────────────────────────────
    candlestick = [
        {"time": ts(t), "open": float(r.open), "high": float(r.high),
         "low": float(r.low), "close": float(r.close)}
        for t, r in df.iterrows()
    ]

    # ── EMAs ─────────────────────────────────────────────────────────────────
    emas = []
    for period, color, width in zip(ema_periods, ema_colors, ema_widths):
        if period and period < len(df):
            vals = calc_ema(df["close"], period)
            emas.append({
                "period": period,
                "color":  color,
                "width":  width,
                "data": [
                    {"time": ts(t), "value": float(v)}
                    for t, v in zip(df.index, vals) if not pd.isna(v)
                ],
            })

    # ── CCI line (green above 0, red below 0) ────────────────────────────────
    cci = []
    if len(df) > config.CCI_PERIOD:
        vals = calc_cci(df["high"], df["low"], df["close"], config.CCI_PERIOD)
        cci = [
            {"time": ts(t), "value": float(v),
             "color": "#00ff88" if v >= 0 else "#ff3366"}
            for t, v in zip(df.index, vals) if not pd.isna(v)
        ]

    # ── MACD histogram ────────────────────────────────────────────────────────
    macd = []
    if len(df) > config.MACD_SLOW:
        hist = macd_histogram(
            df["close"], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL
        )
        macd = [
            {"time": ts(t), "value": float(v),
             "color": "#00ff88" if v >= 0 else "#ff3366"}
            for t, v in zip(df.index, hist) if not pd.isna(v)
        ]

    return {
        "candlestick":   candlestick,
        "emas":          emas,
        "cci":           cci,
        "macd":          macd,
        "pair":          pair,
        "tf":            tf_label,
        "signal_levels": signal_levels,   # None or {entry, sl, tp1, tp2, tp3, direction}
    }
