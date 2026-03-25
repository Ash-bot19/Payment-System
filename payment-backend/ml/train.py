# Run once to regenerate ml/models/model.ubj. Not part of the serving path.
"""Synthetic training script for the ML risk scoring XGBoost model.

Generates 1000 synthetic payment transactions with clear fraud/legitimate signal
separation, trains a binary XGBoost classifier, and saves model to ml/models/model.ubj.

Usage (from payment-backend/):
    python -m ml.train

Output:
    ml/models/model.ubj  -- Pre-trained XGBoost binary model (UBJ format)
    AUC printed to verify model quality.
"""

import os
from pathlib import Path

import numpy as np
import structlog
import xgboost as xgb
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

logger = structlog.get_logger(__name__)

FEATURE_NAMES = [
    "tx_velocity_1m",
    "tx_velocity_5m",
    "amount_zscore",
    "merchant_risk_score",
    "device_switch_flag",
    "hour_of_day",
    "weekend_flag",
    "amount_cents_log",
]


def generate_synthetic_data(n_total: int = 1000, fraud_rate: float = 0.3, seed: int = 42) -> tuple:
    """Generate synthetic payment transaction feature data.

    Normal transactions: low velocity, normal amounts, known merchants, daytime.
    Fraudulent transactions (30%): high velocity, unusual amounts, high-risk merchants,
    device switches, odd hours.

    Args:
        n_total:    Total number of samples to generate.
        fraud_rate: Fraction of samples that are fraudulent.
        seed:       Random seed for reproducibility.

    Returns:
        Tuple of (X: np.ndarray shape [n_total, 8], y: np.ndarray shape [n_total]).
    """
    rng = np.random.default_rng(seed)
    n_fraud = int(n_total * fraud_rate)
    n_normal = n_total - n_fraud

    # Normal transactions
    X_normal = np.column_stack([
        rng.normal(5, 2, n_normal),           # tx_velocity_1m
        rng.normal(20, 5, n_normal),           # tx_velocity_5m
        rng.normal(0, 0.5, n_normal),          # amount_zscore
        rng.uniform(0, 0.3, n_normal),         # merchant_risk_score
        np.zeros(n_normal),                    # device_switch_flag
        rng.uniform(8, 20, n_normal),          # hour_of_day
        np.zeros(n_normal),                    # weekend_flag
        rng.normal(7, 1, n_normal),            # amount_cents_log
    ])

    # Fraudulent transactions
    X_fraud = np.column_stack([
        rng.normal(15, 3, n_fraud),            # tx_velocity_1m
        rng.normal(80, 10, n_fraud),           # tx_velocity_5m
        rng.normal(2.5, 0.5, n_fraud),         # amount_zscore
        rng.uniform(0.5, 1.0, n_fraud),        # merchant_risk_score
        np.ones(n_fraud),                      # device_switch_flag
        rng.uniform(0, 6, n_fraud),            # hour_of_day
        rng.binomial(1, 0.5, n_fraud).astype(float),  # weekend_flag
        rng.normal(10, 1, n_fraud),            # amount_cents_log
    ])

    X = np.vstack([X_normal, X_fraud]).astype(np.float32)
    y = np.array([0] * n_normal + [1] * n_fraud, dtype=np.float32)

    # Shuffle
    idx = rng.permutation(n_total)
    return X[idx], y[idx]


def train_model(X: np.ndarray, y: np.ndarray) -> xgb.Booster:
    """Train XGBoost binary classifier.

    Args:
        X: Feature matrix, shape [n_samples, 8].
        y: Binary labels (0 = normal, 1 = fraud).

    Returns:
        Trained xgb.Booster.
    """
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)

    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=FEATURE_NAMES)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=FEATURE_NAMES)

    params = {
        "objective": "binary:logistic",
        "max_depth": 4,
        "eta": 0.1,
        "eval_metric": "auc",
        "seed": 42,
    }
    evals = [(dtrain, "train"), (dval, "val")]

    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=50,
        evals=evals,
        verbose_eval=False,
    )

    # Compute and log AUC on validation set
    preds = booster.predict(dval)
    auc = roc_auc_score(y_val, preds)
    logger.info("training_complete", val_auc=round(auc, 4), num_boost_round=50)

    return booster


def save_model(booster: xgb.Booster, output_path: str) -> None:
    """Save the trained model in UBJ (Universal Binary JSON) format.

    Args:
        booster:     Trained XGBoost Booster.
        output_path: Path to write model.ubj.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(output_path)
    size_kb = Path(output_path).stat().st_size // 1024
    logger.info("model_saved", path=output_path, size_kb=size_kb)


def main() -> None:
    """Entry point: generate data → train → save → print AUC."""
    output_path = os.path.join(os.path.dirname(__file__), "models", "model.ubj")

    logger.info("generating_synthetic_data", n_total=1000, fraud_rate=0.3)
    X, y = generate_synthetic_data(n_total=1000, fraud_rate=0.3)

    logger.info("training_xgboost_model")
    booster = train_model(X, y)

    save_model(booster, output_path)


if __name__ == "__main__":
    main()
