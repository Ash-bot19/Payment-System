"""Producer for payment.reconciliation.queue topic — Phase 07.

Mirrors AlertProducer exactly: same Producer config, same _delivery_report
callback, same flush(timeout=5), same retry logic (3 retries, backoff [1, 2, 4]),
same crash-on-exhaustion per D-18.

Published by the nightly Airflow reconciliation DAG when it detects discrepancies
between internal ledger entries and Stripe API data. Silent drops are not
acceptable — publish failures crash the caller for Docker restart and replay.
"""

import json
import time
from typing import Any

import structlog
from confluent_kafka import KafkaException, Producer

logger = structlog.get_logger(__name__)

TOPIC = "payment.reconciliation.queue"


class ReconciliationProducer:
    """Publishes ReconciliationMessage dicts to payment.reconciliation.queue.

    Uses transaction_id as Kafka partition key for ordering guarantee.
    Retries up to 3 times with exponential backoff (1s, 2s, 4s).
    After all retries exhausted: logs critical and re-raises KafkaException
    so the caller crashes and Docker restarts it for replay.
    """

    def __init__(self, bootstrap_servers: str) -> None:
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def publish(self, transaction_id: str, message: dict[str, Any]) -> None:
        """Publish a reconciliation discrepancy to payment.reconciliation.queue.

        Uses transaction_id as Kafka key for partition affinity.
        Retries up to 3 times with exponential backoff (1s, 2s, 4s).
        After all retries exhausted: logs critical and re-raises KafkaException
        so the consumer crashes and Docker restarts it for replay.

        Args:
            transaction_id: Transaction identifier — used as Kafka key.
            message: Dict representation of ReconciliationMessage (via .model_dump()).
        """
        backoff_seconds = [1, 2, 4]
        last_exc: KafkaException | None = None

        for attempt, backoff in enumerate(backoff_seconds, start=1):
            try:
                self._producer.produce(
                    topic=TOPIC,
                    key=transaction_id.encode("utf-8"),
                    value=json.dumps(message, default=str).encode("utf-8"),
                    on_delivery=self._delivery_report,
                )
                self._producer.flush(timeout=5)
                logger.info(
                    "reconciliation_publish_success",
                    topic=TOPIC,
                    transaction_id=transaction_id,
                    attempt=attempt,
                )
                return
            except KafkaException as exc:
                last_exc = exc
                logger.warning(
                    "reconciliation_publish_attempt_failed",
                    topic=TOPIC,
                    transaction_id=transaction_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < len(backoff_seconds):
                    time.sleep(backoff)

        logger.critical(
            "reconciliation_publish_exhausted",
            topic=TOPIC,
            transaction_id=transaction_id,
            attempts=3,
        )
        raise last_exc  # type: ignore[misc]

    def publish_batch(self, messages: list[dict[str, Any]]) -> None:
        """Publish a batch of reconciliation messages.

        Iterates the list and calls publish() for each message. The batch
        is serialized sequentially — no partial commits on failure.

        Args:
            messages: List of ReconciliationMessage dicts (each must contain
                      a 'transaction_id' key used as the Kafka partition key).
        """
        for msg in messages:
            self.publish(msg["transaction_id"], msg)

    @staticmethod
    def _delivery_report(err: Any, msg: Any) -> None:
        if err:
            logger.error(
                "kafka_delivery_failed",
                topic=msg.topic(),
                partition=msg.partition(),
                error=str(err),
            )
        else:
            logger.info(
                "kafka_delivery_success",
                topic=msg.topic(),
                partition=msg.partition(),
                offset=msg.offset(),
            )

    def close(self) -> None:
        self._producer.flush()
