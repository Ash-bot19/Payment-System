"""Kafka consumer for payment.webhook.received — Phase 02.

Reads raw webhook events, validates them via validate_event(), routes
failures to payment.dlq via DLQProducer, and commits offsets manually.
Exposes a /health endpoint on port 8002 for Prometheus scraping per D-03.
"""

import json
import os
import signal
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import structlog
from confluent_kafka import Consumer, KafkaException, Message

from kafka.consumers.validation_logic import ValidationError, validate_event
from kafka.producers.dlq_producer import DLQProducer
from models.validation import DLQMessage

logger = structlog.get_logger(__name__)

SOURCE_TOPIC = "payment.webhook.received"
CONSUMER_GROUP = "validation-service"
HEALTH_PORT = 8002


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal health endpoint for Prometheus scraping (per D-03)."""

    consumer_ref: "ValidationConsumer | None" = None

    def do_GET(self) -> None:
        if self.path == "/health":
            status = "ok" if self.consumer_ref and self.consumer_ref._running else "stopping"
            body = json.dumps({"status": status}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress default stderr logging — use structlog instead
        pass


class ValidationConsumer:
    def __init__(self, bootstrap_servers: str) -> None:
        self._consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": CONSUMER_GROUP,           # per CONSUMER-02
            "enable.auto.commit": False,          # per CONSUMER-01: manual only
            "auto.offset.reset": "earliest",      # per D-04
        })
        self._dlq_producer = DLQProducer(bootstrap_servers)
        self._running = True
        self._consumer.subscribe([SOURCE_TOPIC])
        self._health_server: HTTPServer | None = None
        logger.info("validation_consumer_started",
                    topic=SOURCE_TOPIC,
                    group=CONSUMER_GROUP)

    def _start_health_server(self) -> None:
        """Start threaded HTTP health server on HEALTH_PORT (per D-03)."""
        _HealthHandler.consumer_ref = self
        self._health_server = HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
        thread = threading.Thread(target=self._health_server.serve_forever, daemon=True)
        thread.start()
        logger.info("health_server_started", port=HEALTH_PORT)

    def run(self) -> None:
        """Main poll loop. Runs until SIGTERM/SIGINT."""
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)

        self._start_health_server()

        while self._running:
            msg = self._consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                logger.error("kafka_consumer_error", error=str(msg.error()))
                continue

            try:
                self._process_message(msg)
            except Exception:
                # Unhandled exception — crash, Docker restarts, message replayed
                logger.critical("unhandled_consumer_error",
                                topic=msg.topic(),
                                partition=msg.partition(),
                                offset=msg.offset(),
                                exc_info=True)
                raise

        self._cleanup()

    def _process_message(self, msg: Message) -> None:
        raw_value = json.loads(msg.value().decode("utf-8"))
        log = logger.bind(
            stripe_event_id=raw_value.get("stripe_event_id", "unknown"),
            event_type=raw_value.get("event_type", "unknown"),
            partition=msg.partition(),
            offset=msg.offset(),
        )

        try:
            validated = validate_event(raw_value)
            # Per D-16: Phase 2 valid path — commit offset, log awaiting downstream
            log.info("validation_passed_awaiting_downstream",
                     event_id=validated.event_id,
                     phase=2)
        except ValidationError as exc:
            # Build DLQ message with all 6 required fields per locked contract
            dlq_msg = DLQMessage(
                original_topic=SOURCE_TOPIC,
                original_offset=msg.offset(),
                failure_reason=exc.reason,       # "SCHEMA_INVALID"
                retry_count=0,
                first_failure_ts=datetime.now(timezone.utc),
                payload=raw_value,               # original message verbatim
            )
            # Per D-15: write to DLQ first, then commit offset
            self._dlq_producer.publish(
                stripe_event_id=raw_value.get("stripe_event_id", "unknown"),
                dlq_message=dlq_msg.model_dump(mode="json"),
            )
            log.warning("event_validation_failed",
                        failure_reason=exc.reason,
                        detail=exc.detail)

        # Per D-18: store offset after processing, commit per poll cycle
        self._consumer.store_offsets(msg)
        self._consumer.commit()

    def _shutdown(self, signum: int, frame: Any) -> None:
        logger.info("validation_consumer_shutting_down", signal=signum)
        self._running = False

    def _cleanup(self) -> None:
        if self._health_server:
            self._health_server.shutdown()
            logger.info("health_server_stopped")
        self._consumer.close()
        self._dlq_producer.close()
        logger.info("validation_consumer_stopped")
