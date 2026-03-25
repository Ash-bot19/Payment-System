"""Unit tests for ScoringConsumer — Phase 05.

Tests consumer logic by mocking Redis, Kafka Consumer/Producer, and DB engine.
Uses the committed ml/model.ubj for real XGBoost inference in inference tests.

Coverage:
- _fetch_features: returns real features when Redis has feat:{event_id} hash
- _fetch_features: returns FEATURE_DEFAULTS after 3 retries when key missing
- _fetch_features: returns FEATURE_DEFAULTS immediately on TimeoutError
- _is_duplicate_scoring: returns True when SCORING row exists
- _is_duplicate_scoring: returns False when no SCORING row
- _process_message: writes SCORING then AUTHORIZED for low-risk score
- _process_message: writes SCORING then FLAGGED for high-risk score
- _process_message: publishes to payment.alert.triggered only when is_high_risk=True
- _process_message: skips processing when duplicate SCORING detected
- _process_message: sets manual_review=True when features_available=False
"""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure payment-backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Set required env vars before importing ScoringConsumer
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db")
os.environ.setdefault("ML_MODEL_PATH", "ml/models/model.ubj")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_consumer_no_init() -> "ScoringConsumer":  # type: ignore[name-defined]
    """Create a ScoringConsumer bypassing __init__ to avoid live connections."""
    from kafka.consumers.scoring_consumer import ScoringConsumer
    from ml.scorer import FEATURE_DEFAULTS, XGBoostScorer
    from prometheus_client import Counter

    consumer = object.__new__(ScoringConsumer)

    # Real scorer using committed model
    consumer._scorer = XGBoostScorer("ml/models/model.ubj")

    # Mock Kafka consumer
    consumer._consumer = MagicMock()

    # Mock Redis
    consumer._redis = MagicMock()

    # Mock state machine
    consumer._state_machine = MagicMock()
    consumer._state_machine._engine = MagicMock()

    # Mock Kafka producers
    consumer._scored_producer = MagicMock()
    consumer._alert_producer = MagicMock()

    # Real Prometheus counters (use unique names to avoid re-registration collision)
    import time as _time
    ts = str(int(_time.time() * 1000))
    consumer._feature_miss_counter = Counter(f"feature_miss_total_{ts}", "test")
    consumer._feature_timeout_counter = Counter(f"feature_timeout_total_{ts}", "test")
    consumer._events_scored_counter = Counter(f"events_scored_total_{ts}", "test")
    consumer._events_flagged_counter = Counter(f"events_flagged_total_{ts}", "test")

    consumer._running = True
    consumer._health_server = None

    return consumer


def _make_valid_event_dict(event_id: str = "evt_test_001") -> dict:
    """Return a ValidatedPaymentEvent dict for Kafka message payload."""
    return {
        "event_id": event_id,
        "event_type": "payment_intent.succeeded",
        "amount_cents": 5000,
        "currency": "usd",
        "stripe_customer_id": "cus_test_123",
        "merchant_id": "merchant_abc",
        "received_at": datetime.now(timezone.utc).isoformat(),
    }


def _make_kafka_msg(payload: dict) -> MagicMock:
    """Return a mock Kafka Message with JSON-encoded value."""
    msg = MagicMock()
    msg.value.return_value = json.dumps(payload).encode("utf-8")
    msg.partition.return_value = 0
    msg.offset.return_value = 42
    msg.error.return_value = None
    return msg


def _make_default_features() -> dict:
    """Return FEATURE_DEFAULTS for assertions."""
    from ml.scorer import FEATURE_DEFAULTS
    return dict(FEATURE_DEFAULTS)


# ---------------------------------------------------------------------------
# _fetch_features tests
# ---------------------------------------------------------------------------


def test_fetch_features_returns_real_features_when_redis_has_data():
    """_fetch_features returns real features and features_available=True when Redis hit."""
    consumer = _make_consumer_no_init()
    event_id = "evt_test_fetch_hit"

    redis_data = {
        "tx_velocity_1m": "1.0",
        "tx_velocity_5m": "3.0",
        "amount_zscore": "0.5",
        "merchant_risk_score": "0.3",
        "device_switch_flag": "0.0",
        "hour_of_day": "14.0",
        "weekend_flag": "0.0",
        "amount_cents_log": "7.5",
    }
    consumer._redis.hgetall.return_value = redis_data

    features, available = consumer._fetch_features(event_id)

    assert available is True
    assert features["tx_velocity_1m"] == 1.0
    assert features["amount_cents_log"] == 7.5
    consumer._redis.hgetall.assert_called_once_with(f"feat:{event_id}")


def test_fetch_features_returns_defaults_after_all_retries_when_key_missing():
    """_fetch_features returns FEATURE_DEFAULTS + features_available=False after 3 retries."""
    from kafka.consumers.scoring_consumer import FEATURE_RETRY_COUNT

    consumer = _make_consumer_no_init()
    event_id = "evt_test_fetch_miss"

    # Redis always returns empty dict (key not found)
    consumer._redis.hgetall.return_value = {}

    features, available = consumer._fetch_features(event_id)

    assert available is False
    assert features == _make_default_features()
    # Should have been called FEATURE_RETRY_COUNT times
    assert consumer._redis.hgetall.call_count == FEATURE_RETRY_COUNT


def test_fetch_features_returns_defaults_immediately_on_timeout():
    """_fetch_features returns FEATURE_DEFAULTS + features_available=False on TimeoutError."""
    import redis as redis_lib

    consumer = _make_consumer_no_init()
    event_id = "evt_test_fetch_timeout"

    consumer._redis.hgetall.side_effect = redis_lib.exceptions.TimeoutError()

    features, available = consumer._fetch_features(event_id)

    assert available is False
    assert features == _make_default_features()
    # Only one attempt — immediate fallback on timeout
    consumer._redis.hgetall.assert_called_once()


# ---------------------------------------------------------------------------
# _is_duplicate_scoring tests
# ---------------------------------------------------------------------------


def test_is_duplicate_scoring_returns_true_when_scoring_row_exists():
    """_is_duplicate_scoring returns True when payment_state_log has SCORING row."""
    consumer = _make_consumer_no_init()
    event_id = "evt_test_dup_01"

    # Mock DB connection: fetchone returns a row (non-None)
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchone.return_value = (1,)
    consumer._state_machine._engine.connect.return_value = mock_conn

    result = consumer._is_duplicate_scoring(event_id)

    assert result is True


def test_is_duplicate_scoring_returns_false_when_no_scoring_row():
    """_is_duplicate_scoring returns False when no SCORING row in payment_state_log."""
    consumer = _make_consumer_no_init()
    event_id = "evt_test_nodup_01"

    # Mock DB connection: fetchone returns None (no row)
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchone.return_value = None
    consumer._state_machine._engine.connect.return_value = mock_conn

    result = consumer._is_duplicate_scoring(event_id)

    assert result is False


# ---------------------------------------------------------------------------
# _process_message tests
# ---------------------------------------------------------------------------


def test_process_message_writes_scoring_then_authorized_for_low_risk():
    """_process_message writes SCORING then AUTHORIZED for low-risk event (risk < 0.7)."""
    from models.state_machine import PaymentState

    consumer = _make_consumer_no_init()

    # No duplicate
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchone.return_value = None
    consumer._state_machine._engine.connect.return_value = mock_conn

    # Redis returns real features
    consumer._redis.hgetall.return_value = {
        "tx_velocity_1m": "1.0", "tx_velocity_5m": "2.0",
        "amount_zscore": "0.1", "merchant_risk_score": "0.1",
        "device_switch_flag": "0.0", "hour_of_day": "10.0",
        "weekend_flag": "0.0", "amount_cents_log": "6.0",
    }

    # Patch scorer to return low risk score (< 0.7)
    from models.ml_scoring import RiskScore
    consumer._scorer = MagicMock()
    consumer._scorer.score.return_value = RiskScore(
        risk_score=0.2, is_high_risk=False, manual_review=False
    )

    event_id = "evt_low_risk_001"
    msg = _make_kafka_msg(_make_valid_event_dict(event_id))
    consumer._process_message(msg)

    # Verify write_transition calls
    calls = consumer._state_machine.write_transition.call_args_list
    assert len(calls) == 2, f"Expected 2 write_transition calls, got {len(calls)}"

    # First call: VALIDATED → SCORING
    first_call_kwargs = calls[0][1]
    assert first_call_kwargs["from_state"] == PaymentState.VALIDATED
    assert first_call_kwargs["to_state"] == PaymentState.SCORING

    # Second call: SCORING → AUTHORIZED
    second_call_kwargs = calls[1][1]
    assert second_call_kwargs["from_state"] == PaymentState.SCORING
    assert second_call_kwargs["to_state"] == PaymentState.AUTHORIZED


def test_process_message_writes_scoring_then_flagged_for_high_risk():
    """_process_message writes SCORING then FLAGGED for high-risk event (risk >= 0.7)."""
    from models.state_machine import PaymentState

    consumer = _make_consumer_no_init()

    # No duplicate
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchone.return_value = None
    consumer._state_machine._engine.connect.return_value = mock_conn

    # Redis returns real features
    consumer._redis.hgetall.return_value = {
        "tx_velocity_1m": "10.0", "tx_velocity_5m": "50.0",
        "amount_zscore": "3.0", "merchant_risk_score": "1.0",
        "device_switch_flag": "1.0", "hour_of_day": "3.0",
        "weekend_flag": "0.0", "amount_cents_log": "0.0",
    }

    # Patch scorer to return high risk score (>= 0.7)
    from models.ml_scoring import RiskScore
    consumer._scorer = MagicMock()
    consumer._scorer.score.return_value = RiskScore(
        risk_score=0.85, is_high_risk=True, manual_review=True
    )

    event_id = "evt_high_risk_001"
    msg = _make_kafka_msg(_make_valid_event_dict(event_id))
    consumer._process_message(msg)

    calls = consumer._state_machine.write_transition.call_args_list
    assert len(calls) == 2

    second_call_kwargs = calls[1][1]
    assert second_call_kwargs["from_state"] == PaymentState.SCORING
    assert second_call_kwargs["to_state"] == PaymentState.FLAGGED


def test_process_message_publishes_alert_only_for_high_risk():
    """_process_message publishes to payment.alert.triggered only when is_high_risk=True."""
    consumer = _make_consumer_no_init()

    # No duplicate
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchone.return_value = None
    consumer._state_machine._engine.connect.return_value = mock_conn

    consumer._redis.hgetall.return_value = {
        "tx_velocity_1m": "1.0", "tx_velocity_5m": "2.0",
        "amount_zscore": "0.1", "merchant_risk_score": "0.1",
        "device_switch_flag": "0.0", "hour_of_day": "10.0",
        "weekend_flag": "0.0", "amount_cents_log": "6.0",
    }

    from models.ml_scoring import RiskScore

    # Test 1: low risk — no alert
    consumer._scorer = MagicMock()
    consumer._scorer.score.return_value = RiskScore(
        risk_score=0.2, is_high_risk=False, manual_review=False
    )
    consumer._state_machine.write_transition.reset_mock()
    consumer._alert_producer.publish.reset_mock()

    msg = _make_kafka_msg(_make_valid_event_dict("evt_no_alert_001"))
    consumer._process_message(msg)
    consumer._alert_producer.publish.assert_not_called()

    # Reset for high-risk test
    mock_conn.execute.return_value.fetchone.return_value = None
    consumer._state_machine.write_transition.reset_mock()
    consumer._scored_producer.publish.reset_mock()
    consumer._alert_producer.publish.reset_mock()

    # Test 2: high risk — alert published
    consumer._scorer.score.return_value = RiskScore(
        risk_score=0.9, is_high_risk=True, manual_review=True
    )
    msg = _make_kafka_msg(_make_valid_event_dict("evt_alert_001"))
    consumer._process_message(msg)
    consumer._alert_producer.publish.assert_called_once()
    consumer._scored_producer.publish.assert_called_once()


def test_process_message_skips_on_duplicate_scoring():
    """_process_message skips processing when duplicate SCORING state detected."""
    consumer = _make_consumer_no_init()

    # Simulate duplicate — SCORING row exists
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchone.return_value = (1,)  # row exists
    consumer._state_machine._engine.connect.return_value = mock_conn

    msg = _make_kafka_msg(_make_valid_event_dict("evt_dup_skip_001"))
    consumer._process_message(msg)

    # No DB writes, no Kafka publishes
    consumer._state_machine.write_transition.assert_not_called()
    consumer._scored_producer.publish.assert_not_called()
    consumer._alert_producer.publish.assert_not_called()

    # Offset was committed (idempotent skip)
    consumer._consumer.store_offsets.assert_called_once()
    consumer._consumer.commit.assert_called_once()


def test_process_message_sets_manual_review_when_features_unavailable():
    """_process_message forces manual_review=True when features_available=False."""
    consumer = _make_consumer_no_init()

    # No duplicate
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.fetchone.return_value = None
    consumer._state_machine._engine.connect.return_value = mock_conn

    # Redis returns empty (features unavailable after retries)
    consumer._redis.hgetall.return_value = {}

    # Scorer returns manual_review=False (would be overridden)
    from models.ml_scoring import RiskScore
    consumer._scorer = MagicMock()
    consumer._scorer.score.return_value = RiskScore(
        risk_score=0.3, is_high_risk=False, manual_review=False
    )

    captured_scored_event = {}

    def capture_publish(stripe_event_id, scored_event):
        captured_scored_event.update(scored_event)

    consumer._scored_producer.publish.side_effect = capture_publish

    msg = _make_kafka_msg(_make_valid_event_dict("evt_no_feat_001"))
    consumer._process_message(msg)

    # manual_review must be True (belt-and-suspenders per D-C3)
    assert captured_scored_event.get("manual_review") is True
    assert captured_scored_event.get("features_available") is False
