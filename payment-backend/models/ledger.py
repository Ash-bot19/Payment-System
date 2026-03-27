"""SQLAlchemy ORM models for double-entry ledger — Phase 06.

LedgerEntry: Represents a single debit or credit row in ledger_entries.
ManualReviewQueueEntry: Represents a FLAGGED payment queued for human review.

Both tables are append-only: DB-level triggers enforce no UPDATE/DELETE.
The ledger_entries table additionally enforces balanced entries per
transaction_id via a DEFERRABLE INITIALLY DEFERRED constraint trigger.

Writes are performed via SQLAlchemy Core insert() for explicit append-only
semantics (same pattern as PaymentStateMachine in services/state_machine.py).
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, Float, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models.state_machine import Base


class LedgerEntry(Base):
    """ORM representation of ledger_entries (append-only double-entry ledger).

    Each SETTLED transaction produces exactly 2 rows: 1 DEBIT + 1 CREDIT.
    The DB trigger enforce_ledger_balance() ensures SUM(amount_cents) = 0
    per transaction_id at commit time (DEFERRABLE INITIALLY DEFERRED).

    entry_type values: 'DEBIT' or 'CREDIT'
    amount_cents: always positive BIGINT stored in cents (USD only in MVP)
    """

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_type: Mapped[str] = mapped_column(Text, nullable=False)  # 'DEBIT' or 'CREDIT'
    merchant_id: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="USD")
    source_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ManualReviewQueueEntry(Base):
    """ORM representation of manual_review_queue (append-only).

    Rows are inserted when a FLAGGED event has manual_review=True.
    status defaults to 'PENDING' — human reviewer updates via separate
    admin tool (out of scope for MVP).

    payload: full ScoredPaymentEvent JSON for reviewer context.
    """

    __tablename__ = "manual_review_queue"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(Text, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PENDING")
