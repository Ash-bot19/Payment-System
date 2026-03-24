"""Payment state machine — Phase 03.

Writes INITIATED, VALIDATED, and FAILED state transitions to the
payment_state_log table using SQLAlchemy Core inserts (append-only).
The table has DB-level triggers preventing UPDATE and DELETE.
"""

from typing import Optional

import structlog
from sqlalchemy import Engine, insert

from models.state_machine import PaymentState, PaymentStateLogEntry

logger = structlog.get_logger(__name__)


class PaymentStateMachine:
    """Writes payment state transitions to payment_state_log via SQLAlchemy Core.

    Uses Core insert() (not ORM session) for explicit append-only semantics.
    Each write_transition() call is an autocommitted INSERT via engine.begin().

    Injectable via constructor — tests pass a real or in-memory engine without
    changing class internals.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def write_transition(
        self,
        transaction_id: str,
        event_id: str,
        merchant_id: str,
        from_state: Optional[PaymentState],
        to_state: PaymentState,
    ) -> None:
        """Insert a single state transition row into payment_state_log.

        Uses SQLAlchemy Core insert with engine.begin() for autocommit.
        created_at is left to the server-side DEFAULT NOW() — no client clock
        involved in the timestamp.

        Args:
            transaction_id: The payment transaction identifier (Stripe event id
                            or derived key — set by caller for grouping).
            event_id: The Stripe event id (for idempotency lookups).
            merchant_id: The merchant identifier.
            from_state: The previous state (None for INITIATED).
            to_state: The new state being transitioned to.
        """
        from_value = from_state.value if from_state is not None else None

        with self._engine.begin() as conn:
            conn.execute(
                insert(PaymentStateLogEntry.__table__).values(
                    transaction_id=transaction_id,
                    event_id=event_id,
                    merchant_id=merchant_id,
                    from_state=from_value,
                    to_state=to_state.value,
                )
            )

        logger.info(
            "state_transition_written",
            transaction_id=transaction_id,
            event_id=event_id,
            merchant_id=merchant_id,
            from_state=from_value,
            to_state=to_state.value,
        )

    def record_initiated(
        self,
        transaction_id: str,
        event_id: str,
        merchant_id: str,
    ) -> None:
        """Write INITIATED transition (no prior state).

        Called when the consumer first picks up an event from Kafka before
        any validation or rate-limit checks.
        """
        self.write_transition(
            transaction_id=transaction_id,
            event_id=event_id,
            merchant_id=merchant_id,
            from_state=None,
            to_state=PaymentState.INITIATED,
        )

    def record_validated(
        self,
        transaction_id: str,
        event_id: str,
        merchant_id: str,
    ) -> None:
        """Write INITIATED → VALIDATED transition.

        Called after the event passes schema + business-rule validation.
        """
        self.write_transition(
            transaction_id=transaction_id,
            event_id=event_id,
            merchant_id=merchant_id,
            from_state=PaymentState.INITIATED,
            to_state=PaymentState.VALIDATED,
        )

    def record_failed(
        self,
        transaction_id: str,
        event_id: str,
        merchant_id: str,
    ) -> None:
        """Write INITIATED → FAILED transition.

        Called when an event is rejected (schema invalid) or rate-limited.
        Rate-limited events write FAILED but do NOT go to the DLQ.
        """
        self.write_transition(
            transaction_id=transaction_id,
            event_id=event_id,
            merchant_id=merchant_id,
            from_state=PaymentState.INITIATED,
            to_state=PaymentState.FAILED,
        )
