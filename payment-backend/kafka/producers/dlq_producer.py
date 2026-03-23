"""DLQ producer for payment.dlq topic — Phase 02.

Mirrors WebhookProducer pattern with added retry logic per D-17:
3 attempts with exponential backoff (1s, 2s, 4s). On exhaustion the
consumer crashes so Docker can restart it and replay the message.
"""

import json
import time
from typing import Any

import structlog
from confluent_kafka import KafkaException, Producer

logger = structlog.get_logger(__name__)

TOPIC = "payment.dlq"


class DLQProducer:
    def __init__(self, bootstrap_servers: str) -> None:
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def publish(self, stripe_event_id: str, dlq_message: dict[str, Any]) -> None:
        """Publish a failed event to payment.dlq.

        Uses stripe_event_id as Kafka key for partition affinity.
        Retries up to 3 times with exponential backoff (1s, 2s, 4s) per D-17.
        After all retries exhausted: logs critical and re-raises KafkaException
        so the consumer crashes and Docker restarts it for replay.
        """
        backoff_seconds = [1, 2, 4]
        last_exc: KafkaException | None = None

        for attempt, backoff in enumerate(backoff_seconds, start=1):
            try:
                self._producer.produce(
                    topic=TOPIC,
                    key=stripe_event_id.encode("utf-8"),
                    value=json.dumps(dlq_message, default=str).encode("utf-8"),
                    on_delivery=self._delivery_report,
                )
                self._producer.flush(timeout=5)
                logger.info(
                    "dlq_publish_success",
                    topic=TOPIC,
                    stripe_event_id=stripe_event_id,
                    attempt=attempt,
                )
                return
            except KafkaException as exc:
                last_exc = exc
                logger.warning(
                    "dlq_publish_attempt_failed",
                    topic=TOPIC,
                    stripe_event_id=stripe_event_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < len(backoff_seconds):
                    time.sleep(backoff)

        logger.critical(
            "dlq_publish_exhausted",
            topic=TOPIC,
            stripe_event_id=stripe_event_id,
            attempts=3,
        )
        raise last_exc  # type: ignore[misc]

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
