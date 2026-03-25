"""Unit tests for FastAPI ml_service — Phase 05.

Tests POST /score and GET /health using TestClient (no live services needed).
The XGBoostScorer is loaded from the committed ml/model.ubj file.
"""

import os
import sys

import pytest

# Ensure payment-backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Set ML_MODEL_PATH to committed model before importing app
os.environ.setdefault("ML_MODEL_PATH", "ml/models/model.ubj")


@pytest.fixture(scope="module")
def client():
    """TestClient with model loaded via lifespan."""
    from fastapi.testclient import TestClient
    from services.ml_service import app

    with TestClient(app) as c:
        yield c


def _valid_feature_body() -> dict:
    """Return a valid 8-field FeatureVector dict for POST /score."""
    return {
        "tx_velocity_1m": 1.0,
        "tx_velocity_5m": 3.0,
        "amount_zscore": 0.5,
        "merchant_risk_score": 0.3,
        "device_switch_flag": 0.0,
        "hour_of_day": 14.0,
        "weekend_flag": 0.0,
        "amount_cents_log": 7.5,
    }


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


def test_health_returns_200(client):
    """GET /health returns HTTP 200."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_reports_model_loaded(client):
    """GET /health returns model_loaded=true when model is loaded."""
    response = client.get("/health")
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True


# ---------------------------------------------------------------------------
# POST /score — happy path
# ---------------------------------------------------------------------------


def test_score_returns_200_with_valid_features(client):
    """POST /score with valid 8-field body returns HTTP 200."""
    response = client.post("/score", json=_valid_feature_body())
    assert response.status_code == 200


def test_score_returns_risk_score_in_range(client):
    """POST /score risk_score is clamped to [0.0, 1.0]."""
    response = client.post("/score", json=_valid_feature_body())
    body = response.json()
    assert 0.0 <= body["risk_score"] <= 1.0


def test_score_returns_is_high_risk_field(client):
    """POST /score response contains is_high_risk boolean field."""
    response = client.post("/score", json=_valid_feature_body())
    body = response.json()
    assert isinstance(body["is_high_risk"], bool)


def test_score_returns_manual_review_field(client):
    """POST /score response contains manual_review boolean field."""
    response = client.post("/score", json=_valid_feature_body())
    body = response.json()
    assert isinstance(body["manual_review"], bool)


def test_score_is_high_risk_threshold(client):
    """is_high_risk = True iff risk_score >= 0.7."""
    response = client.post("/score", json=_valid_feature_body())
    body = response.json()
    if body["risk_score"] >= 0.7:
        assert body["is_high_risk"] is True
    else:
        assert body["is_high_risk"] is False


def test_score_manual_review_threshold(client):
    """manual_review = True iff risk_score >= 0.85."""
    response = client.post("/score", json=_valid_feature_body())
    body = response.json()
    if body["risk_score"] >= 0.85:
        assert body["manual_review"] is True
    else:
        assert body["manual_review"] is False


# ---------------------------------------------------------------------------
# POST /score — validation errors
# ---------------------------------------------------------------------------


def test_score_missing_field_returns_422(client):
    """POST /score with a missing required field returns HTTP 422."""
    body = _valid_feature_body()
    del body["amount_cents_log"]
    response = client.post("/score", json=body)
    assert response.status_code == 422


def test_score_extra_field_ignored(client):
    """POST /score ignores extra fields (Pydantic v2 default)."""
    body = _valid_feature_body()
    body["unexpected_field"] = 99.9
    response = client.post("/score", json=body)
    assert response.status_code == 200


def test_score_wrong_type_returns_422(client):
    """POST /score with non-numeric field returns HTTP 422."""
    body = _valid_feature_body()
    body["tx_velocity_1m"] = "not_a_float"
    response = client.post("/score", json=body)
    assert response.status_code == 422
