"""
Formats OHLCV data and indicators as plain dicts for TradingView Lightweight Charts v5.
Returned dict goes into chart-data-store and is consumed by dashboard/assets/chart.js.
All timestamps are UNIX seconds (UTC integer).
"""
import pandas as pd

from engine.indicators import (
    ema as calc_ema,
    cci as calc_cci, cci_source, cci_smoothing_ma,
    macd_histogram, macd_full,
    rsi as calc_rsi,
    bollinger_bands as calc_bb,
    stochastic as calc_stoch,
)
import config

_TF_LABEL   = {"M5": "5m", "M15": "15m", "M30": "30m", "H1": "1H", "H4": "4H", "D": "D", "W": "W"}
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
    chart_type: str     = "candlestick",  # "candlestick" | "heikin_ashi"
    # ── Per-chart indicator overrides (from ind-settings-store) ───────────────
    cci_length: int     = None,   # None → use config.CCI_PERIOD
    cci_src: str        = None,   # None → "hlc3"
    macd_fast: int      = None,   # None → use config.MACD_FAST
    macd_slow: int      = None,   # None → use config.MACD_SLOW
    macd_signal_: int   = None,   # None → use config.MACD_SIGNAL
    # ── New indicator toggles ─────────────────────────────────────────────────
    show_rsi: bool      = False,
    rsi_period: int     = 14,
    show_bb: bool       = False,
    bb_period: int      = 20,
    bb_mult: float      = 2.0,
    show_volume: bool   = False,
    show_stoch: bool    = False,
    stoch_k: int        = 14,
    stoch_d: int        = 3,
    stoch_smooth: int   = 3,
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

    # ── Candlestick (or Heikin-Ashi) ─────────────────────────────────────────
    if chart_type == "heikin_ashi" and len(df) > 1:
        ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
        ha_open  = ha_close.copy()
        ha_open.iloc[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2
        for i in range(1, len(df)):
            ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2
        ha_high  = pd.concat([df["high"], ha_open, ha_close], axis=1).max(axis=1)
        ha_low   = pd.concat([df["low"],  ha_open, ha_close], axis=1).min(axis=1)
        candlestick = [
            {"time": ts(t), "open": float(ha_open.iloc[i]), "high": float(ha_high.iloc[i]),
             "low": float(ha_low.iloc[i]),   "close": float(ha_close.iloc[i])}
            for i, t in enumerate(df.index)
        ]
    else:
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

    # ── CCI — raw line + smoothing MA + optional Bollinger Bands ────────────
    # Matches TradingView CCI script exactly:
    #   Core:      (src - SMA) / (0.015 * MeanAbsDev)      — TV's ta.dev
    #   Smoothing: SMA-14 by default (yellow line overlay)  — TV's "Smoothing" group
    #   BB:        only when ma_type == "SMA + Bollinger Bands"
    _CCI_PERIOD_USE    = cci_length if cci_length else config.CCI_PERIOD
    _CCI_SRC_USE       = cci_src if cci_src else "hlc3"
    _CCI_SMOOTH_TYPE   = getattr(config, "CCI_SMOOTH_TYPE",   "SMA")
    _CCI_SMOOTH_LENGTH = getattr(config, "CCI_SMOOTH_LENGTH", 14)
    _CCI_BB_MULT       = getattr(config, "CCI_BB_MULT",       2.0)

    cci = []
    cci_ma_data  = []
    cci_bb_upper = []
    cci_bb_lower = []

    if len(df) > _CCI_PERIOD_USE:
        _src_series = cci_source(
            df["high"], df["low"], df["close"],
            open_=df.get("open"),
            src=_CCI_SRC_USE,
        )
        vals = calc_cci(df["high"], df["low"], df["close"],
                        period=_CCI_PERIOD_USE, source=_src_series)

        # Raw CCI — per-bar green/red coloring (above/below zero)
        cci = [
            {"time": ts(t), "value": float(v),
             "color": "#00ff88" if v >= 0 else "#ff3366"}
            for t, v in zip(df.index, vals) if not pd.isna(v)
        ]

        # Smoothing MA (TV script default: SMA-14, colour yellow #ffd700)
        ma_s, bb_up, bb_dn = cci_smoothing_ma(
            vals, length=_CCI_SMOOTH_LENGTH,
            ma_type=_CCI_SMOOTH_TYPE, bb_mult=_CCI_BB_MULT,
        )
        if ma_s is not None:
            cci_ma_data = [
                {"time": ts(t), "value": float(v)}
                for t, v in zip(df.index, ma_s) if not pd.isna(v)
            ]
        if bb_up is not None:
            cci_bb_upper = [
                {"time": ts(t), "value": float(v)}
                for t, v in zip(df.index, bb_up) if not pd.isna(v)
            ]
            cci_bb_lower = [
                {"time": ts(t), "value": float(v)}
                for t, v in zip(df.index, bb_dn) if not pd.isna(v)
            ]

    # ── MACD — histogram (TV-style momentum coloring) + line + signal ────────
    _MACD_FAST_USE   = macd_fast    if macd_fast    else config.MACD_FAST
    _MACD_SLOW_USE   = macd_slow    if macd_slow    else config.MACD_SLOW
    _MACD_SIGNAL_USE = macd_signal_ if macd_signal_ else config.MACD_SIGNAL

    macd = []
    macd_line_data   = []
    macd_signal_data = []
    if len(df) > _MACD_SLOW_USE:
        ml, sl_s, hist = macd_full(
            df["close"], _MACD_FAST_USE, _MACD_SLOW_USE, _MACD_SIGNAL_USE
        )
        hist_vals = list(hist)
        for i, (t, v) in enumerate(zip(df.index, hist_vals)):
            if pd.isna(v):
                continue
            prev = hist_vals[i - 1] if i > 0 else v
            if pd.isna(prev):
                prev = v
            # TV color logic: rising histogram = brighter, falling = muted
            if v >= 0:
                color = "#26a69a" if v > prev else "#b2dfdb"   # bright/muted teal
            else:
                color = "#ff5252" if v < prev else "#ffcdd2"   # bright/muted red
            macd.append({"time": ts(t), "value": float(v), "color": color})

        macd_line_data = [
            {"time": ts(t), "value": float(v)}
            for t, v in zip(df.index, ml) if not pd.isna(v)
        ]
        macd_signal_data = [
            {"time": ts(t), "value": float(v)}
            for t, v in zip(df.index, sl_s) if not pd.isna(v)
        ]

    # ── RSI ──────────────────────────────────────────────────────────────────
    rsi_data = []
    if show_rsi and len(df) > rsi_period:
        vals = calc_rsi(df["close"], rsi_period)
        rsi_data = [
            {"time": ts(t), "value": float(v)}
            for t, v in zip(df.index, vals) if not pd.isna(v)
        ]

    # ── Bollinger Bands (price pane overlay) ─────────────────────────────────
    bb_upper_data = []
    bb_basis_data = []
    bb_lower_data = []
    if show_bb and len(df) > bb_period:
        upper, basis, lower = calc_bb(df["close"], bb_period, bb_mult)
        bb_upper_data = [{"time": ts(t), "value": float(v)}
                         for t, v in zip(df.index, upper) if not pd.isna(v)]
        bb_basis_data = [{"time": ts(t), "value": float(v)}
                         for t, v in zip(df.index, basis) if not pd.isna(v)]
        bb_lower_data = [{"time": ts(t), "value": float(v)}
                         for t, v in zip(df.index, lower) if not pd.isna(v)]

    # ── Volume ───────────────────────────────────────────────────────────────
    volume_data = []
    if show_volume and "volume" in df.columns:
        volume_data = [
            {"time": ts(t), "value": float(r["volume"]),
             "color": "#26a69a66" if float(r["close"]) >= float(r["open"]) else "#ff525266"}
            for t, r in df.iterrows() if not pd.isna(r["volume"])
        ]

    # ── Stochastic ───────────────────────────────────────────────────────────
    stoch_k_data = []
    stoch_d_data = []
    if show_stoch and len(df) > stoch_k + stoch_smooth:
        sk, sd = calc_stoch(df["high"], df["low"], df["close"],
                            stoch_k, stoch_d, stoch_smooth)
        stoch_k_data = [{"time": ts(t), "value": float(v)}
                        for t, v in zip(df.index, sk) if not pd.isna(v)]
        stoch_d_data = [{"time": ts(t), "value": float(v)}
                        for t, v in zip(df.index, sd) if not pd.isna(v)]

    return {
        "candlestick":   candlestick,
        "emas":          emas,
        "cci":           cci,
        "cci_ma":        cci_ma_data,       # CCI smoothing MA (yellow, TV default SMA-14)
        "cci_bb_upper":  cci_bb_upper,      # BB upper band (only when type = SMA+BB)
        "cci_bb_lower":  cci_bb_lower,      # BB lower band (only when type = SMA+BB)
        "macd":          macd,
        "macd_line":     macd_line_data,    # MACD line (blue)
        "macd_signal":   macd_signal_data,  # Signal line (orange)
        "rsi":           rsi_data,
        "bb_upper":      bb_upper_data,
        "bb_basis":      bb_basis_data,
        "bb_lower":      bb_lower_data,
        "volume":        volume_data,
        "stoch_k":       stoch_k_data,
        "stoch_d":       stoch_d_data,
        "pair":          pair,
        "tf":            tf_label,
        "raw_tf":        tf,
        "signal_levels": signal_levels,
        "ind_toggles": {
            "rsi":    show_rsi,
            "bb":     show_bb,
            "volume": show_volume,
            "stoch":  show_stoch,
        },
        # Active computation params — fed back to chart.js for display in panel header
        "ind_params": {
            "cci_length": _CCI_PERIOD_USE,
            "cci_src":    _CCI_SRC_USE,
            "macd_fast":  _MACD_FAST_USE,
            "macd_slow":  _MACD_SLOW_USE,
            "macd_signal": _MACD_SIGNAL_USE,
            "rsi_period": rsi_period,
            "bb_period":  bb_period,
            "bb_mult":    bb_mult,
            "stoch_k":    stoch_k,
            "stoch_d":    stoch_d,
        },
    }
