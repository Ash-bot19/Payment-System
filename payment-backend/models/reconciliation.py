"""Pydantic v2 model for reconciliation messages — Phase 07.

ReconciliationMessage implements the D-11 schema for discrepancies found by
the nightly Airflow reconciliation DAG when comparing internal ledger entries
against Stripe API data.

Three discrepancy types per D-11:
  - MISSING_INTERNALLY: Stripe has a payment_intent the internal ledger lacks.
    internal_amount_cents and diff_cents are None.
  - AMOUNT_MISMATCH: Both internal and Stripe have records but amounts differ.
    stripe_amount_cents and stripe_created_at are None (Stripe data was used
    as the reference; diff = stripe - internal is computed by the DAG).
  - DUPLICATE_LEDGER: Internal ledger has duplicate entries for a transaction.
    stripe_payment_intent_id, stripe_amount_cents, diff_cents, stripe_created_at
    are all None.

Published to payment.reconciliation.queue by ReconciliationProducer.
Consumed by the Airflow downstream pipeline for alerting + correction.
"""

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel


class ReconciliationMessage(BaseModel):
    """D-11 schema for a single reconciliation discrepancy.

    All 10 fields are present on every message. Optional fields are None
    where not applicable per the discrepancy type (see module docstring).

    Uses Literal for discrepancy_type (not a Python enum class) to keep
    serialization simple for Kafka JSON via .model_dump().
    """

    transaction_id: str
    discrepancy_type: Literal["MISSING_INTERNALLY", "AMOUNT_MISMATCH", "DUPLICATE_LEDGER"]
    stripe_payment_intent_id: Optional[str] = None
    internal_amount_cents: Optional[int] = None
    stripe_amount_cents: Optional[int] = None
    diff_cents: Optional[int] = None
    currency: str = "USD"
    merchant_id: str
    stripe_created_at: Optional[datetime] = None
    run_date: date
