"""Unit tests for LedgerEntryProducer — Phase 06.

Tests:
  - publish() produces JSON to payment.ledger.entry with correct key
  - publish() retries 3x with backoff [1, 2, 4]s on KafkaException
  - publish() re-raises KafkaException after all 3 retries exhausted
"""

import json
from unittest.mock import MagicMock, call, patch

import pytest
from confluent_kafka import KafkaException

from kafka.producers.ledger_entry_producer import LedgerEntryProducer, TOPIC


class TestLedgerEntryProducerPublish:
    """Tests for LedgerEntryProducer.publish()."""

    def _make_producer(self) -> tuple[LedgerEntryProducer, MagicMock]:
        """Return (LedgerEntryProducer, mock_confluent_producer)."""
        with patch("kafka.producers.ledger_entry_producer.Producer") as mock_cls:
            mock_kafka = MagicMock()
            mock_cls.return_value = mock_kafka
            producer = LedgerEntryProducer("localhost:9092")
        return producer, mock_kafka

    def test_topic_constant(self) -> None:
        assert TOPIC == "payment.ledger.entry"

    def test_publish_calls_produce_with_correct_topic(self) -> None:
        producer, mock_kafka = self._make_producer()
        event = {
            "event_id": "evt_001",
            "event_type": "payment_intent.succeeded",
            "amount_cents": 5000,
            "currency": "USD",
            "merchant_id": "merch_123",
            "source_event_id": "evt_001",
            "stripe_customer_id": "cus_abc",
            "risk_score": 0.1,
        }
        producer.publish("evt_001", event)

        mock_kafka.produce.assert_called_once()
        call_kwargs = mock_kafka.produce.call_args[1]
        assert call_kwargs["topic"] == "payment.ledger.entry"

    def test_publish_uses_stripe_event_id_as_key(self) -> None:
        producer, mock_kafka = self._make_producer()
        event = {"event_id": "evt_001", "amount_cents": 5000}
        producer.publish("evt_001", event)

        call_kwargs = mock_kafka.produce.call_args[1]
        assert call_kwargs["key"] == b"evt_001"

    def test_publish_serializes_event_as_json(self) -> None:
        producer, mock_kafka = self._make_producer()
        event = {"event_id": "evt_002", "amount_cents": 9999, "currency": "USD"}
        producer.publish("evt_002", event)

        call_kwargs = mock_kafka.produce.call_args[1]
        decoded = json.loads(call_kwargs["value"].decode("utf-8"))
        assert decoded == event

    def test_publish_calls_flush(self) -> None:
        producer, mock_kafka = self._make_producer()
        producer.publish("evt_003", {"event_id": "evt_003"})
        mock_kafka.flush.assert_called_with(timeout=5)

    def test_publish_retries_on_kafka_exception(self) -> None:
        producer, mock_kafka = self._make_producer()
        # Fail twice, succeed on third attempt
        kafka_exc = KafkaException("temporary failure")
        mock_kafka.produce.side_effect = [kafka_exc, kafka_exc, None]

        with patch("kafka.producers.ledger_entry_producer.time.sleep") as mock_sleep:
            producer.publish("evt_004", {"event_id": "evt_004"})

        assert mock_kafka.produce.call_count == 3
        # Slept after attempts 1 and 2 (not after final success)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    def test_publish_raises_after_all_retries_exhausted(self) -> None:
        producer, mock_kafka = self._make_producer()
        kafka_exc = KafkaException("persistent failure")
        mock_kafka.produce.side_effect = [kafka_exc, kafka_exc, kafka_exc]

        with patch("kafka.producers.ledger_entry_producer.time.sleep"):
            with pytest.raises(KafkaException):
                producer.publish("evt_005", {"event_id": "evt_005"})

        assert mock_kafka.produce.call_count == 3

    def test_close_calls_flush(self) -> None:
        producer, mock_kafka = self._make_producer()
        producer.close()
        mock_kafka.flush.assert_called_once()
