"""SQLAlchemy models and enums for payment state machine — Phase 03.

PaymentState: Python enum for state transitions (append-only log).
PaymentStateLogEntry: SQLAlchemy ORM model for payment_state_log table.
ProcessingResult: dataclass returned by PaymentStateMachine.process().
"""

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Text, TIMESTAMP, func
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped

from models.validation import ValidatedPaymentEvent


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy ORM models.

    Used by Alembic env.py to populate target_metadata for autogenerate.
    State writes use SQLAlchemy Core (insert) for explicit append-only ops.
    """

    pass


class PaymentState(enum.Enum):
    """Payment state transitions per LOCKED state machine contract.

    INITIATED  — event first seen by validation consumer
    VALIDATED  — passed schema + business-rule validation
    FAILED     — rejected (schema error) or rate-limited
    SCORING    — sent to ML scoring service (Phase 05)
    AUTHORIZED — ML risk_score < 0.7 (Phase 05)
    FLAGGED    — ML risk_score >= 0.7, routed to manual review (Phase 05)
    """

    INITIATED = "INITIATED"
    VALIDATED = "VALIDATED"
    FAILED = "FAILED"
    SCORING = "SCORING"
    AUTHORIZED = "AUTHORIZED"
    FLAGGED = "FLAGGED"


class PaymentStateLogEntry(Base):
    """ORM representation of payment_state_log (append-only).

    The table has DB-level triggers preventing UPDATE and DELETE.
    Writes are performed via SQLAlchemy Core insert() to keep them explicit.
    """

    __tablename__ = "payment_state_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(Text, nullable=False)
    from_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    to_state: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


@dataclass
class ProcessingResult:
    """Result returned by PaymentStateMachine after processing a raw event.

    Carries enough information for ValidationConsumer to decide whether to
    publish downstream or route to DLQ.

    Attributes:
        success: True if event passed validation and state is VALIDATED.
        validated_event: Populated when success=True; None on failure.
        failure_reason: DLQ failure_reason enum string (SCHEMA_INVALID,
                        IDEMPOTENCY_COLLISION, ML_TIMEOUT, LEDGER_WRITE_FAIL,
                        UNKNOWN). None when success=True.
    """

    success: bool
    validated_event: Optional[ValidatedPaymentEvent] = None
    failure_reason: Optional[str] = None
