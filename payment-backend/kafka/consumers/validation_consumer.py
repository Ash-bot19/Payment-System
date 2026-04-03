"""Kafka consumer for payment.webhook.received — Phase 03.

Reads raw webhook events, applies rate limiting, writes state transitions
via PaymentStateMachine, validates events, publishes to
payment.transaction.validated on success (or payment.dlq on failure).

Processing order (D-03):
  1. Decode message.
  2. Extract merchant_id from raw payload.
  3. Rate-limit check (payment_intent.succeeded only).
     If rate-limited: write INITIATED + FAILED, commit offset, return.
  4. Write INITIATED to payment_state_log.
  5. Validate event via validate_event().
     If invalid: write FAILED, publish to DLQ, commit offset, return.
  6. Write VALIDATED to payment_state_log.
  7. Publish to payment.transaction.validated.
  8. Commit Kafka offset.

Exposes a /health endpoint on port 8002 for Docker health checks.
Runs Alembic migrations at startup (D-10).
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
from redis import Redis
from sqlalchemy import create_engine

from kafka.consumers.validation_logic import ValidationError, validate_event
from kafka.producers.dlq_producer import DLQProducer
from kafka.producers.validated_event_producer import ValidatedEventProducer
from models.validation import DLQMessage
from services.rate_limiter import MerchantRateLimiter
from services.state_machine import PaymentStateMachine

logger = structlog.get_logger(__name__)

SOURCE_TOPIC = "payment.webhook.received"
CONSUMER_GROUP = "validation-service"
HEALTH_PORT = 8002


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal health endpoint for Docker health checks (per D-03)."""

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
        # Kafka consumer — manual offset commit only (per CONSUMER-01)
        self._consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": CONSUMER_GROUP,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
        })
        self._dlq_producer = DLQProducer(bootstrap_servers)
        self._validated_producer = ValidatedEventProducer(bootstrap_servers)

        # PostgreSQL state machine (per D-04, D-13)
        db_url = os.getenv("DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db")
        engine = create_engine(db_url)
        self._state_machine = PaymentStateMachine(engine)

        # Redis rate limiter (per D-04, D-16)
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        self._rate_limiter = MerchantRateLimiter(redis_client)

        self._default_merchant_id = os.getenv("DEFAULT_MERCHANT_ID", "unknown_merchant")
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

    def _run_migrations(self) -> None:
        """Run Alembic migrations at startup (per D-10)."""
        from alembic import command
        from alembic.config import Config
        alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "..", "..", "db", "alembic.ini"))
        alembic_cfg.set_main_option(
            "script_location",
            os.path.join(os.path.dirname(__file__), "..", "..", "db", "migrations"),
        )
        db_url = os.getenv("DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db")
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        command.upgrade(alembic_cfg, "head")
        logger.info("alembic_migrations_applied")

    def run(self) -> None:
        """Main poll loop. Runs until SIGTERM/SIGINT."""
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)

        # Run Alembic migrations at startup (per D-10)
        self._run_migrations()

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

    def _extract_merchant_id(self, raw_value: dict[str, Any]) -> str:
        """Extract merchant_id from raw payload metadata (per D-05).

        Falls back to DEFAULT_MERCHANT_ID if the key is absent or the path
        is malformed.
        """
        try:
            return raw_value["payload"]["data"]["object"]["metadata"]["merchant_id"]
        except (KeyError, TypeError):
            return self._default_merchant_id

    def _process_message(self, msg: Message) -> None:
        raw_value = json.loads(msg.value().decode("utf-8"))
        stripe_event_id = raw_value.get("stripe_event_id", "unknown")
        event_type = raw_value.get("event_type", "unknown")
        log = logger.bind(
            stripe_event_id=stripe_event_id,
            event_type=event_type,
            partition=msg.partition(),
            offset=msg.offset(),
        )

        # Step 1: Extract merchant_id from raw payload (per D-05, D-06)
        merchant_id = self._extract_merchant_id(raw_value)

        # Step 2: Rate limit check — before INITIATED write (per D-06, D-08, D-17)
        # Only apply rate limiting to payment_intent.succeeded events.
        if event_type == "payment_intent.succeeded" and self._rate_limiter.is_rate_limited(merchant_id):
            # Per D-17: rate-limited → write INITIATED + FAILED, no DLQ publish
            self._state_machine.record_initiated(stripe_event_id, stripe_event_id, merchant_id)
            self._state_machine.record_failed(stripe_event_id, stripe_event_id, merchant_id)
            log.warning("event_rate_limited", merchant_id=merchant_id)
            self._consumer.store_offsets(msg)
            self._consumer.commit()
            return

        # Step 3: Write INITIATED to state log (per D-03 step 4)
        self._state_machine.record_initiated(stripe_event_id, stripe_event_id, merchant_id)

        # Step 4: Validate (per D-03 step 5)
        try:
            validated = validate_event(raw_value, merchant_id)
        except ValidationError as exc:
            # Validation failed → write FAILED, publish DLQ
            self._state_machine.record_failed(stripe_event_id, stripe_event_id, merchant_id)
            dlq_msg = DLQMessage(
                original_topic=SOURCE_TOPIC,
                original_offset=msg.offset(),
                failure_reason=exc.reason,
                retry_count=0,
                first_failure_ts=datetime.now(timezone.utc),
                payload=raw_value,
            )
            self._dlq_producer.publish(
                stripe_event_id=stripe_event_id,
                dlq_message=dlq_msg.model_dump(mode="json"),
            )
            log.warning("event_validation_failed",
                        failure_reason=exc.reason,
                        detail=exc.detail)
            self._consumer.store_offsets(msg)
            self._consumer.commit()
            return

        # Step 5: Write VALIDATED to state log (per D-03 step 6)
        self._state_machine.record_validated(stripe_event_id, stripe_event_id, merchant_id)

        # Step 6: Publish to payment.transaction.validated (per D-03 step 7, D-19)
        self._validated_producer.publish(
            stripe_event_id=stripe_event_id,
            validated_event=validated.model_dump(mode="json"),
        )
        log.info("event_validated_and_published",
                 event_id=validated.event_id,
                 merchant_id=merchant_id)

        # Step 7: Commit offset (per D-03 step 8)
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
        self._validated_producer.close()
        logger.info("validation_consumer_stopped")
