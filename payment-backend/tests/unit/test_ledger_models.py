"""Unit tests for ledger ORM models and PaymentState enum extensions — Phase 06.

Tests:
  - PaymentState enum has SETTLED and MANUAL_REVIEW values
  - LedgerEntry model has correct tablename and column types
  - ManualReviewQueueEntry model has correct tablename and column types
"""

import pytest
import sqlalchemy as sa
from sqlalchemy import BigInteger, Float, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB

from models.state_machine import PaymentState
from models.ledger import LedgerEntry, ManualReviewQueueEntry


class TestPaymentStateEnum:
    """Tests for PaymentState enum Phase 06 extensions."""

    def test_settled_value(self) -> None:
        assert PaymentState.SETTLED.value == "SETTLED"

    def test_manual_review_value(self) -> None:
        assert PaymentState.MANUAL_REVIEW.value == "MANUAL_REVIEW"

    def test_settled_is_distinct_from_authorized(self) -> None:
        assert PaymentState.SETTLED != PaymentState.AUTHORIZED

    def test_manual_review_is_distinct_from_flagged(self) -> None:
        assert PaymentState.MANUAL_REVIEW != PaymentState.FLAGGED


class TestLedgerEntryModel:
    """Tests for LedgerEntry SQLAlchemy model."""

    def test_tablename(self) -> None:
        assert LedgerEntry.__tablename__ == "ledger_entries"

    def test_has_id_column(self) -> None:
        col = LedgerEntry.__table__.c["id"]
        assert isinstance(col.type, BigInteger)
        assert col.primary_key is True

    def test_has_transaction_id_column(self) -> None:
        col = LedgerEntry.__table__.c["transaction_id"]
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_has_amount_cents_column(self) -> None:
        col = LedgerEntry.__table__.c["amount_cents"]
        assert isinstance(col.type, BigInteger)
        assert col.nullable is False

    def test_has_entry_type_column(self) -> None:
        col = LedgerEntry.__table__.c["entry_type"]
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_has_merchant_id_column(self) -> None:
        col = LedgerEntry.__table__.c["merchant_id"]
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_has_currency_column(self) -> None:
        col = LedgerEntry.__table__.c["currency"]
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_has_source_event_id_column(self) -> None:
        col = LedgerEntry.__table__.c["source_event_id"]
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_has_created_at_column(self) -> None:
        col = LedgerEntry.__table__.c["created_at"]
        assert isinstance(col.type, TIMESTAMP)
        assert col.nullable is False

    def test_column_count(self) -> None:
        column_names = {c.name for c in LedgerEntry.__table__.c}
        expected = {
            "id", "transaction_id", "amount_cents", "entry_type",
            "merchant_id", "currency", "source_event_id", "created_at",
        }
        assert column_names == expected


class TestManualReviewQueueEntryModel:
    """Tests for ManualReviewQueueEntry SQLAlchemy model."""

    def test_tablename(self) -> None:
        assert ManualReviewQueueEntry.__tablename__ == "manual_review_queue"

    def test_has_id_column(self) -> None:
        col = ManualReviewQueueEntry.__table__.c["id"]
        assert isinstance(col.type, BigInteger)
        assert col.primary_key is True

    def test_has_transaction_id_column(self) -> None:
        col = ManualReviewQueueEntry.__table__.c["transaction_id"]
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_has_risk_score_column(self) -> None:
        col = ManualReviewQueueEntry.__table__.c["risk_score"]
        assert isinstance(col.type, Float)
        assert col.nullable is False

    def test_has_payload_column(self) -> None:
        col = ManualReviewQueueEntry.__table__.c["payload"]
        # JSONB is a dialect-specific type; check via class name
        assert col.type.__class__.__name__ == "JSONB"
        assert col.nullable is False

    def test_has_created_at_column(self) -> None:
        col = ManualReviewQueueEntry.__table__.c["created_at"]
        assert isinstance(col.type, TIMESTAMP)
        assert col.nullable is False

    def test_has_status_column(self) -> None:
        col = ManualReviewQueueEntry.__table__.c["status"]
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_column_count(self) -> None:
        column_names = {c.name for c in ManualReviewQueueEntry.__table__.c}
        expected = {
            "id", "transaction_id", "risk_score", "payload",
            "created_at", "status",
        }
        assert column_names == expected
