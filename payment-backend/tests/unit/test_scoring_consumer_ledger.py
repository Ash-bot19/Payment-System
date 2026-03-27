"""Unit tests for ScoringConsumer ledger + manual review wiring — Phase 06.

Tests that:
  - AUTHORIZED events (risk < 0.7) cause _ledger_producer.publish() to be called
    with the correct ledger_event dict
  - FLAGGED events (risk >= 0.7) do NOT call _ledger_producer.publish()
  - FLAGGED + manual_review=True events call _review_repo.insert()
  - FLAGGED + manual_review=False events do NOT call _review_repo.insert()
  - _cleanup() calls _ledger_producer.close()
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from models.state_machine import PaymentState
from models.validation import ValidatedPaymentEvent
from models.ml_scoring import ScoredPaymentEvent, RiskScore


def _make_validated_event(
    event_id: str = "evt_test_001",
    amount_cents: int = 5000,
    risk_score: float = 0.2,
) -> ValidatedPaymentEvent:
    return ValidatedPaymentEvent(
        event_id=event_id,
        event_type="payment_intent.succeeded",
        amount_cents=amount_cents,
        currency="USD",
        stripe_customer_id="cus_abc123",
        merchant_id="merch_xyz",
        received_at=datetime(2026, 3, 27, 10, 0, 0, tzinfo=timezone.utc),
    )


def _make_scored_event(
    validated: ValidatedPaymentEvent,
    risk_score: float,
    is_high_risk: bool,
    manual_review: bool,
) -> ScoredPaymentEvent:
    return ScoredPaymentEvent(
        event_id=validated.event_id,
        event_type=validated.event_type,
        amount_cents=validated.amount_cents,
        currency=validated.currency,
        stripe_customer_id=validated.stripe_customer_id,
        merchant_id=validated.merchant_id,
        received_at=validated.received_at,
        risk_score=risk_score,
        is_high_risk=is_high_risk,
        manual_review=manual_review,
        features_available=True,
    )


def _make_consumer_with_mocks() -> tuple:
    """
    Create a ScoringConsumer instance with all external dependencies mocked.
    Returns (consumer, mock_ledger_producer, mock_review_repo).
    """
    # Patch Counter to avoid duplicate registration across test instances
    mock_counter = MagicMock()
    mock_counter.return_value = MagicMock()

    with patch("kafka.consumers.scoring_consumer.Consumer"), \
         patch("kafka.consumers.scoring_consumer.XGBoostScorer"), \
         patch("kafka.consumers.scoring_consumer.redis.Redis.from_url"), \
         patch("kafka.consumers.scoring_consumer.create_engine"), \
         patch("kafka.consumers.scoring_consumer.PaymentStateMachine"), \
         patch("kafka.consumers.scoring_consumer.ScoredEventProducer"), \
         patch("kafka.consumers.scoring_consumer.AlertProducer"), \
         patch("kafka.consumers.scoring_consumer.LedgerEntryProducer") as mock_ledger_cls, \
         patch("kafka.consumers.scoring_consumer.ManualReviewRepository") as mock_repo_cls, \
         patch("kafka.consumers.scoring_consumer.Counter", mock_counter), \
         patch.dict("os.environ", {
             "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
             "REDIS_URL": "redis://localhost:6379/0",
             "DATABASE_URL_SYNC": "postgresql://payment:payment@localhost:5432/payment_db",
             "ML_MODEL_PATH": "ml/models/model.ubj",
         }):
        mock_ledger_producer = MagicMock()
        mock_ledger_cls.return_value = mock_ledger_producer

        mock_review_repo = MagicMock()
        mock_repo_cls.return_value = mock_review_repo

        from kafka.consumers.scoring_consumer import ScoringConsumer
        consumer = ScoringConsumer("localhost:9092")

    return consumer, mock_ledger_producer, mock_review_repo


def _build_mock_message(validated_event: ValidatedPaymentEvent) -> MagicMock:
    """Build a mock Kafka message carrying a ValidatedPaymentEvent JSON payload."""
    msg = MagicMock()
    msg.value.return_value = json.dumps(
        validated_event.model_dump(mode="json")
    ).encode("utf-8")
    msg.partition.return_value = 0
    msg.offset.return_value = 100
    return msg


class TestScoringConsumerLedgerWiring:
    """Tests for ledger publish wiring in ScoringConsumer._process_message()."""

    def _setup_authorized(self) -> tuple:
        """Set up consumer for an AUTHORIZED event (risk_score = 0.2)."""
        consumer, mock_ledger, mock_repo = _make_consumer_with_mocks()

        validated = _make_validated_event(risk_score=0.2)
        risk_result = RiskScore(risk_score=0.2, is_high_risk=False, manual_review=False)
        scored = _make_scored_event(validated, 0.2, False, False)

        # Mock scorer output
        consumer._scorer.score.return_value = risk_result

        # Mock Redis to return empty (triggers feature miss → defaults)
        consumer._redis.hgetall.return_value = {}

        # Mock idempotency check (not a duplicate)
        consumer._state_machine._engine.connect.return_value.__enter__ = MagicMock(
            return_value=MagicMock(execute=MagicMock(return_value=MagicMock(fetchone=MagicMock(return_value=None))))
        )
        consumer._state_machine._engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        msg = _build_mock_message(validated)
        return consumer, mock_ledger, mock_repo, validated, risk_result, msg

    def _setup_flagged(self, manual_review: bool = True) -> tuple:
        """Set up consumer for a FLAGGED event (risk_score = 0.9).

        When manual_review=False we return real Redis features so that
        step 7 (belt-and-suspenders override) doesn't force manual_review=True.
        """
        consumer, mock_ledger, mock_repo = _make_consumer_with_mocks()

        validated = _make_validated_event(risk_score=0.9)
        risk_result = RiskScore(risk_score=0.9, is_high_risk=True, manual_review=manual_review)
        scored = _make_scored_event(validated, 0.9, True, manual_review)

        consumer._scorer.score.return_value = risk_result

        if manual_review:
            # Features miss is fine — manual_review=True is already set
            consumer._redis.hgetall.return_value = {}
        else:
            # Return real features so features_available=True → step 7 doesn't override
            consumer._redis.hgetall.return_value = {
                "tx_velocity_1m": "1.0",
                "tx_velocity_5m": "2.0",
                "amount_zscore": "0.5",
                "merchant_risk_score": "0.3",
                "device_switch_flag": "0.0",
                "hour_of_day": "10.0",
                "weekend_flag": "0.0",
                "amount_cents_log": "8.5",
            }

        consumer._state_machine._engine.connect.return_value.__enter__ = MagicMock(
            return_value=MagicMock(execute=MagicMock(return_value=MagicMock(fetchone=MagicMock(return_value=None))))
        )
        consumer._state_machine._engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        msg = _build_mock_message(validated)
        return consumer, mock_ledger, mock_repo, validated, risk_result, msg

    def test_authorized_event_publishes_to_ledger(self) -> None:
        """AUTHORIZED events (risk < 0.7) must publish to payment.ledger.entry."""
        consumer, mock_ledger, mock_repo, validated, risk_result, msg = self._setup_authorized()

        consumer._process_message(msg)

        mock_ledger.publish.assert_called_once()
        call_kwargs = mock_ledger.publish.call_args[1]
        assert call_kwargs["stripe_event_id"] == validated.event_id

    def test_authorized_event_ledger_event_contains_required_fields(self) -> None:
        """Ledger event dict must contain all required fields."""
        consumer, mock_ledger, mock_repo, validated, risk_result, msg = self._setup_authorized()

        consumer._process_message(msg)

        call_kwargs = mock_ledger.publish.call_args[1]
        ledger_event = call_kwargs["ledger_event"]
        assert ledger_event["event_id"] == validated.event_id
        assert ledger_event["amount_cents"] == validated.amount_cents
        assert ledger_event["currency"] == validated.currency
        assert ledger_event["merchant_id"] == validated.merchant_id
        assert ledger_event["source_event_id"] == validated.event_id
        assert "risk_score" in ledger_event

    def test_flagged_event_does_not_publish_to_ledger(self) -> None:
        """FLAGGED events (risk >= 0.7) must NOT publish to payment.ledger.entry."""
        consumer, mock_ledger, mock_repo, validated, risk_result, msg = self._setup_flagged(manual_review=True)

        consumer._process_message(msg)

        mock_ledger.publish.assert_not_called()

    def test_flagged_with_manual_review_queues_for_review(self) -> None:
        """FLAGGED + manual_review=True events must insert into manual_review_queue."""
        consumer, mock_ledger, mock_repo, validated, risk_result, msg = self._setup_flagged(manual_review=True)

        consumer._process_message(msg)

        mock_repo.insert.assert_called_once()
        call_kwargs = mock_repo.insert.call_args[1]
        assert call_kwargs["transaction_id"] == validated.event_id
        assert call_kwargs["risk_score"] == risk_result.risk_score
        assert isinstance(call_kwargs["payload"], dict)

    def test_flagged_without_manual_review_does_not_queue(self) -> None:
        """FLAGGED + manual_review=False events (0.7 <= risk < 0.85) must NOT insert."""
        consumer, mock_ledger, mock_repo, validated, risk_result, msg = self._setup_flagged(manual_review=False)

        consumer._process_message(msg)

        mock_repo.insert.assert_not_called()

    def test_cleanup_closes_ledger_producer(self) -> None:
        """_cleanup() must call _ledger_producer.close()."""
        consumer, mock_ledger, mock_repo, *_ = _make_consumer_with_mocks()

        consumer._cleanup()

        mock_ledger.close.assert_called_once()


class TestScoringConsumerCounters:
    """Tests for Prometheus counter wiring in ScoringConsumer."""

    def test_ledger_published_counter_exists(self) -> None:
        """ScoringConsumer must have ledger_published_total counter."""
        consumer, _, _ = _make_consumer_with_mocks()
        assert hasattr(consumer, "_ledger_published_counter")

    def test_manual_review_counter_exists(self) -> None:
        """ScoringConsumer must have manual_review_queued_total counter."""
        consumer, _, _ = _make_consumer_with_mocks()
        assert hasattr(consumer, "_manual_review_counter")
