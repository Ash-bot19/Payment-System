"""Kafka consumer for payment.ledger.entry — Phase 06 Plan 02.

Reads ledger entry events (published by ScoringConsumer for AUTHORIZED payments),
writes exactly 2 rows (1 DEBIT + 1 CREDIT) to ledger_entries in a single DB
transaction, then transitions payment state from AUTHORIZED to SETTLED.

Processing order:
  1. Decode JSON from message value.
  2. Validate required fields (event_id, amount_cents as int, merchant_id).
     On validation error: DLQ with LEDGER_WRITE_FAIL, commit offset, return.
  3. Idempotency check: if SETTLED row already exists for event_id, skip.
  4. Write 2 ledger entries (DEBIT + CREDIT) in single engine.begin() context.
     The DEFERRABLE INITIALLY DEFERRED balance trigger fires at commit.
  5. Transition state: AUTHORIZED → SETTLED via PaymentStateMachine.
  6. Commit Kafka offset (manual only — per CLAUDE.md).
  7. Increment Prometheus counters.

Error handling (per D-08, D-09, D-10):
  - IntegrityError / InternalError (constraint violation): DLQ, offset committed
  - OperationalError (DB down): RE-RAISE — Docker restart + Kafka replay handles it
  - JSONDecodeError / KeyError / TypeError (malformed payload): DLQ, offset committed

Exposes /health endpoint on port 8004 for Docker health checks.
Does NOT run Alembic migrations — schema owned by ValidationConsumer (per D-D2).
"""

import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import structlog
from confluent_kafka import Consumer, Message
from prometheus_client import Counter
from sqlalchemy import create_engine, insert, text
from sqlalchemy.exc import IntegrityError, InternalError, OperationalError

from kafka.producers.dlq_producer import DLQProducer
from models.ledger import LedgerEntry
from models.state_machine import PaymentState
from services.state_machine import PaymentStateMachine

logger = structlog.get_logger(__name__)

SOURCE_TOPIC = "payment.ledger.entry"
CONSUMER_GROUP = "ledger-service"
HEALTH_PORT = 8004


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal health endpoint for Docker health checks."""

    consumer_ref: "LedgerConsumer | None" = None

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


class LedgerConsumer:
    """Kafka consumer for payment.ledger.entry.

    Writes exactly 2 balanced ledger rows (DEBIT + CREDIT) per AUTHORIZED payment
    in a single DB transaction, then transitions state to SETTLED.
    """

    def __init__(self, bootstrap_servers: str) -> None:
        # Validate required environment variables at startup
        required_env_vars = [
            "KAFKA_BOOTSTRAP_SERVERS",
            "DATABASE_URL_SYNC",
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
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
        })

        # PostgreSQL engine for direct ledger writes
        db_url = os.getenv(
            "DATABASE_URL_SYNC",
            "postgresql://payment:payment@localhost:5432/payment_db",
        )
        self._engine = create_engine(db_url)

        # State machine — reuse existing PaymentStateMachine
        self._state_machine = PaymentStateMachine(self._engine)

        # DLQ producer for non-retryable errors
        self._dlq_producer = DLQProducer(bootstrap_servers)

        # Prometheus counters
        self._ledger_entries_written_counter = Counter(
            "ledger_entries_written_total",
            "Total ledger entry pairs (DEBIT+CREDIT) written",
        )
        self._ledger_settled_counter = Counter(
            "ledger_settled_total",
            "Total payments transitioned to SETTLED state",
        )
        self._ledger_dlq_counter = Counter(
            "ledger_dlq_total",
            "Total ledger messages routed to DLQ",
        )

        self._consumer.subscribe([SOURCE_TOPIC])
        self._running = True
        self._health_server: HTTPServer | None = None

        logger.info(
            "ledger_consumer_started",
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

    def _is_duplicate_settled(self, event_id: str) -> bool:
        """Check if a SETTLED state row already exists for this event_id.

        Queries payment_state_log directly to detect replayed messages.
        Returns True if a SETTLED row exists, False otherwise.
        """
        with self._engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT 1 FROM payment_state_log "
                    "WHERE event_id = :eid AND to_state = 'SETTLED' LIMIT 1"
                ),
                {"eid": event_id},
            ).fetchone()
        return result is not None

    def _write_ledger_entries(
        self,
        event_id: str,
        amount_cents: int,
        merchant_id: str,
        currency: str,
        source_event_id: str,
    ) -> None:
        """Write exactly 2 rows (DEBIT + CREDIT) in a single DB transaction.

        Both inserts share the same engine.begin() context so the DEFERRABLE
        INITIALLY DEFERRED balance trigger fires at commit (SUM=0 enforced).
        """
        with self._engine.begin() as conn:
            conn.execute(
                insert(LedgerEntry.__table__).values(
                    transaction_id=event_id,
                    amount_cents=amount_cents,
                    entry_type="DEBIT",
                    merchant_id=merchant_id,
                    currency=currency,
                    source_event_id=source_event_id,
                )
            )
            conn.execute(
                insert(LedgerEntry.__table__).values(
                    transaction_id=event_id,
                    amount_cents=amount_cents,
                    entry_type="CREDIT",
                    merchant_id=merchant_id,
                    currency=currency,
                    source_event_id=source_event_id,
                )
            )

    def _process_message(self, msg: Message) -> None:
        """Process a single Kafka message — write balanced ledger entries + settle.

        Processing order:
          1. Decode JSON.
          2. Validate required fields.
          3. Idempotency check.
          4. Write DEBIT + CREDIT in single transaction.
          5. Transition AUTHORIZED → SETTLED.
          6. Commit offset.
        """
        log = logger.bind(
            topic=msg.topic(),
            partition=msg.partition(),
            offset=msg.offset(),
        )

        # Step 1: Decode JSON
        try:
            raw_value = json.loads(msg.value().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("ledger_malformed_json", error=str(exc))
            self._publish_dlq(msg, "LEDGER_WRITE_FAIL", str(exc))
            self._consumer.store_offsets(msg)
            self._consumer.commit()
            return

        # Step 2: Validate required fields
        try:
            event_id: str = raw_value["event_id"]
            amount_cents: int = raw_value["amount_cents"]
            merchant_id: str = raw_value["merchant_id"]
            currency: str = raw_value.get("currency", "usd")
            source_event_id: str = raw_value.get("source_event_id", event_id)

            # Strict type check: amount_cents must be an integer
            if not isinstance(amount_cents, int):
                raise TypeError(
                    f"amount_cents must be int, got {type(amount_cents).__name__}: {amount_cents!r}"
                )
            if not isinstance(event_id, str) or not event_id:
                raise ValueError("event_id must be a non-empty string")
            if not isinstance(merchant_id, str) or not merchant_id:
                raise ValueError("merchant_id must be a non-empty string")

        except (KeyError, TypeError, ValueError) as exc:
            log.warning("ledger_validation_error", error=str(exc))
            self._publish_dlq(msg, "LEDGER_WRITE_FAIL", str(exc))
            self._consumer.store_offsets(msg)
            self._consumer.commit()
            return

        log = log.bind(event_id=event_id, merchant_id=merchant_id)

        # Step 3: Idempotency check — skip if already SETTLED
        if self._is_duplicate_settled(event_id):
            log.info("ledger_duplicate_settled_skip")
            self._consumer.store_offsets(msg)
            self._consumer.commit()
            return

        # Step 4: Write DEBIT + CREDIT in single transaction
        # IntegrityError/InternalError = non-retryable constraint violation → DLQ
        # OperationalError = DB down → RE-RAISE (Docker restart handles it)
        try:
            self._write_ledger_entries(
                event_id=event_id,
                amount_cents=amount_cents,
                merchant_id=merchant_id,
                currency=currency,
                source_event_id=source_event_id,
            )
        except (IntegrityError, InternalError) as exc:
            log.error("ledger_constraint_violation", error=str(exc))
            self._publish_dlq(msg, "LEDGER_WRITE_FAIL", str(exc))
            self._consumer.store_offsets(msg)
            self._consumer.commit()
            self._ledger_dlq_counter.inc()
            return
        except OperationalError:
            # Systemic DB outage — crash so Docker restarts and Kafka replays
            log.critical("ledger_db_connection_failure", exc_info=True)
            raise

        # Step 5: Transition AUTHORIZED → SETTLED
        self._state_machine.write_transition(
            transaction_id=event_id,
            event_id=event_id,
            merchant_id=merchant_id,
            from_state=PaymentState.AUTHORIZED,
            to_state=PaymentState.SETTLED,
        )

        # Step 6: Commit offset AFTER DB write + state transition
        self._consumer.store_offsets(msg)
        self._consumer.commit()

        # Step 7: Increment counters
        self._ledger_entries_written_counter.inc()
        self._ledger_settled_counter.inc()

        log.info(
            "ledger_entry_settled",
            event_id=event_id,
            amount_cents=amount_cents,
            currency=currency,
        )

    def _publish_dlq(self, msg: Message, failure_reason: str, error_detail: str) -> None:
        """Publish failed message to DLQ with LEDGER_WRITE_FAIL reason."""
        try:
            original_value = msg.value().decode("utf-8") if msg.value() else ""
        except Exception:
            original_value = ""

        dlq_message = {
            "original_topic": SOURCE_TOPIC,
            "original_offset": msg.offset(),
            "failure_reason": failure_reason,
            "retry_count": 0,
            "first_failure_ts": None,
            "payload": original_value,
            "error_detail": error_detail,
        }

        event_id = "unknown"
        try:
            raw = json.loads(original_value)
            event_id = raw.get("event_id", "unknown")
        except Exception:
            pass

        self._dlq_producer.publish(
            stripe_event_id=event_id,
            dlq_message=dlq_message,
        )
        self._ledger_dlq_counter.inc()
        logger.info(
            "ledger_dlq_published",
            failure_reason=failure_reason,
            event_id=event_id,
        )

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
                    "unhandled_ledger_consumer_error",
                    topic=msg.topic(),
                    partition=msg.partition(),
                    offset=msg.offset(),
                    exc_info=True,
                )
                raise

        self._cleanup()

    def _shutdown(self, signum: int, frame: Any) -> None:
        logger.info("ledger_consumer_shutting_down", signal=signum)
        self._running = False

    def _cleanup(self) -> None:
        if self._health_server:
            self._health_server.shutdown()
            logger.info("health_server_stopped")
        self._consumer.close()
        self._dlq_producer.close()
        logger.info("ledger_consumer_stopped")


if __name__ == "__main__":
    servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    consumer = LedgerConsumer(servers)
    consumer.run()
