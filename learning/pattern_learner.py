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
]

# Categorical features — label-encoded at training/prediction time
CATEGORICAL_FEATURES = [
    "direction",        # long / short         → 1 / -1
    "h4_trend",         # bull / bear / neutral → 1 / -1 / 0
    "d_trend",          # bull / bear / neutral → 1 / -1 / 0
    "session",          # london_ny / london / new_york / asian → 3/2/1/0
    "market_structure", # uptrend / downtrend / ranging → 1 / -1 / 0
]

# Flat list used externally (e.g. by main.py to build a feature dict)
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

# ── Encoding maps ─────────────────────────────────────────────────────────────
_ENCODINGS: dict[str, dict] = {
    "direction":        {"long": 1,  "short": -1},
    "h4_trend":         {"bull": 1,  "bear": -1,  "neutral": 0},
    "d_trend":          {"bull": 1,  "bear": -1,  "neutral": 0},
    "session":          {"london_ny": 3, "london": 2, "new_york": 1, "asian": 0},
    "market_structure": {"uptrend": 1, "downtrend": -1, "ranging": 0},
}


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
        """
        if not os.path.exists(model_path):
            return None
        try:
            saved   = joblib.load(model_path)
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
        if not os.path.exists(model_path):
            return None
        try:
            saved = joblib.load(model_path)
            return saved.get("accuracy")
        except Exception as exc:
            logger.error("get_accuracy failed: %s", exc)
            return None

    # ── Feature importance (for dashboard) ───────────────────────────────────

    def get_feature_importance(self, model_path: str = MODEL_PATH) -> dict | None:
        """Return {feature_name: importance} sorted descending, or None if no model."""
        if not os.path.exists(model_path):
            return None
        try:
            saved = joblib.load(model_path)
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
