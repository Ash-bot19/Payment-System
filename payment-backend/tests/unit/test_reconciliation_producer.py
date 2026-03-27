"""Unit tests for ReconciliationProducer — Phase 07.

Tests:
  - publish() produces JSON to payment.reconciliation.queue with correct key
  - publish() retries 3x with backoff [1, 2, 4]s on KafkaException
  - publish() re-raises KafkaException after all 3 retries exhausted (crash-on-exhaustion)
  - publish_batch() iterates messages and calls publish() per message
"""

import json
from unittest.mock import MagicMock, call, patch

import pytest
from confluent_kafka import KafkaException

from kafka.producers.reconciliation_producer import ReconciliationProducer, TOPIC


class TestReconciliationProducerPublish:
    """Tests for ReconciliationProducer.publish()."""

    def _make_producer(self) -> tuple[ReconciliationProducer, MagicMock]:
        """Return (ReconciliationProducer, mock_confluent_producer)."""
        with patch("kafka.producers.reconciliation_producer.Producer") as mock_cls:
            mock_kafka = MagicMock()
            mock_cls.return_value = mock_kafka
            producer = ReconciliationProducer("localhost:9092")
        return producer, mock_kafka

    def test_topic_constant(self) -> None:
        assert TOPIC == "payment.reconciliation.queue"

    def test_publish_calls_produce_with_correct_topic(self) -> None:
        producer, mock_kafka = self._make_producer()
        message = {
            "transaction_id": "pi_test_001",
            "discrepancy_type": "MISSING_INTERNALLY",
            "merchant_id": "merch_abc",
            "run_date": "2026-03-27",
        }
        producer.publish("pi_test_001", message)

        mock_kafka.produce.assert_called_once()
        call_kwargs = mock_kafka.produce.call_args[1]
        assert call_kwargs["topic"] == "payment.reconciliation.queue"

    def test_publish_uses_transaction_id_as_key(self) -> None:
        producer, mock_kafka = self._make_producer()
        message = {"transaction_id": "pi_test_002", "merchant_id": "merch_abc"}
        producer.publish("pi_test_002", message)

        call_kwargs = mock_kafka.produce.call_args[1]
        assert call_kwargs["key"] == b"pi_test_002"

    def test_publish_serializes_message_as_json(self) -> None:
        producer, mock_kafka = self._make_producer()
        message = {
            "transaction_id": "pi_test_003",
            "discrepancy_type": "DUPLICATE_LEDGER",
            "merchant_id": "merch_xyz",
            "run_date": "2026-03-27",
        }
        producer.publish("pi_test_003", message)

        call_kwargs = mock_kafka.produce.call_args[1]
        decoded = json.loads(call_kwargs["value"].decode("utf-8"))
        assert decoded == message

    def test_publish_calls_flush(self) -> None:
        producer, mock_kafka = self._make_producer()
        producer.publish("pi_test_004", {"transaction_id": "pi_test_004"})
        mock_kafka.flush.assert_called_with(timeout=5)

    def test_publish_retries_on_kafka_exception(self) -> None:
        producer, mock_kafka = self._make_producer()
        kafka_exc = KafkaException("temporary failure")
        # Fail twice, succeed on third attempt
        mock_kafka.produce.side_effect = [kafka_exc, kafka_exc, None]

        with patch("kafka.producers.reconciliation_producer.time.sleep") as mock_sleep:
            producer.publish("pi_test_005", {"transaction_id": "pi_test_005"})

        assert mock_kafka.produce.call_count == 3
        # Slept after attempts 1 and 2 (not after final success)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    def test_publish_raises_after_all_retries_exhausted(self) -> None:
        producer, mock_kafka = self._make_producer()
        kafka_exc = KafkaException("persistent failure")
        mock_kafka.produce.side_effect = [kafka_exc, kafka_exc, kafka_exc]

        with patch("kafka.producers.reconciliation_producer.time.sleep"):
            with pytest.raises(KafkaException):
                producer.publish("pi_test_006", {"transaction_id": "pi_test_006"})

        assert mock_kafka.produce.call_count == 3

    def test_close_calls_flush(self) -> None:
        producer, mock_kafka = self._make_producer()
        producer.close()
        mock_kafka.flush.assert_called_once()


class TestReconciliationProducerPublishBatch:
    """Tests for ReconciliationProducer.publish_batch()."""

    def _make_producer(self) -> tuple[ReconciliationProducer, MagicMock]:
        with patch("kafka.producers.reconciliation_producer.Producer") as mock_cls:
            mock_kafka = MagicMock()
            mock_cls.return_value = mock_kafka
            producer = ReconciliationProducer("localhost:9092")
        return producer, mock_kafka

    def test_publish_batch_calls_publish_per_message(self) -> None:
        producer, mock_kafka = self._make_producer()
        messages = [
            {"transaction_id": "pi_001", "merchant_id": "merch_a", "discrepancy_type": "MISSING_INTERNALLY"},
            {"transaction_id": "pi_002", "merchant_id": "merch_b", "discrepancy_type": "AMOUNT_MISMATCH"},
            {"transaction_id": "pi_003", "merchant_id": "merch_c", "discrepancy_type": "DUPLICATE_LEDGER"},
        ]
        with patch.object(producer, "publish") as mock_publish:
            producer.publish_batch(messages)

        assert mock_publish.call_count == 3
        mock_publish.assert_any_call("pi_001", messages[0])
        mock_publish.assert_any_call("pi_002", messages[1])
        mock_publish.assert_any_call("pi_003", messages[2])

    def test_publish_batch_empty_list_does_nothing(self) -> None:
        producer, mock_kafka = self._make_producer()
        with patch.object(producer, "publish") as mock_publish:
            producer.publish_batch([])
        mock_publish.assert_not_called()
