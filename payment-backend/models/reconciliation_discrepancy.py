"""SQLAlchemy ORM model for reconciliation_discrepancies table — Phase 08.

ReconciliationDiscrepancy represents a single discrepancy row persisted by
the nightly_reconciliation Airflow DAG's persist_discrepancies task.

The table is append-only: DB-level triggers prevent UPDATE and DELETE.
Columns mirror ReconciliationMessage (Pydantic model in models/reconciliation.py)
with the addition of a server-assigned BigSerial id and server-default created_at.

Used as the source table for the dbt reconciliation_summary mart (Phase 8, Plan 02).
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, Text, TIMESTAMP, func
from sqlalchemy.orm import Mapped, mapped_column

from models.state_machine import Base


class ReconciliationDiscrepancy(Base):
    """ORM representation of reconciliation_discrepancies (append-only).

    Each row records one discrepancy detected during the nightly reconciliation
    DAG run. discrepancy_type values: MISSING_INTERNALLY, AMOUNT_MISMATCH,
    DUPLICATE_LEDGER.

    Optional columns (nullable=True) are None for discrepancy types where
    the corresponding Stripe or internal data is absent — see ReconciliationMessage
    docstring for per-type rules.
    """

    __tablename__ = "reconciliation_discrepancies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(Text, nullable=False)
    discrepancy_type: Mapped[str] = mapped_column(Text, nullable=False)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stripe_amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    diff_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="USD")
    merchant_id: Mapped[str] = mapped_column(Text, nullable=False)
    stripe_created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
