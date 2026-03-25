"""Integration tests for Phase 5: ML risk scoring pipeline.

Requires docker-compose services running: PostgreSQL, Redis.
Run: cd payment-backend && python -m pytest tests/integration/test_phase5_integration.py -v

Coverage:
- SCORING-CONSUMER: Full scoring pipeline — Redis features → inference → risk_score in [0,1]
- STATE-WRITES: SCORING + AUTHORIZED/FLAGGED rows written to payment_state_log
- REDIS-FEATURE-LOOKUP: Feature miss fallback returns FEATURE_DEFAULTS after retries
- IDEMPOTENCY-GUARD: _is_duplicate_scoring detects existing SCORING row
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

# Ensure payment-backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Set required env vars before importing consumer
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db")
os.environ.setdefault("ML_MODEL_PATH", "ml/models/model.ubj")


# ---------------------------------------------------------------------------
# Skip guard — require live Redis and PostgreSQL
# ---------------------------------------------------------------------------


def _require_redis():
    """Skip if Redis is not reachable."""
    import redis as redis_lib
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis_lib.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        return client
    except Exception:
        pytest.skip("Redis not available — skipping integration test")


def _require_db_engine():
    """Skip if PostgreSQL is not reachable."""
    from sqlalchemy import create_engine, text
    url = os.getenv("DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db")
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:
        pytest.skip("PostgreSQL not available — skipping integration test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scorer_consumer_components():
    """Build ScoringConsumer components without Kafka (for isolated testing).

    Returns (consumer, redis_client, db_engine) where consumer has live
    Redis and DB connections but mocked Kafka components.
    """
    import redis as redis_lib
    from kafka.consumers.scoring_consumer import ScoringConsumer
    from ml.scorer import FEATURE_DEFAULTS, XGBoostScorer
    from prometheus_client import Counter
    from services.state_machine import PaymentStateMachine
    from sqlalchemy import create_engine
    import time as _time

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    db_url = os.getenv("DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db")

    consumer = object.__new__(ScoringConsumer)
    consumer._scorer = XGBoostScorer("ml/models/model.ubj")
    consumer._redis = redis_lib.Redis.from_url(
        redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1
    )
    db_engine = create_engine(db_url)
    consumer._state_machine = PaymentStateMachine(db_engine)
    consumer._consumer = MagicMock()
    consumer._scored_producer = MagicMock()
    consumer._alert_producer = MagicMock()
    consumer._running = True
    consumer._health_server = None

    # Unique counter names per test session to avoid Prometheus re-registration
    ts = str(int(_time.time() * 1000))
    consumer._feature_miss_counter = Counter(f"p5_feature_miss_{ts}", "test")
    consumer._feature_timeout_counter = Counter(f"p5_feature_timeout_{ts}", "test")
    consumer._events_scored_counter = Counter(f"p5_events_scored_{ts}", "test")
    consumer._events_flagged_counter = Counter(f"p5_events_flagged_{ts}", "test")

    return consumer, db_engine


# ---------------------------------------------------------------------------
# SCORING-CONSUMER: Full pipeline test
# ---------------------------------------------------------------------------


def test_full_scoring_pipeline_risk_score_in_range():
    """Full pipeline: write features to Redis, run inference, verify risk_score in [0,1].

    Uses live Redis for feat:{event_id} lookup + real XGBoostScorer inference.
    """
    redis_client = _require_redis()
    consumer, db_engine = _make_scorer_consumer_components()

    event_id = f"evt_integ_{uuid4().hex[:8]}"

    # Write 8 ML features to Redis
    feature_hash = {
        "tx_velocity_1m": "1.0",
        "tx_velocity_5m": "3.0",
        "amount_zscore": "0.5",
        "merchant_risk_score": "0.3",
        "device_switch_flag": "0.0",
        "hour_of_day": "14.0",
        "weekend_flag": "0.0",
        "amount_cents_log": "7.5",
    }
    redis_client.hset(f"feat:{event_id}", mapping=feature_hash)
    redis_client.expire(f"feat:{event_id}", 60)  # 60s TTL for test cleanup

    try:
        # Fetch features via consumer method
        features, available = consumer._fetch_features(event_id)
        assert available is True, "Expected features_available=True with Redis data"

        # Run inference via real XGBoostScorer
        risk_result = consumer._scorer.score(features)

        assert 0.0 <= risk_result.risk_score <= 1.0, (
            f"risk_score out of range: {risk_result.risk_score}"
        )
        assert isinstance(risk_result.is_high_risk, bool)
        assert isinstance(risk_result.manual_review, bool)

        # Threshold consistency checks
        if risk_result.risk_score >= 0.7:
            assert risk_result.is_high_risk is True
        else:
            assert risk_result.is_high_risk is False

        if risk_result.risk_score >= 0.85:
            assert risk_result.manual_review is True
    finally:
        redis_client.delete(f"feat:{event_id}")


# ---------------------------------------------------------------------------
# STATE-WRITES: SCORING + AUTHORIZED/FLAGGED rows
# ---------------------------------------------------------------------------


def test_state_machine_writes_scoring_then_authorized():
    """State machine writes SCORING then AUTHORIZED rows to payment_state_log.

    Requires live PostgreSQL with payment_state_log table.
    Uses UUID-keyed rows for test isolation (append-only table).
    """
    from sqlalchemy import text

    _require_redis()
    consumer, db_engine = _make_scorer_consumer_components()

    event_id = f"evt_auth_{uuid4().hex[:8]}"
    merchant_id = "test_merchant_p5_auth"

    from models.state_machine import PaymentState
    from services.state_machine import PaymentStateMachine

    sm = PaymentStateMachine(db_engine)

    # Write SCORING state
    sm.write_transition(event_id, event_id, merchant_id, PaymentState.VALIDATED, PaymentState.SCORING)
    # Write AUTHORIZED state
    sm.write_transition(event_id, event_id, merchant_id, PaymentState.SCORING, PaymentState.AUTHORIZED)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT from_state, to_state FROM payment_state_log "
                "WHERE event_id = :eid ORDER BY id ASC"
            ),
            {"eid": event_id},
        ).fetchall()

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    assert rows[0].from_state == "VALIDATED"
    assert rows[0].to_state == "SCORING"
    assert rows[1].from_state == "SCORING"
    assert rows[1].to_state == "AUTHORIZED"


def test_state_machine_writes_scoring_then_flagged():
    """State machine writes SCORING then FLAGGED rows to payment_state_log.

    Requires live PostgreSQL.
    """
    from sqlalchemy import text

    _require_redis()
    consumer, db_engine = _make_scorer_consumer_components()

    event_id = f"evt_flag_{uuid4().hex[:8]}"
    merchant_id = "test_merchant_p5_flag"

    from models.state_machine import PaymentState
    from services.state_machine import PaymentStateMachine

    sm = PaymentStateMachine(db_engine)
    sm.write_transition(event_id, event_id, merchant_id, PaymentState.VALIDATED, PaymentState.SCORING)
    sm.write_transition(event_id, event_id, merchant_id, PaymentState.SCORING, PaymentState.FLAGGED)

    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT from_state, to_state FROM payment_state_log "
                "WHERE event_id = :eid ORDER BY id ASC"
            ),
            {"eid": event_id},
        ).fetchall()

    assert len(rows) == 2
    assert rows[1].to_state == "FLAGGED"


# ---------------------------------------------------------------------------
# REDIS-FEATURE-LOOKUP: Feature miss fallback
# ---------------------------------------------------------------------------


def test_feature_miss_fallback_returns_defaults():
    """_fetch_features returns FEATURE_DEFAULTS when key not in Redis.

    Does NOT write features to Redis for this event_id — verifies
    that after FEATURE_RETRY_COUNT attempts, defaults are returned.
    """
    from ml.scorer import FEATURE_DEFAULTS

    _require_redis()
    consumer, _ = _make_scorer_consumer_components()

    # Use a unique event_id that has no Redis key
    event_id = f"evt_nomiss_{uuid4().hex[:8]}"

    # Ensure key doesn't exist
    consumer._redis.delete(f"feat:{event_id}")

    features, available = consumer._fetch_features(event_id)

    assert available is False, "Expected features_available=False for missing key"
    assert features == dict(FEATURE_DEFAULTS), (
        f"Expected FEATURE_DEFAULTS, got {features}"
    )


# ---------------------------------------------------------------------------
# IDEMPOTENCY-GUARD: Duplicate SCORING detection
# ---------------------------------------------------------------------------


def test_idempotency_guard_detects_existing_scoring_row():
    """_is_duplicate_scoring returns True when SCORING row exists.

    Writes a SCORING row directly, then verifies idempotency guard fires.
    """
    from sqlalchemy import text

    _require_redis()
    consumer, db_engine = _make_scorer_consumer_components()

    event_id = f"evt_idem_{uuid4().hex[:8]}"
    merchant_id = "test_merchant_p5_idem"

    from models.state_machine import PaymentState
    from services.state_machine import PaymentStateMachine

    sm = PaymentStateMachine(db_engine)

    # Insert a SCORING row
    sm.write_transition(event_id, event_id, merchant_id, PaymentState.VALIDATED, PaymentState.SCORING)

    # Verify idempotency guard fires
    result = consumer._is_duplicate_scoring(event_id)
    assert result is True, "Expected _is_duplicate_scoring to return True"


def test_idempotency_guard_returns_false_for_new_event():
    """_is_duplicate_scoring returns False for an event with no SCORING row."""
    _require_redis()
    consumer, _ = _make_scorer_consumer_components()

    # Unique event that has never been processed
    event_id = f"evt_new_{uuid4().hex[:8]}"

    result = consumer._is_duplicate_scoring(event_id)
    assert result is False, "Expected _is_duplicate_scoring to return False for new event"
