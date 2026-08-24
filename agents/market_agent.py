"""
Market context facade — wraps connectors.oanda_connector.OandaConnector +
engine.market_regime. Takes a connector instance rather than constructing
its own (same dependency-injection pattern engine.signal_engine.SignalEngine
already uses via get_candles_fn), so it's testable without a real OANDA
connection and so it reuses whichever connector main.py already has open
rather than opening a second one.
"""
import logging

import config
from engine.market_regime import classify_regime

logger = logging.getLogger(__name__)


def get_context(pair: str, get_candles_fn, primary_tf: "str | None" = None,
                 confirm_tf: "str | None" = None, bars: int = 250) -> "dict | None":
    """
    get_candles_fn: same signature as engine.signal_engine.GetCandlesFn —
    (instrument, granularity, count) -> pd.DataFrame. Pass a bound
    connector method, e.g. `oanda.get_candles`.

    Returns {"pair", "df_primary", "df_confirm", "regime": <classify_regime dict>}
    or None on a fetch failure (mirrors SignalEngine.run's own
    try/except-and-return-None on candle fetch errors).
    """
    primary_tf = primary_tf or config.primary_tf_for(pair)
    confirm_tf = confirm_tf or config.confirm_tf_for(pair)
    try:
        df_primary = get_candles_fn(pair, primary_tf, bars)
        df_confirm = get_candles_fn(pair, confirm_tf, bars)
    except Exception as exc:
        logger.error("market_agent candle fetch failed for %s: %s", pair, exc)
        return None

    regime_info = classify_regime(df_primary, df_confirm)
    return {
        "pair": pair,
        "df_primary": df_primary,
        "df_confirm": df_confirm,
        "primary_tf": primary_tf,
        "confirm_tf": confirm_tf,
        "regime": regime_info,
    }
