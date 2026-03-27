"""Unit tests for ReconciliationMessage Pydantic model — Phase 07.

Tests:
  - Full message with all 10 fields validates successfully
  - Type 1 (MISSING_INTERNALLY): internal_amount_cents=None, diff_cents=None accepted
  - Type 3 (AMOUNT_MISMATCH): stripe_amount_cents=None, stripe_created_at=None accepted
  - Type 4 (DUPLICATE_LEDGER): stripe_payment_intent_id=None, stripe_amount_cents=None,
    diff_cents=None, stripe_created_at=None accepted
  - Invalid discrepancy_type raises ValidationError
  - run_date is a date (not datetime)
"""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from models.reconciliation import ReconciliationMessage


class TestReconciliationMessageFullValidation:
    """All 10 fields populated — should validate successfully."""

    def test_all_fields_populated_validates(self) -> None:
        msg = ReconciliationMessage(
            transaction_id="pi_test_001",
            discrepancy_type="AMOUNT_MISMATCH",
            stripe_payment_intent_id="pi_stripe_001",
            internal_amount_cents=10000,
            stripe_amount_cents=9500,
            diff_cents=-500,
            currency="USD",
            merchant_id="merch_abc",
            stripe_created_at=datetime(2026, 3, 27, 12, 0, 0),
            run_date=date(2026, 3, 27),
        )
        assert msg.transaction_id == "pi_test_001"
        assert msg.discrepancy_type == "AMOUNT_MISMATCH"
        assert msg.stripe_payment_intent_id == "pi_stripe_001"
        assert msg.internal_amount_cents == 10000
        assert msg.stripe_amount_cents == 9500
        assert msg.diff_cents == -500
        assert msg.currency == "USD"
        assert msg.merchant_id == "merch_abc"
        assert isinstance(msg.stripe_created_at, datetime)
        assert isinstance(msg.run_date, date)


class TestReconciliationMessageType1MissingInternally:
    """Type 1: MISSING_INTERNALLY — internal_amount_cents=None, diff_cents=None."""

    def test_type1_null_pattern_accepted(self) -> None:
        msg = ReconciliationMessage(
            transaction_id="pi_test_002",
            discrepancy_type="MISSING_INTERNALLY",
            stripe_payment_intent_id="pi_stripe_002",
            internal_amount_cents=None,
            stripe_amount_cents=5000,
            diff_cents=None,
            currency="USD",
            merchant_id="merch_abc",
            stripe_created_at=datetime(2026, 3, 27, 10, 0, 0),
            run_date=date(2026, 3, 27),
        )
        assert msg.discrepancy_type == "MISSING_INTERNALLY"
        assert msg.internal_amount_cents is None
        assert msg.diff_cents is None
        assert msg.stripe_amount_cents == 5000
        assert msg.stripe_payment_intent_id == "pi_stripe_002"

    def test_type1_required_fields_present(self) -> None:
        """stripe_amount_cents, stripe_payment_intent_id, stripe_created_at required."""
        msg = ReconciliationMessage(
            transaction_id="pi_test_003",
            discrepancy_type="MISSING_INTERNALLY",
            stripe_payment_intent_id="pi_stripe_003",
            stripe_amount_cents=7500,
            stripe_created_at=datetime(2026, 3, 27, 8, 0, 0),
            merchant_id="merch_xyz",
            run_date=date(2026, 3, 27),
        )
        assert msg.stripe_payment_intent_id == "pi_stripe_003"
        assert msg.stripe_amount_cents == 7500
        assert msg.stripe_created_at is not None


class TestReconciliationMessageType3AmountMismatch:
    """Type 3: AMOUNT_MISMATCH — stripe_amount_cents=None, stripe_created_at=None."""

    def test_type3_null_pattern_accepted(self) -> None:
        msg = ReconciliationMessage(
            transaction_id="pi_test_004",
            discrepancy_type="AMOUNT_MISMATCH",
            stripe_payment_intent_id="pi_stripe_004",
            internal_amount_cents=10000,
            stripe_amount_cents=None,
            diff_cents=500,
            currency="USD",
            merchant_id="merch_abc",
            stripe_created_at=None,
            run_date=date(2026, 3, 27),
        )
        assert msg.discrepancy_type == "AMOUNT_MISMATCH"
        assert msg.stripe_amount_cents is None
        assert msg.stripe_created_at is None
        assert msg.internal_amount_cents == 10000
        assert msg.diff_cents == 500

    def test_type3_internal_amount_required(self) -> None:
        """internal_amount_cents must be provided for AMOUNT_MISMATCH."""
        msg = ReconciliationMessage(
            transaction_id="pi_test_005",
            discrepancy_type="AMOUNT_MISMATCH",
            stripe_payment_intent_id="pi_stripe_005",
            internal_amount_cents=8000,
            diff_cents=-200,
            merchant_id="merch_abc",
            run_date=date(2026, 3, 27),
        )
        assert msg.internal_amount_cents == 8000


class TestReconciliationMessageType4DuplicateLedger:
    """Type 4: DUPLICATE_LEDGER — stripe_payment_intent_id, stripe_amount_cents,
    diff_cents, stripe_created_at all None."""

    def test_type4_null_pattern_accepted(self) -> None:
        msg = ReconciliationMessage(
            transaction_id="pi_test_006",
            discrepancy_type="DUPLICATE_LEDGER",
            stripe_payment_intent_id=None,
            internal_amount_cents=10000,
            stripe_amount_cents=None,
            diff_cents=None,
            currency="USD",
            merchant_id="merch_abc",
            stripe_created_at=None,
            run_date=date(2026, 3, 27),
        )
        assert msg.discrepancy_type == "DUPLICATE_LEDGER"
        assert msg.stripe_payment_intent_id is None
        assert msg.stripe_amount_cents is None
        assert msg.diff_cents is None
        assert msg.stripe_created_at is None
        assert msg.internal_amount_cents == 10000


class TestReconciliationMessageValidation:
    """Validation constraints."""

    def test_invalid_discrepancy_type_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            ReconciliationMessage(
                transaction_id="pi_test_007",
                discrepancy_type="INVALID_TYPE",  # type: ignore[arg-type]
                merchant_id="merch_abc",
                run_date=date(2026, 3, 27),
            )

    def test_run_date_is_date_not_datetime(self) -> None:
        msg = ReconciliationMessage(
            transaction_id="pi_test_008",
            discrepancy_type="DUPLICATE_LEDGER",
            merchant_id="merch_abc",
            run_date=date(2026, 3, 27),
        )
        assert type(msg.run_date) is date
        assert not isinstance(msg.run_date, datetime)

    def test_default_currency_is_usd(self) -> None:
        msg = ReconciliationMessage(
            transaction_id="pi_test_009",
            discrepancy_type="DUPLICATE_LEDGER",
            merchant_id="merch_abc",
            run_date=date(2026, 3, 27),
        )
        assert msg.currency == "USD"

    def test_model_dump_produces_serializable_dict(self) -> None:
        msg = ReconciliationMessage(
            transaction_id="pi_test_010",
            discrepancy_type="MISSING_INTERNALLY",
            stripe_payment_intent_id="pi_stripe_010",
            stripe_amount_cents=5000,
            stripe_created_at=datetime(2026, 3, 27, 12, 0, 0),
            merchant_id="merch_abc",
            run_date=date(2026, 3, 27),
        )
        data = msg.model_dump()
        assert data["transaction_id"] == "pi_test_010"
        assert data["discrepancy_type"] == "MISSING_INTERNALLY"
        assert data["internal_amount_cents"] is None
        assert data["diff_cents"] is None
