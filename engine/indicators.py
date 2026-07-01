"""
Self-contained indicator functions — no pandas-ta dependency.
All return pd.Series aligned to the input index.
"""
import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def cci_source(
    high: pd.Series, low: pd.Series,
    close: pd.Series, open_: pd.Series | None = None,
    src: str = "hlc3",
) -> pd.Series:
    """
    Compute the price source used by CCI — matches TradingView source options.
    src options: 'close'|'open'|'high'|'low'|'hl2'|'hlc3'|'ohlc4'
    """
    if src == "close":   return close
    if src == "open":    return open_ if open_ is not None else close
    if src == "high":    return high
    if src == "low":     return low
    if src == "hl2":     return (high + low) / 2
    if src == "ohlc4":
        op = open_ if open_ is not None else close
        return (op + high + low + close) / 4
    return (high + low + close) / 3   # hlc3 — default


def cci(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int = 20,
    source: pd.Series | None = None,   # pre-computed source; None → hlc3
) -> pd.Series:
    """
    Commodity Channel Index — matches TradingView CCI script exactly.

    Formula: (src - SMA(src, length)) / (0.015 * MeanAbsDev(src, length))
    TradingView's ta.dev() is the mean absolute deviation, same as `mad` below.

    Pass `source` to use a custom price source (close, hl2, etc.).
    If None, defaults to HLC3 as the TV script does.
    """
    tp  = source if source is not None else (high + low + close) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    # Guard against division by zero on flat bars
    return (tp - sma) / (0.015 * mad.replace(0, np.nan))


def cci_smoothing_ma(
    cci_series: pd.Series,
    length: int = 14,
    ma_type: str = "SMA",
    bb_mult: float = 2.0,
) -> tuple[pd.Series, pd.Series | None, pd.Series | None]:
    """
    Applies a smoothing MA to a CCI series — matches the TradingView CCI script's
    'Smoothing' group exactly.

    Supported ma_type values (same options as the TV script):
        "None"                 — returns (None, None, None)
        "SMA"                  — simple moving average
        "SMA + Bollinger Bands"— SMA + upper/lower BB bands
        "EMA"                  — exponential moving average
        "SMMA (RMA)"           — Wilder/RMA smoothing (ta.rma in Pine)
        "WMA"                  — weighted moving average
        "VWMA"                 — not applicable without volume; falls back to SMA

    Returns:
        (smoothing_ma, bb_upper, bb_lower)
        bb_upper / bb_lower are None unless ma_type == "SMA + Bollinger Bands"
    """
    if ma_type == "None" or not ma_type:
        return None, None, None

    if ma_type in ("SMA", "SMA + Bollinger Bands", "VWMA"):
        ma_vals = cci_series.rolling(length).mean()
    elif ma_type == "EMA":
        ma_vals = cci_series.ewm(span=length, adjust=False).mean()
    elif ma_type == "SMMA (RMA)":
        # Wilder smoothing: equivalent to EMA with alpha=1/length
        ma_vals = cci_series.ewm(alpha=1.0 / length, adjust=False).mean()
    elif ma_type == "WMA":
        weights = np.arange(1, length + 1, dtype=float)
        ma_vals = cci_series.rolling(length).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True
        )
    else:
        ma_vals = cci_series.rolling(length).mean()   # safe default

    bb_upper = bb_lower = None
    if ma_type == "SMA + Bollinger Bands":
        std   = cci_series.rolling(length).std(ddof=0)
        bb_upper = ma_vals + bb_mult * std
        bb_lower = ma_vals - bb_mult * std

    return ma_vals, bb_upper, bb_lower


def macd_histogram(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.Series:
    ema_fast    = close.ewm(span=fast,   adjust=False).mean()
    ema_slow    = close.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


def macd_full(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Returns (macd_line, signal_line, histogram) — all aligned to input index.
    Matches the TradingView MACD script exactly (EMA-based fast/slow/signal).
    """
    ema_fast    = close.ewm(span=fast,   adjust=False).mean()
    ema_slow    = close.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """
    Wilder Average Directional Index — matches TradingView ADX exactly.
    Returns the ADX line (0–100). Values above 25–30 indicate a trending market;
    values below 25 indicate a ranging/choppy market where EMA bounces work best.
    """
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional movement
    up   = high - prev_high
    down = prev_low - low

    plus_dm  = np.where((up > down) & (up > 0),   up,   0.0)
    minus_dm = np.where((down > up) & (down > 0),  down, 0.0)

    plus_dm_s  = pd.Series(plus_dm,  index=close.index)
    minus_dm_s = pd.Series(minus_dm, index=close.index)

    # Wilder smoothing (alpha = 1/period)
    alpha = 1.0 / period
    atr_s      = tr.ewm(alpha=alpha,       min_periods=period, adjust=False).mean()
    plus_di_s  = plus_dm_s.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    minus_di_s = minus_dm_s.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    plus_di  = 100 * plus_di_s  / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_di_s / atr_s.replace(0, np.nan)

    dx_denom = (plus_di + minus_di).replace(0, np.nan)
    dx       = 100 * (plus_di - minus_di).abs() / dx_denom

    return dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI — matches TradingView ta.rsi() exactly."""
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def bollinger_bands(
    close: pd.Series, period: int = 20, mult: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, basis, lower) — matches TradingView BB indicator."""
    basis = close.rolling(period).mean()
    std   = close.rolling(period).std(ddof=0)
    return basis + mult * std, basis, basis - mult * std


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series,
    k_period: int = 14, d_period: int = 3, smooth_k: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Returns (%K smoothed, %D) — matches TradingView Stochastic indicator."""
    lowest  = low.rolling(k_period).min()
    highest = high.rolling(k_period).max()
    raw_k   = 100 * (close - lowest) / (highest - lowest).replace(0, np.nan)
    k       = raw_k.rolling(smooth_k).mean()
    d       = k.rolling(d_period).mean()
    return k, d
