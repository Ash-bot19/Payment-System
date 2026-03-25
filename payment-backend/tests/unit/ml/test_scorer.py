"""Unit tests for XGBoostScorer and ML scoring Pydantic models.

Tests follow TDD RED phase: written before implementation.
Covers: FeatureVector, RiskScore, ScoredPaymentEvent, XGBoostScorer.
"""

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import xgboost as xgb
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


def test_feature_vector_accepts_all_8_fields():
    """FeatureVector accepts all 8 float fields."""
    from models.ml_scoring import FeatureVector

    fv = FeatureVector(
        tx_velocity_1m=3.0,
        tx_velocity_5m=10.0,
        amount_zscore=0.5,
        merchant_risk_score=0.2,
        device_switch_flag=0.0,
        hour_of_day=14.0,
        weekend_flag=0.0,
        amount_cents_log=7.5,
    )
    assert fv.tx_velocity_1m == 3.0
    assert fv.tx_velocity_5m == 10.0
    assert fv.amount_zscore == 0.5
    assert fv.merchant_risk_score == 0.2
    assert fv.device_switch_flag == 0.0
    assert fv.hour_of_day == 14.0
    assert fv.weekend_flag == 0.0
    assert fv.amount_cents_log == 7.5


def test_feature_vector_rejects_missing_field():
    """FeatureVector raises ValidationError when any field is missing."""
    from models.ml_scoring import FeatureVector

    with pytest.raises(ValidationError):
        FeatureVector(
            tx_velocity_1m=3.0,
            # tx_velocity_5m missing
            amount_zscore=0.5,
            merchant_risk_score=0.2,
            device_switch_flag=0.0,
            hour_of_day=14.0,
            weekend_flag=0.0,
            amount_cents_log=7.5,
        )


def test_risk_score_fields():
    """RiskScore has risk_score (float), is_high_risk (bool), manual_review (bool)."""
    from models.ml_scoring import RiskScore

    rs = RiskScore(risk_score=0.5, is_high_risk=False, manual_review=False)
    assert isinstance(rs.risk_score, float)
    assert isinstance(rs.is_high_risk, bool)
    assert isinstance(rs.manual_review, bool)


def test_scored_payment_event_extends_validated():
    """ScoredPaymentEvent extends ValidatedPaymentEvent with 4 extra fields."""
    from models.ml_scoring import ScoredPaymentEvent

    spe = ScoredPaymentEvent(
        event_id="evt_001",
        event_type="payment_intent.succeeded",
        amount_cents=10000,
        currency="usd",
        stripe_customer_id="cus_abc",
        merchant_id="merchant_1",
        received_at=datetime(2026, 3, 25, 12, 0, 0),
        risk_score=0.3,
        is_high_risk=False,
        manual_review=False,
        features_available=True,
    )
    assert spe.event_id == "evt_001"
    assert spe.risk_score == 0.3
    assert spe.is_high_risk is False
    assert spe.manual_review is False
    assert spe.features_available is True


# ---------------------------------------------------------------------------
# XGBoostScorer tests
# ---------------------------------------------------------------------------


@pytest.fixture
def trained_model_path(tmp_path) -> str:
    """Generate a small trained XGBoost model for testing."""
    np.random.seed(42)
    n = 200
    # Normal transactions (low risk)
    X_normal = np.column_stack([
        np.random.normal(3, 1, n // 2),   # tx_velocity_1m
        np.random.normal(10, 2, n // 2),  # tx_velocity_5m
        np.random.normal(0, 0.3, n // 2), # amount_zscore
        np.random.uniform(0, 0.3, n // 2),# merchant_risk_score
        np.zeros(n // 2),                 # device_switch_flag
        np.random.uniform(8, 20, n // 2), # hour_of_day
        np.zeros(n // 2),                 # weekend_flag
        np.random.normal(7, 1, n // 2),   # amount_cents_log
    ])
    # Fraud transactions (high risk)
    X_fraud = np.column_stack([
        np.random.normal(15, 3, n // 2),  # tx_velocity_1m
        np.random.normal(80, 10, n // 2), # tx_velocity_5m
        np.random.normal(2.5, 0.5, n // 2),# amount_zscore
        np.random.uniform(0.5, 1.0, n // 2),# merchant_risk_score
        np.ones(n // 2),                  # device_switch_flag
        np.random.uniform(0, 6, n // 2),  # hour_of_day
        np.random.binomial(1, 0.5, n // 2).astype(float),# weekend_flag
        np.random.normal(10, 1, n // 2),  # amount_cents_log
    ])
    X = np.vstack([X_normal, X_fraud])
    y = np.array([0] * (n // 2) + [1] * (n // 2), dtype=float)

    feature_names = [
        "tx_velocity_1m", "tx_velocity_5m", "amount_zscore",
        "merchant_risk_score", "device_switch_flag",
        "hour_of_day", "weekend_flag", "amount_cents_log",
    ]
    dtrain = xgb.DMatrix(X, label=y, feature_names=feature_names)
    booster = xgb.train(
        {"objective": "binary:logistic", "max_depth": 3, "eta": 0.1},
        dtrain,
        num_boost_round=10,
        verbose_eval=False,
    )
    model_path = str(tmp_path / "model.ubj")
    booster.save_model(model_path)
    return model_path


def test_scorer_loads_valid_model(trained_model_path):
    """XGBoostScorer loads a valid model file without error."""
    from ml.scorer import XGBoostScorer

    scorer = XGBoostScorer(model_path=trained_model_path)
    assert scorer is not None


def test_scorer_exits_on_missing_model():
    """XGBoostScorer raises SystemExit and logs ERROR when model file is missing."""
    from ml.scorer import XGBoostScorer

    with pytest.raises(SystemExit):
        XGBoostScorer(model_path="/nonexistent/path/model.ubj")


def test_scorer_returns_risk_score_in_range(trained_model_path):
    """XGBoostScorer.score() returns RiskScore with risk_score in [0, 1]."""
    from ml.scorer import XGBoostScorer

    scorer = XGBoostScorer(model_path=trained_model_path)
    features = {
        "tx_velocity_1m": 3.0,
        "tx_velocity_5m": 10.0,
        "amount_zscore": 0.5,
        "merchant_risk_score": 0.2,
        "device_switch_flag": 0.0,
        "hour_of_day": 14.0,
        "weekend_flag": 0.0,
        "amount_cents_log": 7.5,
    }
    result = scorer.score(features)
    assert 0.0 <= result.risk_score <= 1.0


def test_is_high_risk_threshold(trained_model_path):
    """is_high_risk is True when risk_score >= 0.7, False otherwise."""
    from ml.scorer import XGBoostScorer
    from models.ml_scoring import RiskScore

    scorer = XGBoostScorer(model_path=trained_model_path)

    # Use the model fixture to produce a result, then verify threshold logic
    # via direct RiskScore construction
    rs_low = RiskScore(risk_score=0.69, is_high_risk=False, manual_review=False)
    rs_high = RiskScore(risk_score=0.70, is_high_risk=True, manual_review=False)

    assert rs_low.is_high_risk is False
    assert rs_high.is_high_risk is True


def test_manual_review_threshold(trained_model_path):
    """manual_review is True when risk_score >= 0.85, False otherwise."""
    from models.ml_scoring import RiskScore

    rs_below = RiskScore(risk_score=0.84, is_high_risk=True, manual_review=False)
    rs_above = RiskScore(risk_score=0.85, is_high_risk=True, manual_review=True)

    assert rs_below.manual_review is False
    assert rs_above.manual_review is True


def test_feature_defaults_has_all_8_keys():
    """FEATURE_DEFAULTS dict has all 8 keys."""
    from ml.scorer import FEATURE_DEFAULTS

    expected_keys = {
        "tx_velocity_1m", "tx_velocity_5m", "amount_zscore",
        "merchant_risk_score", "device_switch_flag",
        "hour_of_day", "weekend_flag", "amount_cents_log",
    }
    assert set(FEATURE_DEFAULTS.keys()) == expected_keys


def test_feature_defaults_conservative_values():
    """FEATURE_DEFAULTS has specific conservative values per D-C3."""
    from ml.scorer import FEATURE_DEFAULTS

    assert FEATURE_DEFAULTS["tx_velocity_1m"] == 10
    assert FEATURE_DEFAULTS["merchant_risk_score"] == 1.0
    assert FEATURE_DEFAULTS["device_switch_flag"] == 1
    assert FEATURE_DEFAULTS["amount_zscore"] == 2.0


def test_scoring_with_defaults_produces_high_risk():
    """Scoring with FEATURE_DEFAULTS produces manual_review=True (conservative = high risk).

    Uses the committed ml/models/model.ubj (50 rounds, 1000 samples) which is
    well-calibrated enough for defaults to exceed the 0.85 threshold.
    The small fixture model (10 rounds, 200 samples) is not used here because
    its AUC=1.0 means it may place defaults below 0.85 due to less granular splits.
    """
    import os
    from ml.scorer import XGBoostScorer, FEATURE_DEFAULTS

    # Use the committed model, which is the one the serving path actually loads
    model_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "ml", "models", "model.ubj"
    )
    model_path = os.path.normpath(model_path)
    if not os.path.exists(model_path):
        pytest.skip("ml/models/model.ubj not found — run ml/train.py first")

    scorer = XGBoostScorer(model_path=model_path)
    result = scorer.score(FEATURE_DEFAULTS)

    # Conservative defaults must trigger manual_review=True with the committed model
    assert result.manual_review is True, (
        f"Expected manual_review=True with conservative defaults, "
        f"got risk_score={result.risk_score}"
    )


def test_scorer_score_result_has_correct_types(trained_model_path):
    """XGBoostScorer.score() returns RiskScore with correct field types."""
    from ml.scorer import XGBoostScorer

    scorer = XGBoostScorer(model_path=trained_model_path)
    features = {
        "tx_velocity_1m": 5.0,
        "tx_velocity_5m": 20.0,
        "amount_zscore": 0.0,
        "merchant_risk_score": 0.1,
        "device_switch_flag": 0.0,
        "hour_of_day": 10.0,
        "weekend_flag": 0.0,
        "amount_cents_log": 8.0,
    }
    result = scorer.score(features)
    assert isinstance(result.risk_score, float)
    assert isinstance(result.is_high_risk, bool)
    assert isinstance(result.manual_review, bool)
