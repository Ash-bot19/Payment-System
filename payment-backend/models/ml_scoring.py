"""Pydantic v2 models for ML risk scoring — Phase 05.

Exports:
    FeatureVector        -- 8-field input contract for XGBoost inference
    RiskScore            -- Output contract: risk_score, is_high_risk, manual_review
    ScoredPaymentEvent   -- Extends ValidatedPaymentEvent with scoring fields
"""

from pydantic import BaseModel

from models.validation import ValidatedPaymentEvent


class FeatureVector(BaseModel):
    """8 ML input features per CLAUDE.md ML Risk Score Contract (LOCKED).

    All fields are float. Field names match the Redis hash keys written by
    the Spark feature engineering pipeline (spark/redis_sink.py).
    """

    tx_velocity_1m: float
    tx_velocity_5m: float
    amount_zscore: float
    merchant_risk_score: float
    device_switch_flag: float
    hour_of_day: float
    weekend_flag: float
    amount_cents_log: float


class RiskScore(BaseModel):
    """XGBoost inference output per CLAUDE.md ML Risk Score Contract (LOCKED).

    Thresholds:
        is_high_risk  -- True when risk_score >= 0.7
        manual_review -- True when risk_score >= 0.85
    """

    risk_score: float       # clamped to [0.0, 1.0]
    is_high_risk: bool      # risk_score >= 0.7
    manual_review: bool     # risk_score >= 0.85


class ScoredPaymentEvent(ValidatedPaymentEvent):
    """ValidatedPaymentEvent enriched with ML scoring results.

    Published to payment.transaction.scored after inference.
    Published to payment.alert.triggered when is_high_risk=True.
    """

    risk_score: float
    is_high_risk: bool
    manual_review: bool
    features_available: bool  # True = real Spark features used; False = FEATURE_DEFAULTS
