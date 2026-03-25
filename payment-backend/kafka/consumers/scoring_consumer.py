"""Kafka consumer for payment.transaction.validated — Phase 05.

Reads validated payment events, assembles ML feature vectors from Redis,
runs XGBoost inference, writes state transitions to payment_state_log,
and publishes scored events downstream.

Processing order (per D-D3, D-D4):
  1. Decode JSON, deserialize as ValidatedPaymentEvent.
  2. Bind structlog context.
  3. Idempotency guard — skip if SCORING row already exists for event_id.
  4. Write SCORING state: VALIDATED → SCORING.
  5. Fetch features from Redis feat:{event_id} with 3x50ms retry + 20ms timeout fallback.
  6. Run XGBoost inference via XGBoostScorer.score().
  7. Force manual_review=True when features_available=False (belt-and-suspenders per D-C3).
  8. Determine final state: AUTHORIZED (risk < 0.7) or FLAGGED (risk >= 0.7).
  9. Write final state to DB FIRST (DB before Kafka publish per D-D4).
  10. Build ScoredPaymentEvent.
  11. Publish to payment.transaction.scored.
  12. If is_high_risk: also publish to payment.alert.triggered.
  13. Increment Prometheus counters.
  14. Commit Kafka offset (manual only — never auto-commit).

Exposes a /health endpoint on port 8003 for Docker health checks.
Does NOT run Alembic migrations — schema owned by ValidationConsumer (per D-D2).
"""

import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import redis
import structlog
from confluent_kafka import Consumer, KafkaException, Message
from prometheus_client import Counter
from sqlalchemy import create_engine, text

from kafka.producers.alert_producer import AlertProducer
from kafka.producers.scored_event_producer import ScoredEventProducer
from ml.scorer import FEATURE_DEFAULTS, XGBoostScorer
from models.ml_scoring import ScoredPaymentEvent
from models.state_machine import PaymentState
from models.validation import ValidatedPaymentEvent
from services.state_machine import PaymentStateMachine

logger = structlog.get_logger(__name__)

SOURCE_TOPIC = "payment.transaction.validated"
CONSUMER_GROUP = "ml-scoring-service"
HEALTH_PORT = 8003
REDIS_FEATURE_TIMEOUT_MS = 20   # per CLAUDE.md ML contract: fallback if Redis > 20ms
FEATURE_RETRY_COUNT = 3         # per D-C1: 3 retries before using defaults
FEATURE_RETRY_DELAY_MS = 50     # per D-C1: 50ms between retries


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal health endpoint for Docker health checks."""

    consumer_ref: "ScoringConsumer | None" = None

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


class ScoringConsumer:
    """Kafka consumer for payment.transaction.validated.

    Orchestrates the full ML scoring pipeline: Redis feature assembly,
    XGBoost inference, state machine writes, and downstream Kafka publish.
    """

    def __init__(self, bootstrap_servers: str) -> None:
        # Validate required environment variables at startup
        required_env_vars = [
            "KAFKA_BOOTSTRAP_SERVERS",
            "REDIS_URL",
            "DATABASE_URL_SYNC",
            "ML_MODEL_PATH",
        ]
        for var in required_env_vars:
            if not os.getenv(var):
                logger.error("missing_env_var", var=var)
                sys.exit(1)

        # Kafka consumer — manual offset commit only (per CONSUMER-01)
        self._consumer = Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": CONSUMER_GROUP,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        })

        # XGBoost scorer — crash-and-exit if model missing (per D-A3)
        model_path = os.getenv("ML_MODEL_PATH", "ml/models/model.ubj")
        self._scorer = XGBoostScorer(model_path)

        # Redis client with 20ms socket timeout per CLAUDE.md ML contract
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=REDIS_FEATURE_TIMEOUT_MS / 1000,
            socket_timeout=REDIS_FEATURE_TIMEOUT_MS / 1000,
        )

        # PostgreSQL state machine (reuse existing PaymentStateMachine)
        db_url = os.getenv("DATABASE_URL_SYNC", "postgresql://payment:payment@localhost:5432/payment_db")
        self._state_machine = PaymentStateMachine(create_engine(db_url))

        # Downstream Kafka producers
        self._scored_producer = ScoredEventProducer(bootstrap_servers)
        self._alert_producer = AlertProducer(bootstrap_servers)

        # Prometheus counters
        self._feature_miss_counter = Counter(
            "feature_miss_total",
            "Redis feature key not found after all retries",
        )
        self._feature_timeout_counter = Counter(
            "feature_timeout_total",
            "Redis feature lookup timed out (>20ms)",
        )
        self._events_scored_counter = Counter(
            "events_scored_total",
            "Total events scored by XGBoost",
        )
        self._events_flagged_counter = Counter(
            "events_flagged_total",
            "Total events flagged as high-risk (risk_score >= 0.7)",
        )

        self._consumer.subscribe([SOURCE_TOPIC])
        self._running = True
        self._health_server: HTTPServer | None = None

        logger.info(
            "scoring_consumer_started",
            topic=SOURCE_TOPIC,
            group=CONSUMER_GROUP,
        )

    def _start_health_server(self) -> None:
        """Start threaded HTTP health server on HEALTH_PORT."""
        _HealthHandler.consumer_ref = self
        self._health_server = HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
        thread = threading.Thread(target=self._health_server.serve_forever, daemon=True)
        thread.start()
        logger.info("health_server_started", port=HEALTH_PORT)

    def _fetch_features(self, event_id: str) -> tuple[dict[str, float], bool]:
        """Fetch ML features from Redis with retry + timeout fallback.

        Tries up to FEATURE_RETRY_COUNT times with FEATURE_RETRY_DELAY_MS
        between attempts. Returns defaults immediately on TimeoutError
        (20ms socket timeout per CLAUDE.md ML contract).

        Returns:
            (features_dict, features_available)
            features_available=True when real Spark features were found.
            features_available=False when defaults are used.
        """
        for attempt in range(FEATURE_RETRY_COUNT):
            try:
                raw = self._redis.hgetall(f"feat:{event_id}")
                if raw:
                    features = {k: float(v) for k, v in raw.items()}
                    return features, True  # (features, features_available=True)
            except redis.exceptions.TimeoutError:
                self._feature_timeout_counter.inc()
                logger.warning(
                    "redis_feature_timeout",
                    event_id=event_id,
                    attempt=attempt + 1,
                )
                return dict(FEATURE_DEFAULTS), False  # immediate fallback on timeout
            time.sleep(FEATURE_RETRY_DELAY_MS / 1000)

        # All retries exhausted — key not found in Redis
        self._feature_miss_counter.inc()
        logger.warning(
            "redis_feature_miss",
            event_id=event_id,
            retries=FEATURE_RETRY_COUNT,
        )
        return dict(FEATURE_DEFAULTS), False

    def _is_duplicate_scoring(self, event_id: str) -> bool:
        """Check if a SCORING state row already exists for this event (per D-D5).

        Queries payment_state_log directly to detect replayed events.
        Returns True if a SCORING row exists, False otherwise.
        """
        with self._state_machine._engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT 1 FROM payment_state_log "
                    "WHERE event_id = :eid AND to_state = 'SCORING' LIMIT 1"
                ),
                {"eid": event_id},
            ).fetchone()
        return result is not None

    def _process_message(self, msg: Message) -> None:
        """Process a single Kafka message through the full scoring pipeline.

        Processing order follows D-D3 (idempotency before scoring) and
        D-D4 (DB write before Kafka publish).
        """
        # Step 1: Decode JSON and deserialize as ValidatedPaymentEvent
        raw_value = json.loads(msg.value().decode("utf-8"))
        event = ValidatedPaymentEvent.model_validate(raw_value)

        # Step 2: Bind structlog context
        log = logger.bind(
            event_id=event.event_id,
            merchant_id=event.merchant_id,
            partition=msg.partition(),
            offset=msg.offset(),
        )

        # Step 3: Idempotency guard — skip duplicate SCORING state writes
        if self._is_duplicate_scoring(event.event_id):
            log.info("duplicate_scoring_skip")
            self._consumer.store_offsets(msg)
            self._consumer.commit()
            return

        # Step 4: Write SCORING state — VALIDATED → SCORING
        self._state_machine.write_transition(
            transaction_id=event.event_id,
            event_id=event.event_id,
            merchant_id=event.merchant_id,
            from_state=PaymentState.VALIDATED,
            to_state=PaymentState.SCORING,
        )

        # Step 5: Fetch features from Redis with 3x50ms retry
        features, features_available = self._fetch_features(event.event_id)

        # Step 6: Run XGBoost inference
        risk_result = self._scorer.score(features)

        # Step 7: Belt-and-suspenders — force manual_review=True when no real features
        if not features_available:
            risk_result = risk_result.model_copy(update={"manual_review": True})

        # Step 8: Determine final state
        final_state = (
            PaymentState.AUTHORIZED if risk_result.risk_score < 0.7 else PaymentState.FLAGGED
        )

        # Step 9: Write final state to DB FIRST (DB before Kafka publish per D-D4)
        self._state_machine.write_transition(
            transaction_id=event.event_id,
            event_id=event.event_id,
            merchant_id=event.merchant_id,
            from_state=PaymentState.SCORING,
            to_state=final_state,
        )

        # Step 10: Build ScoredPaymentEvent
        scored_event = ScoredPaymentEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            amount_cents=event.amount_cents,
            currency=event.currency,
            stripe_customer_id=event.stripe_customer_id,
            merchant_id=event.merchant_id,
            received_at=event.received_at,
            risk_score=risk_result.risk_score,
            is_high_risk=risk_result.is_high_risk,
            manual_review=risk_result.manual_review,
            features_available=features_available,
        )

        # Step 11: Publish to payment.transaction.scored
        self._scored_producer.publish(
            stripe_event_id=event.event_id,
            scored_event=scored_event.model_dump(mode="json"),
        )

        # Step 12: Publish alert only for high-risk events
        if risk_result.is_high_risk:
            self._alert_producer.publish(
                stripe_event_id=event.event_id,
                scored_event=scored_event.model_dump(mode="json"),
            )
            self._events_flagged_counter.inc()

        # Step 13: Increment scored counter
        self._events_scored_counter.inc()

        log.info(
            "event_scored",
            risk_score=risk_result.risk_score,
            is_high_risk=risk_result.is_high_risk,
            manual_review=risk_result.manual_review,
            features_available=features_available,
            final_state=final_state.value,
        )

        # Step 14: Commit Kafka offset (manual only per CLAUDE.md)
        self._consumer.store_offsets(msg)
        self._consumer.commit()

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
                logger.critical(
                    "unhandled_consumer_error",
                    topic=msg.topic(),
                    partition=msg.partition(),
                    offset=msg.offset(),
                    exc_info=True,
                )
                raise

        self._cleanup()

    def _shutdown(self, signum: int, frame: Any) -> None:
        logger.info("scoring_consumer_shutting_down", signal=signum)
        self._running = False

    def _cleanup(self) -> None:
        if self._health_server:
            self._health_server.shutdown()
            logger.info("health_server_stopped")
        self._consumer.close()
        self._scored_producer.close()
        self._alert_producer.close()
        logger.info("scoring_consumer_stopped")


if __name__ == "__main__":
    servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    consumer = ScoringConsumer(servers)
    consumer.run()
