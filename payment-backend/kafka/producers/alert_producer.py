"""Producer for payment.alert.triggered topic — Phase 05.

Mirrors ValidatedEventProducer exactly: same Producer config, same
_delivery_report callback, same flush(timeout=5), same retry logic
(3 retries, backoff [1, 2, 4]), same crash-on-exhaustion per D-18.

Published only when is_high_risk=True (risk_score >= 0.7) after the scored
event has been published to payment.transaction.scored. Silent drops are
not acceptable — publish failures crash the consumer for Docker restart.
"""

import json
import time
from typing import Any

import structlog
from confluent_kafka import KafkaException, Producer

logger = structlog.get_logger(__name__)

TOPIC = "payment.alert.triggered"


class AlertProducer:
    """Publishes ScoredPaymentEvent dicts to payment.alert.triggered.

    Only called when is_high_risk=True (risk_score >= 0.7).
    Uses stripe_event_id as Kafka partition key for ordering guarantee.
    """

    def __init__(self, bootstrap_servers: str) -> None:
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def publish(self, stripe_event_id: str, scored_event: dict[str, Any]) -> None:
        """Publish a high-risk alert to payment.alert.triggered.

        Uses stripe_event_id as Kafka key for partition affinity.
        Retries up to 3 times with exponential backoff (1s, 2s, 4s).
        After all retries exhausted: logs critical and re-raises KafkaException
        so the consumer crashes and Docker restarts it for replay.

        Args:
            stripe_event_id: Stripe event identifier — used as Kafka key.
            scored_event: Dict representation of ScoredPaymentEvent (is_high_risk=True).
        """
        backoff_seconds = [1, 2, 4]
        last_exc: KafkaException | None = None

        for attempt, backoff in enumerate(backoff_seconds, start=1):
            try:
                self._producer.produce(
                    topic=TOPIC,
                    key=stripe_event_id.encode("utf-8"),
                    value=json.dumps(scored_event, default=str).encode("utf-8"),
                    on_delivery=self._delivery_report,
                )
                self._producer.flush(timeout=5)
                logger.info(
                    "alert_publish_success",
                    topic=TOPIC,
                    stripe_event_id=stripe_event_id,
                    attempt=attempt,
                )
                return
            except KafkaException as exc:
                last_exc = exc
                logger.warning(
                    "alert_publish_attempt_failed",
                    topic=TOPIC,
                    stripe_event_id=stripe_event_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < len(backoff_seconds):
                    time.sleep(backoff)

        logger.critical(
            "alert_publish_exhausted",
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
