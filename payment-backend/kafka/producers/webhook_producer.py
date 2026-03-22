import json
from typing import Any

import structlog
from confluent_kafka import Producer, KafkaException

logger = structlog.get_logger(__name__)

TOPIC = "payment.webhook.received"


class WebhookProducer:
    def __init__(self, bootstrap_servers: str) -> None:
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def publish(self, stripe_event_id: str, message: dict[str, Any]) -> None:
        """Publish a webhook message to payment.webhook.received.

        Uses stripe_event_id as the Kafka message key so events for the
        same payment hash to the same partition (ordering guarantee).
        """
        try:
            self._producer.produce(
                topic=TOPIC,
                key=stripe_event_id.encode("utf-8"),
                value=json.dumps(message, default=str).encode("utf-8"),
                on_delivery=self._delivery_report,
            )
            self._producer.flush(timeout=5)
        except KafkaException as exc:
            logger.error(
                "kafka_publish_failed",
                topic=TOPIC,
                stripe_event_id=stripe_event_id,
                error=str(exc),
            )
            raise

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
