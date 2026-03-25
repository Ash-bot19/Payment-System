"""XGBoost inference engine for ML risk scoring.

Exports:
    XGBoostScorer  -- Load-once, score-per-event inference class
    FEATURE_ORDER  -- Canonical feature order for DMatrix construction
    FEATURE_DEFAULTS -- Conservative defaults when Redis features unavailable

Per D-A3: missing model at startup → crash-and-exit (misconfiguration).
Per D-C3: FEATURE_DEFAULTS are conservative (high-risk) values that guarantee
          manual_review=True even if downstream reads feature values, not the flag.
"""

import sys

import numpy as np
import structlog
import xgboost as xgb

from models.ml_scoring import RiskScore

logger = structlog.get_logger(__name__)

# Canonical feature order — must match CLAUDE.md ML Risk Score Contract and
# the feature_names used during training in ml/train.py.
FEATURE_ORDER = [
    "tx_velocity_1m",
    "tx_velocity_5m",
    "amount_zscore",
    "merchant_risk_score",
    "device_switch_flag",
    "hour_of_day",
    "weekend_flag",
    "amount_cents_log",
]

# Conservative defaults per D-C3: values that represent a suspicious transaction.
# manual_review=True is set unconditionally when features are unavailable, but
# these values also ensure the model itself would score the event as high-risk.
FEATURE_DEFAULTS: dict[str, float] = {
    "tx_velocity_1m": 10,        # above normal thresholds
    "tx_velocity_5m": 50,        # above normal thresholds
    "amount_zscore": 2.0,        # unusual amount
    "merchant_risk_score": 1.0,  # maximum risk
    "device_switch_flag": 1,     # switched device
    "hour_of_day": 12,           # neutral (no bias)
    "weekend_flag": 0,           # neutral (no bias)
    "amount_cents_log": 0.0,     # neutral
}


class XGBoostScorer:
    """Loads a pre-trained XGBoost model and scores feature vectors.

    Model is loaded once at construction and held in process memory.
    Never reloaded per inference — load time is 10-100ms for a small model.

    Per D-A3: if model file is missing at construction time, logs ERROR and
    calls sys.exit(1). This is misconfiguration, not a recoverable runtime error.
    """

    def __init__(self, model_path: str) -> None:
        """Load XGBoost model from disk.

        Args:
            model_path: Absolute or relative path to the .ubj model file.

        Raises:
            SystemExit: If model file does not exist at model_path.
        """
        import os

        if not os.path.exists(model_path):
            logger.error("model_file_missing", path=model_path)
            sys.exit(1)

        self._booster = xgb.Booster()
        self._booster.load_model(model_path)
        logger.info("xgboost_model_loaded", path=model_path)

    def score(self, features: dict[str, float]) -> RiskScore:
        """Run inference on a feature dict and return a RiskScore.

        Args:
            features: Dict mapping feature name → float value.
                      Keys must include all 8 names in FEATURE_ORDER.

        Returns:
            RiskScore with risk_score in [0.0, 1.0], is_high_risk (>= 0.7),
            and manual_review (>= 0.85).
        """
        feature_array = np.array(
            [[features[name] for name in FEATURE_ORDER]],
            dtype=np.float32,
        )
        dmatrix = xgb.DMatrix(feature_array, feature_names=FEATURE_ORDER)
        raw_score = self._booster.predict(dmatrix)[0]

        # Clamp to [0, 1] — XGBoost binary:logistic outputs in this range
        # but clamping is defensive against edge cases.
        risk_score = float(max(0.0, min(1.0, raw_score)))

        return RiskScore(
            risk_score=risk_score,
            is_high_risk=risk_score >= 0.7,
            manual_review=risk_score >= 0.85,
        )
