"""
XGBoost-based ML model that learns which signal setups produce winning trades.

Lifecycle:
  - Requires MIN_SAMPLES (50) closed trades before first training
  - Retrains every RETRAIN_EVERY (25) new closed trades
  - Trained model saved to models/ml_model.pkl
  - Win probability fed back into confluence_scorer to boost/suppress signals

Upgrade from RandomForest:
  - XGBoost handles small, imbalanced datasets better and gives calibrated probabilities
  - 6 new features: atr_pips, h4_trend, d_trend, direction, session, market_structure
  - StratifiedKFold cross-validation instead of single 80/20 split
  - Label-encodes categorical columns internally (CSV stays human-readable)
"""
import logging
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedKFold, cross_val_score
from xgboost import XGBClassifier

from engine.strategy_price_action import _BULLISH as _BULLISH_PATTERNS, _BEARISH as _BEARISH_PATTERNS

logger = logging.getLogger(__name__)

_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH  = os.path.join(_ROOT, "models", "ml_model.pkl")
LOG_PATH    = os.path.join(_ROOT, "data", "signal_log.csv")

# ── Features ──────────────────────────────────────────────────────────────────
# Numeric features — used as-is
NUMERIC_FEATURES = [
    "confluence_score", "ema_score", "structure_score", "pa_score",
    "candle_body_ratio", "upper_wick_ratio", "lower_wick_ratio",
    "cci_at_signal", "macd_hist_at_signal",
    "was_at_sr_zone", "bos_confirmed",
    "atr_pips", "hour_utc",
    # Added 2026-08-22 — derived from the existing pattern_name CSV column
    # (already logged for every signal, just never turned into model
    # features before now). has_bullish/has_bearish let the model learn
    # from the *specific candlestick pattern identity* the setup had,
    # rather than only pa_score's compressed 0-30 summary of it.
    "has_bullish_pattern", "has_bearish_pattern", "pattern_count",
]

# Categorical features — label-encoded at training/prediction time
CATEGORICAL_FEATURES = [
    "direction",        # long / short         → 1 / -1
    "h4_trend",         # bull / bear / neutral → 1 / -1 / 0
    "d_trend",          # bull / bear / neutral → 1 / -1 / 0
    "session",          # london_ny / london / new_york / asian → 3/2/1/0
    "market_structure", # uptrend / downtrend / ranging → 1 / -1 / 0
    # Added 2026-08-22 — one shared model across all pairs (not a separate
    # model per pair — signal_log.csv has ~200 usable rows total across
    # 5-6 pairs, too little to split further), with pair as a feature so
    # it can still learn pair-specific differences instead of treating
    # every pair identically. See tasks/todo.md 2026-08-22.
    "pair",
]

# Flat list used externally (e.g. by main.py to build a feature dict)
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

# Stable pair encoding — APPEND new pairs here only, never reorder or
# remove entries. Each pair's code is baked into every historical training
# row; reordering would silently change what old rows mean to the model.
# Covers every pair this project has traded or watched, active or paused.
_PAIR_ENCODING: dict = {
    "GBP_USD": 0, "NZD_USD": 1, "GBP_CAD": 2, "CHF_JPY": 3, "AUD_JPY": 4,
    "EUR_AUD": 5, "EUR_JPY": 6, "USD_CAD": 7, "EUR_CHF": 8, "EUR_CAD": 9,
    "EUR_GBP": 10, "CAD_CHF": 11, "NZD_CHF": 12, "XAU_USD": 13,
}

# ── Encoding maps ─────────────────────────────────────────────────────────────
_ENCODINGS: dict[str, dict] = {
    "direction":        {"long": 1,  "short": -1},
    "h4_trend":         {"bull": 1,  "bear": -1,  "neutral": 0},
    "d_trend":          {"bull": 1,  "bear": -1,  "neutral": 0},
    "session":          {"london_ny": 3, "london": 2, "new_york": 1, "asian": 0},
    # FIXED 2026-08-22 — engine.strategy_market_structure.classify_structure()
    # actually returns "bullish"/"bearish"/"ranging", not "uptrend"/
    # "downtrend". The old keys never matched, so every trending row
    # silently mapped to 0 (fillna default) same as genuinely-ranging rows
    # — confirmed against real data: 222/223 logged rows showed
    # market_structure="ranging" under the old mapping, 1 "bearish". The
    # model has never actually been able to learn trend-structure
    # direction until this fix.
    "market_structure": {"bullish": 1, "bearish": -1, "ranging": 0},
    "pair":             _PAIR_ENCODING,
}


def _pattern_name_to_features(pattern_name: str) -> dict:
    """Parses the pipe-joined pattern_name string (learning.data_collector's
    "|".join(patterns) format) into the 3 derived numeric features above.
    Used both at training time (existing CSV rows) and available for any
    caller building a features dict from a raw pattern_name string —
    callers that already have the pattern list (e.g. a live strategy
    calling engine.strategy_price_action.detect_patterns() directly)
    should compute these directly instead, see engine/strategy_adaptive.py."""
    if pd.isna(pattern_name):
        pattern_name = ""
    names = [p for p in str(pattern_name).split("|") if p]
    return {
        "has_bullish_pattern": float(any(p in _BULLISH_PATTERNS for p in names)),
        "has_bearish_pattern": float(any(p in _BEARISH_PATTERNS for p in names)),
        "pattern_count": float(len(names)),
    }


# Process-wide cache: model_path -> (mtime, saved_dict). Module-level
# rather than per-instance since PatternLearner is constructed cheaply and
# often (e.g. once per SignalEngine.run() call, per its own docstring) —
# an instance-level cache would just reload every time a new instance is
# made, defeating the point.
_model_cache: dict = {}


def _load_model_cached(model_path: str) -> dict:
    """Returns the joblib-loaded {"model", "features", "accuracy"} dict for
    model_path, from cache when the file's mtime matches what's cached,
    otherwise reads it fresh and updates the cache. Raises FileNotFoundError
    if model_path doesn't exist (callers already handle that case)."""
    mtime = os.path.getmtime(model_path)   # raises FileNotFoundError if missing
    cached = _model_cache.get(model_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    saved = joblib.load(model_path)
    _model_cache[model_path] = (mtime, saved)
    return saved


def _derive_pattern_features(df: pd.DataFrame) -> pd.DataFrame:
    """Adds has_bullish_pattern/has_bearish_pattern/pattern_count columns
    to a loaded signal_log.csv DataFrame from its existing pattern_name
    column — every one of the ~200 historical rows already has this data,
    it just wasn't parsed into model features before now."""
    df = df.copy()
    if "pattern_name" in df.columns:
        derived = df["pattern_name"].apply(_pattern_name_to_features).apply(pd.Series)
        for col in ("has_bullish_pattern", "has_bearish_pattern", "pattern_count"):
            df[col] = derived[col]
    return df


def _encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    """Replace string categoricals with their numeric codes. Unknown values → 0."""
    df = df.copy()
    for col, mapping in _ENCODINGS.items():
        if col in df.columns:
            df[col] = df[col].map(mapping).fillna(0).astype(float)
    return df


class PatternLearner:
    MIN_SAMPLES   = 50
    RETRAIN_EVERY = 25

    def __init__(self):
        self._last_trained_count = 0

    # ── Retraining trigger ────────────────────────────────────────────────────

    def should_retrain(self, log_path: str = LOG_PATH) -> bool:
        """True when enough new closed trades have accumulated since last training."""
        df = _load_closed(log_path)
        if df is None:
            return False
        n = len(df)
        if n < self.MIN_SAMPLES:
            return False
        return (n - self._last_trained_count) >= self.RETRAIN_EVERY

    # ── Training ──────────────────────────────────────────────────────────────

    def train(
        self,
        log_path:   str = LOG_PATH,
        model_path: str = MODEL_PATH,
    ) -> str | None:
        """
        Train an XGBClassifier on all closed trades.
        Uses 5-fold stratified cross-validation for a reliable performance estimate.
        Returns a summary string on success, None if insufficient data.
        """
        df = _load_closed(log_path)
        if df is None or len(df) < self.MIN_SAMPLES:
            logger.info(
                "PatternLearner: %d closed trades — need %d to train",
                len(df) if df is not None else 0, self.MIN_SAMPLES,
            )
            return None

        df = _derive_pattern_features(df)
        df = _encode_categoricals(df)
        avail = [f for f in FEATURES if f in df.columns]
        X = df[avail].apply(pd.to_numeric, errors="coerce").fillna(0)
        # would_win/would_lose are shadow-resolved outcomes for signals that were
        # scored but never traded (learning/shadow_outcomes.py) — trained on
        # the same footing as real win/loss so the model also learns from
        # near-misses and gate-rejected setups, not just trades actually taken.
        y = df.loc[X.index, "outcome"].isin(["win", "would_win"]).astype(int)

        if len(X) < self.MIN_SAMPLES:
            logger.info("PatternLearner: too many unparseable rows — %d usable", len(X))
            return None

        # Class imbalance: weight the minority class automatically
        n_pos   = int(y.sum())
        n_neg   = int((y == 0).sum())
        scale_w = n_neg / n_pos if n_pos > 0 else 1.0

        model = XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            scale_pos_weight=scale_w,   # handles win/loss imbalance
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )

        # Cross-validation — gives a stable estimate with small N
        cv = StratifiedKFold(n_splits=min(5, n_pos, n_neg), shuffle=True, random_state=42)
        cv_scores = cross_val_score(model, X, y, cv=cv, scoring="roc_auc")
        cv_mean   = float(np.mean(cv_scores))
        cv_std    = float(np.std(cv_scores))

        # Final fit on all data
        model.fit(X, y)

        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        joblib.dump({"model": model, "features": avail, "accuracy": cv_mean}, model_path)

        self._last_trained_count = len(df)
        summary = (
            f"XGBoost trained on {len(df)} samples  "
            f"features={len(avail)}  "
            f"AUC={cv_mean:.3f}±{cv_std:.3f}  "
            f"pos_weight={scale_w:.1f}"
        )
        logger.info("PatternLearner: %s", summary)
        return summary

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict_win_prob(
        self,
        features: dict,
        model_path: str = MODEL_PATH,
    ) -> float | None:
        """
        Return P(win) in [0, 1] for the given feature dict.
        Returns None if no model exists yet — safe to call before first training.

        Loads via _load_model_cached() (mtime-keyed process-wide cache) —
        one real disk read per model version, not per call. Found this
        mattered 2026-08-22 backtesting engine/strategy_adaptive.py: a
        naive joblib.load() every call took ~12 minutes for one pair's
        6000-bar backtest (thousands of predictions × a full unpickle
        each time). The cache still picks up a retrained model
        immediately — keyed on the file's mtime, so a newer file (a real
        retrain) always invalidates it; this only skips *redundant*
        reloads of an unchanged file.
        """
        try:
            saved = _load_model_cached(model_path)
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.error("predict_win_prob failed: %s", exc)
            return None
        try:
            model   = saved["model"]
            cols    = saved["features"]
            row     = {f: (features.get(f) or 0) for f in cols}
            X       = pd.DataFrame([row])
            X       = _encode_categoricals(X)
            X       = X.apply(pd.to_numeric, errors="coerce").fillna(0)
            return float(model.predict_proba(X)[0][1])
        except Exception as exc:
            logger.error("predict_win_prob failed: %s", exc)
            return None

    # ── Accuracy (for dashboard) ─────────────────────────────────────────────

    def get_accuracy(self, model_path: str = MODEL_PATH) -> float | None:
        """
        Return the cross-validated ROC-AUC computed at training time, or None
        if no model exists yet (or it was saved before this field existed).
        """
        try:
            saved = _load_model_cached(model_path)
            return saved.get("accuracy")
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.error("get_accuracy failed: %s", exc)
            return None

    # ── Feature importance (for dashboard) ───────────────────────────────────

    def get_feature_importance(self, model_path: str = MODEL_PATH) -> dict | None:
        """Return {feature_name: importance} sorted descending, or None if no model."""
        try:
            saved = _load_model_cached(model_path)
        except FileNotFoundError:
            return None
        try:
            model = saved["model"]
            cols  = saved["features"]
            pairs = sorted(
                zip(cols, model.feature_importances_),
                key=lambda x: x[1], reverse=True,
            )
            return dict(pairs)
        except Exception as exc:
            logger.error("get_feature_importance failed: %s", exc)
            return None

    def top_features(self, n: int = 3, model_path: str = MODEL_PATH) -> list[str]:
        """Return the n most important feature names."""
        importance = self.get_feature_importance(model_path)
        if not importance:
            return []
        return list(importance.keys())[:n]

    # ── Force retrain (ignores MIN_SAMPLES) ──────────────────────────────────

    def force_train(
        self,
        log_path:   str = LOG_PATH,
        model_path: str = MODEL_PATH,
        min_samples: int = 10,
    ) -> str:
        """Train regardless of MIN_SAMPLES. Returns summary or descriptive error string."""
        df = _load_closed(log_path)
        n  = len(df) if df is not None else 0
        if n < min_samples:
            return f"Not enough data: {n} closed trades (need at least {min_samples})"
        orig = self.MIN_SAMPLES
        self.MIN_SAMPLES = min_samples
        try:
            result = self.train(log_path, model_path)
        finally:
            self.MIN_SAMPLES = orig
        return result or f"Training failed ({n} samples)"

    # ── Calibration data (predicted prob vs actual outcome) ───────────────────

    def calibration_data(
        self,
        n:          int = 20,
        log_path:   str = LOG_PATH,
        model_path: str = MODEL_PATH,
    ) -> list[dict]:
        """
        Return the last `n` closed trades from signal_log with their predicted win probability.
        Each entry: {pair, direction, score, predicted_prob, actual}.
        """
        df = _load_closed(log_path)
        if df is None or len(df) == 0:
            return []
        out = []
        for _, r in df.tail(n).iterrows():
            features = {f: r.get(f) for f in FEATURES}
            prob = self.predict_win_prob(features, model_path)
            out.append({
                "pair":           str(r.get("pair", "")),
                "direction":      str(r.get("direction", "")),
                "score":          r.get("confluence_score", ""),
                "predicted_prob": round(prob, 3) if prob is not None else None,
                "actual":         str(r.get("outcome", "")),
            })
        return out


# ── Helper ────────────────────────────────────────────────────────────────────

def _load_closed(log_path: str) -> pd.DataFrame | None:
    """
    Load signal_log.csv and return win/loss rows — real trade outcomes plus
    would_win/would_lose (shadow-resolved outcomes for signals that were
    scored but never traded, see learning/shadow_outcomes.py). Excludes any
    backtest-seeded rows (source == "seed" — old rows from the
    seed-from-backtest feature removed 2026-07-24, which could go stale
    relative to the live strategy config and shouldn't be silently blended
    into what the model learns from). Note: would_win/would_lose could only
    ever originate from real live scanning even when seeding existed (it
    never wrote those outcomes), so this filter only ever removes seeded
    win/loss rows in practice — kept explicit rather than relying on that
    invariant. Returns None on any error.
    """
    try:
        df = pd.read_csv(log_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return None
    is_live = df["source"] == "live" if "source" in df.columns else False
    closed = df[df["outcome"].isin(["win", "loss", "would_win", "would_lose"]) & is_live].copy()
    return closed if len(closed) > 0 else None
