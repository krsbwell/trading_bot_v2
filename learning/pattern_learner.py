"""
ML model that learns which signal setups lead to winning trades.

Lifecycle:
  - Requires MIN_SAMPLES (50) closed trades before first training
  - Retrains every RETRAIN_EVERY (25) new closed trades
  - Trained model saved to models/ml_model.pkl
  - Win probability fed back into confluence_scorer to boost/suppress signals
"""
import logging
import os

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

logger = logging.getLogger(__name__)

_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH  = os.path.join(_ROOT, "models", "ml_model.pkl")
LOG_PATH    = os.path.join(_ROOT, "data", "signal_log.csv")

FEATURES = [
    "confluence_score", "ema_score", "structure_score", "pa_score",
    "candle_body_ratio", "upper_wick_ratio", "lower_wick_ratio",
    "cci_at_signal", "macd_hist_at_signal",
    "was_at_sr_zone", "bos_confirmed",
]


class PatternLearner:
    MIN_SAMPLES   = 50
    RETRAIN_EVERY = 25

    def __init__(self):
        self._last_trained_count = 0

    # ── Retraining trigger ────────────────────────────────────────────────────

    def should_retrain(self, log_path: str = LOG_PATH) -> bool:
        """
        Return True when enough new trades have accumulated since last training.
        Conditions: total closed >= MIN_SAMPLES AND new since last train >= RETRAIN_EVERY.
        """
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
        Train a RandomForestClassifier on all closed trades.
        Returns a classification_report string on success, None if insufficient data.
        Saves model to model_path and updates _last_trained_count.
        """
        df = _load_closed(log_path)
        if df is None or len(df) < self.MIN_SAMPLES:
            logger.info(
                "PatternLearner: %d closed trades — need %d to train",
                len(df) if df is not None else 0, self.MIN_SAMPLES,
            )
            return None

        X = df[FEATURES].apply(pd.to_numeric, errors="coerce").dropna()
        y = (df.loc[X.index, "outcome"] == "win").astype(int)

        if len(X) < self.MIN_SAMPLES:
            logger.info("PatternLearner: too many NaN features — %d usable rows", len(X))
            return None

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y if y.nunique() > 1 else None
        )
        model = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1)
        model.fit(X_train, y_train)

        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        joblib.dump(model, model_path)

        self._last_trained_count = len(df)
        report = classification_report(y_test, model.predict(X_test), zero_division=0)
        logger.info("PatternLearner trained on %d samples\n%s", len(df), report)
        return report

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict_win_prob(
        self,
        features: dict,
        model_path: str = MODEL_PATH,
    ) -> float | None:
        """
        Return P(win) for a given feature dict, or None if no model exists yet.
        Safe: returns None on any error rather than crashing the signal engine.
        """
        if not os.path.exists(model_path):
            return None
        try:
            model = joblib.load(model_path)
            X = pd.DataFrame([features])[FEATURES]
            X = X.apply(pd.to_numeric, errors="coerce").fillna(0)
            return float(model.predict_proba(X)[0][1])
        except Exception as exc:
            logger.error("predict_win_prob failed: %s", exc)
            return None

    # ── Feature importance (for dashboard) ───────────────────────────────────

    def get_feature_importance(self, model_path: str = MODEL_PATH) -> dict | None:
        """Return {feature_name: importance} sorted descending, or None if no model."""
        if not os.path.exists(model_path):
            return None
        try:
            model = joblib.load(model_path)
            pairs = sorted(
                zip(FEATURES, model.feature_importances_),
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


# ── Helper ────────────────────────────────────────────────────────────────────

def _load_closed(log_path: str) -> pd.DataFrame | None:
    """Load signal_log.csv and return only win/loss rows. Returns None on any error."""
    try:
        df = pd.read_csv(log_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return None
    closed = df[df["outcome"].isin(["win", "loss"])].copy()
    return closed if len(closed) > 0 else None
