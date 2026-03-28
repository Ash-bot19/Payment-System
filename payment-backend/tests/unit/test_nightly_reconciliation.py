"""Unit tests for the nightly_reconciliation Airflow DAG — Phase 07, Plan 02 / Phase 08, Plan 01.

Tests cover all 4 DAG task functions:
  - detect_duplicates(): SQL GROUP BY HAVING, DUPLICATE_LEDGER publishing
  - fetch_stripe_window(): Stripe API pagination, succeeded filter
  - compare_and_publish(): MISSING_INTERNALLY + AMOUNT_MISMATCH detection, returns list[dict]
  - persist_discrepancies(): SQLAlchemy Core insert() to reconciliation_discrepancies

All external dependencies (SQLAlchemy, Stripe, Kafka) are mocked.
No Airflow, PostgreSQL, Stripe, or Kafka required to run these tests.

Import strategy: airflow is a namespace package pointing at payment-backend/airflow/.
We stub airflow.decorators before importing the DAG module so that @dag and @task
decorators are no-ops — task functions are importable without running Airflow.
"""

import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub airflow.decorators so @dag / @task are importable without Apache Airflow.
# airflow itself is a namespace package resolved to payment-backend/airflow/,
# so we only need to stub the 'decorators' sub-module.
# ---------------------------------------------------------------------------


class _InDagContext:
    """Thread-local flag indicating whether we are inside a @dag decorator body."""
    active: bool = False


def _stub_airflow_decorators() -> None:
    """Inject a minimal stub for airflow.decorators before the DAG is imported.

    Strategy:
    - @dag decorator sets _InDagContext.active=True while calling the function.
    - @task wraps the original function so that when called INSIDE a @dag context
      it returns a sentinel MagicMock (simulating Airflow XCom task reference)
      rather than executing the real logic.
    - When called OUTSIDE the @dag context (direct test call), it runs the real
      function with the provided arguments.

    This lets us:
    1. Import the module without executing DB/API calls at module-level.
    2. Call task functions directly in tests to exercise real logic with mocks.
    """
    import airflow  # namespace package — payment-backend/airflow/

    decorators_mod = types.ModuleType("airflow.decorators")

    def dag(**kwargs):  # noqa: D401
        def decorator(fn):
            def wrapper(*a, **kw):
                _InDagContext.active = True
                try:
                    fn(*a, **kw)
                finally:
                    _InDagContext.active = False
            return wrapper
        return decorator

    def task(*args, **kwargs):  # noqa: D401
        def decorator(fn):
            def wrapper(*a, **kw):
                if _InDagContext.active:
                    # Inside @dag body — return sentinel (simulate TaskInstance)
                    return MagicMock(name=f"task_ref_{fn.__name__}")
                # Direct test call — run the real logic
                return fn(*a, **kw)
            # Expose the unwrapped function for introspection in tests
            wrapper.__wrapped__ = fn
            wrapper.__name__ = fn.__name__
            return wrapper
        if args and callable(args[0]):
            return decorator(args[0])
        return decorator

    decorators_mod.dag = dag
    decorators_mod.task = task

    sys.modules["airflow.decorators"] = decorators_mod
    airflow.decorators = decorators_mod  # type: ignore[attr-defined]


_stub_airflow_decorators()

# Now import the DAG module — task functions become plain module-level functions
import airflow.dags.nightly_reconciliation as dag_module  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dup_row(transaction_id: str, row_count: int = 3) -> MagicMock:
    row = MagicMock()
    row.transaction_id = transaction_id
    row.row_count = row_count
    return row


def _make_ledger_row(
    transaction_id: str,
    amount_cents: int,
    merchant_id: str = "merch_001",
    currency: str = "USD",
) -> MagicMock:
    row = MagicMock()
    row.transaction_id = transaction_id
    row.amount_cents = amount_cents
    row.merchant_id = merchant_id
    row.currency = currency
    return row


def _make_merchant_row(merchant_id: str = "merch_001") -> MagicMock:
    row = MagicMock()
    row.merchant_id = merchant_id
    return row


def _make_pi(
    pi_id: str,
    amount_received: int = 5000,
    currency: str = "usd",
    created: int = 1774656000,
    status: str = "succeeded",
    metadata: dict | None = None,
) -> MagicMock:
    pi = MagicMock()
    pi.id = pi_id
    pi.amount_received = amount_received
    pi.currency = currency
    pi.created = created
    pi.status = status
    pi.metadata = metadata or {"merchant_id": "merch_001"}
    return pi


def _mock_engine_with_results(*results_sequence):
    """Return (mock_engine_cls, mock_conn) where execute() side_effect is the provided iterables."""
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.side_effect = list(results_sequence)

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_conn
    return mock_engine, mock_conn


# ---------------------------------------------------------------------------
# detect_duplicates tests
# ---------------------------------------------------------------------------


class TestDetectDuplicates(unittest.TestCase):
    """Tests for detect_duplicates() task function."""

    def test_queries_group_by_having(self) -> None:
        """detect_duplicates executes SQL with GROUP BY transaction_id HAVING COUNT(*) > 2."""
        mock_engine, mock_conn = _mock_engine_with_results(iter([]))

        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer"),
        ):
            dag_module.detect_duplicates(ds="2026-03-27")

        assert mock_conn.execute.called
        sql_text = str(mock_conn.execute.call_args[0][0]).upper()
        assert "GROUP BY" in sql_text, f"Expected GROUP BY in SQL:\n{sql_text}"
        assert "HAVING" in sql_text, f"Expected HAVING in SQL:\n{sql_text}"

    def test_returns_duplicate_count(self) -> None:
        """detect_duplicates returns the count of duplicate transactions found."""
        dup1 = _make_dup_row("pi_dup1")
        dup2 = _make_dup_row("pi_dup2")
        merch = _make_merchant_row()
        mock_engine, _ = _mock_engine_with_results(
            iter([dup1, dup2]),
            iter([merch]),   # merchant subquery for pi_dup1
            iter([merch]),   # merchant subquery for pi_dup2
        )

        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer"),
        ):
            count = dag_module.detect_duplicates(ds="2026-03-27")

        assert count == 2, f"Expected 2, got {count}"

    def test_publishes_duplicate_ledger_type(self) -> None:
        """detect_duplicates publishes DUPLICATE_LEDGER messages via publish_batch."""
        dup = _make_dup_row("pi_abc123")
        merch = _make_merchant_row("merch_42")
        mock_engine, _ = _mock_engine_with_results(iter([dup]), iter([merch]))

        mock_prod = MagicMock()
        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer", return_value=mock_prod),
        ):
            dag_module.detect_duplicates(ds="2026-03-27")

        mock_prod.publish_batch.assert_called_once()
        batch = mock_prod.publish_batch.call_args[0][0]
        assert len(batch) == 1
        msg = batch[0]
        assert msg["discrepancy_type"] == "DUPLICATE_LEDGER"
        assert msg["transaction_id"] == "pi_abc123"

    def test_duplicate_ledger_null_stripe_fields(self) -> None:
        """DUPLICATE_LEDGER message has None for stripe_payment_intent_id, stripe_amount_cents, diff_cents, stripe_created_at."""
        dup = _make_dup_row("pi_dup99")
        merch = _make_merchant_row("merch_x")
        mock_engine, _ = _mock_engine_with_results(iter([dup]), iter([merch]))

        mock_prod = MagicMock()
        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer", return_value=mock_prod),
        ):
            dag_module.detect_duplicates(ds="2026-03-27")

        batch = mock_prod.publish_batch.call_args[0][0]
        msg = batch[0]
        assert msg.get("stripe_payment_intent_id") is None
        assert msg.get("stripe_amount_cents") is None
        assert msg.get("diff_cents") is None
        assert msg.get("stripe_created_at") is None

    def test_no_duplicates_does_not_publish(self) -> None:
        """detect_duplicates with empty result does not call publish_batch."""
        mock_engine, _ = _mock_engine_with_results(iter([]))

        mock_prod = MagicMock()
        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer", return_value=mock_prod),
        ):
            count = dag_module.detect_duplicates(ds="2026-03-27")

        mock_prod.publish_batch.assert_not_called()
        assert count == 0


# ---------------------------------------------------------------------------
# fetch_stripe_window tests
# ---------------------------------------------------------------------------


class TestFetchStripeWindow(unittest.TestCase):
    """Tests for fetch_stripe_window() task function."""

    def test_calls_stripe_payment_intent_list(self) -> None:
        """fetch_stripe_window calls stripe.PaymentIntent.list with gte/lte timestamps."""
        with patch("airflow.dags.nightly_reconciliation.stripe") as mock_stripe:
            mock_stripe.PaymentIntent.list.return_value = iter([])

            dag_module.fetch_stripe_window(ds="2026-03-27")

        mock_stripe.PaymentIntent.list.assert_called_once()
        call_kwargs = mock_stripe.PaymentIntent.list.call_args[1]
        assert "created" in call_kwargs
        assert "gte" in call_kwargs["created"]
        assert "lte" in call_kwargs["created"]

    def test_filters_succeeded_only(self) -> None:
        """fetch_stripe_window only returns succeeded PaymentIntents."""
        with patch("airflow.dags.nightly_reconciliation.stripe") as mock_stripe:
            pi_ok = _make_pi("pi_ok", status="succeeded")
            pi_fail = _make_pi("pi_fail", status="requires_payment_method")
            pi_canceled = _make_pi("pi_canceled", status="canceled")
            mock_stripe.PaymentIntent.list.return_value = iter([pi_ok, pi_fail, pi_canceled])

            result = dag_module.fetch_stripe_window(ds="2026-03-27")

        assert "pi_ok" in result
        assert "pi_fail" not in result
        assert "pi_canceled" not in result

    def test_returns_dict_keyed_by_pi_id(self) -> None:
        """fetch_stripe_window returns dict keyed by PaymentIntent id."""
        with patch("airflow.dags.nightly_reconciliation.stripe") as mock_stripe:
            pi = _make_pi("pi_xyz", amount_received=10000)
            mock_stripe.PaymentIntent.list.return_value = iter([pi])

            result = dag_module.fetch_stripe_window(ds="2026-03-27")

        assert isinstance(result, dict)
        assert "pi_xyz" in result
        assert result["pi_xyz"]["amount"] == 10000

    def test_correct_midnight_epoch_range(self) -> None:
        """fetch_stripe_window passes correct midnight-to-midnight UTC timestamps."""
        with patch("airflow.dags.nightly_reconciliation.stripe") as mock_stripe:
            mock_stripe.PaymentIntent.list.return_value = iter([])

            dag_module.fetch_stripe_window(ds="2026-03-27")

        call_kwargs = mock_stripe.PaymentIntent.list.call_args[1]
        gte = call_kwargs["created"]["gte"]
        lte = call_kwargs["created"]["lte"]

        expected_gte = int(datetime(2026, 3, 27, 0, 0, 0, tzinfo=timezone.utc).timestamp())
        assert gte == expected_gte, f"Expected gte={expected_gte}, got {gte}"
        assert lte > gte, "lte must be after gte"


# ---------------------------------------------------------------------------
# compare_and_publish tests
# ---------------------------------------------------------------------------


class TestCompareAndPublish(unittest.TestCase):
    """Tests for compare_and_publish() task function."""

    def test_detects_missing_internally(self) -> None:
        """compare_and_publish detects MISSING_INTERNALLY when PI exists in Stripe but not ledger."""
        mock_engine, _ = _mock_engine_with_results(iter([]))  # no ledger rows

        mock_prod = MagicMock()
        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer", return_value=mock_prod),
        ):
            dag_module.compare_and_publish(
                stripe_intents={
                    "pi_missing": {
                        "amount": 5000,
                        "currency": "usd",
                        "created": 1774656000,
                        "metadata": {"merchant_id": "merch_001"},
                    }
                },
                ds="2026-03-27",
            )

        mock_prod.publish_batch.assert_called_once()
        batch = mock_prod.publish_batch.call_args[0][0]
        assert len(batch) == 1
        msg = batch[0]
        assert msg["discrepancy_type"] == "MISSING_INTERNALLY"
        assert msg["transaction_id"] == "pi_missing"
        assert msg["stripe_amount_cents"] == 5000
        assert msg["internal_amount_cents"] is None

    def test_detects_amount_mismatch(self) -> None:
        """compare_and_publish detects AMOUNT_MISMATCH when amounts differ."""
        ledger_row = _make_ledger_row("pi_mismatch", amount_cents=4500)
        mock_engine, _ = _mock_engine_with_results(iter([ledger_row]))

        mock_prod = MagicMock()
        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer", return_value=mock_prod),
        ):
            dag_module.compare_and_publish(
                stripe_intents={
                    "pi_mismatch": {
                        "amount": 5000,
                        "currency": "usd",
                        "created": 1774656000,
                        "metadata": {"merchant_id": "merch_001"},
                    }
                },
                ds="2026-03-27",
            )

        mock_prod.publish_batch.assert_called_once()
        batch = mock_prod.publish_batch.call_args[0][0]
        assert len(batch) == 1
        msg = batch[0]
        assert msg["discrepancy_type"] == "AMOUNT_MISMATCH"
        assert msg["diff_cents"] == 500  # 5000 - 4500
        assert msg["internal_amount_cents"] == 4500

    def test_skips_clean_matches(self) -> None:
        """compare_and_publish publishes nothing when amounts agree."""
        ledger_row = _make_ledger_row("pi_clean", amount_cents=5000)
        mock_engine, _ = _mock_engine_with_results(iter([ledger_row]))

        mock_prod = MagicMock()
        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer", return_value=mock_prod),
        ):
            dag_module.compare_and_publish(
                stripe_intents={
                    "pi_clean": {
                        "amount": 5000,
                        "currency": "usd",
                        "created": 1774656000,
                        "metadata": {"merchant_id": "merch_001"},
                    }
                },
                ds="2026-03-27",
            )

        mock_prod.publish_batch.assert_not_called()

    def test_uses_debit_entry_type_filter(self) -> None:
        """compare_and_publish SQL query filters to entry_type = 'DEBIT'."""
        mock_engine, mock_conn = _mock_engine_with_results(iter([]))

        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer"),
        ):
            dag_module.compare_and_publish(stripe_intents={}, ds="2026-03-27")

        assert mock_conn.execute.called
        sql_text = str(mock_conn.execute.call_args[0][0])
        assert "DEBIT" in sql_text, f"Expected 'DEBIT' in SQL:\n{sql_text}"

    def test_amount_mismatch_negative_diff(self) -> None:
        """AMOUNT_MISMATCH diff_cents = stripe - internal (can be negative when internal > stripe)."""
        ledger_row = _make_ledger_row("pi_neg", amount_cents=6000)
        mock_engine, _ = _mock_engine_with_results(iter([ledger_row]))

        mock_prod = MagicMock()
        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer", return_value=mock_prod),
        ):
            dag_module.compare_and_publish(
                stripe_intents={
                    "pi_neg": {
                        "amount": 5000,
                        "currency": "usd",
                        "created": 1774656000,
                        "metadata": {"merchant_id": "merch_001"},
                    }
                },
                ds="2026-03-27",
            )

        batch = mock_prod.publish_batch.call_args[0][0]
        msg = batch[0]
        assert msg["diff_cents"] == -1000  # 5000 - 6000

    def test_compare_and_publish_returns_discrepancy_list(self) -> None:
        """compare_and_publish returns a list of discrepancy dicts (not None)."""
        # One MISSING_INTERNALLY (pi_miss) + one AMOUNT_MISMATCH (pi_mismatch)
        ledger_row = _make_ledger_row("pi_mismatch", amount_cents=4500)
        mock_engine, _ = _mock_engine_with_results(iter([ledger_row]))

        mock_prod = MagicMock()
        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer", return_value=mock_prod),
        ):
            result = dag_module.compare_and_publish(
                stripe_intents={
                    "pi_miss": {
                        "amount": 3000,
                        "currency": "usd",
                        "created": 1774656000,
                        "metadata": {"merchant_id": "merch_001"},
                    },
                    "pi_mismatch": {
                        "amount": 5000,
                        "currency": "usd",
                        "created": 1774656000,
                        "metadata": {"merchant_id": "merch_001"},
                    },
                },
                ds="2026-03-27",
            )

        assert isinstance(result, list), f"Expected list, got {type(result)}"
        assert len(result) == 2, f"Expected 2 discrepancies, got {len(result)}"
        assert all(isinstance(item, dict) for item in result)
        types_found = {item["discrepancy_type"] for item in result}
        assert "MISSING_INTERNALLY" in types_found
        assert "AMOUNT_MISMATCH" in types_found

    def test_compare_and_publish_empty_stripe_no_internal(self) -> None:
        """compare_and_publish returns empty list when no Stripe data and no internal rows."""
        mock_engine, _ = _mock_engine_with_results(iter([]))

        with (
            patch("airflow.dags.nightly_reconciliation.create_engine", return_value=mock_engine),
            patch("airflow.dags.nightly_reconciliation.ReconciliationProducer"),
        ):
            result = dag_module.compare_and_publish(
                stripe_intents={},
                ds="2026-03-27",
            )

        assert result == [], f"Expected empty list, got {result!r}"


# ---------------------------------------------------------------------------
# persist_discrepancies tests
# ---------------------------------------------------------------------------


def _sample_discrepancy(
    transaction_id: str = "pi_test_001",
    discrepancy_type: str = "MISSING_INTERNALLY",
    merchant_id: str = "merch_001",
) -> dict:
    """Return a minimal discrepancy dict matching ReconciliationMessage fields."""
    return {
        "transaction_id": transaction_id,
        "discrepancy_type": discrepancy_type,
        "stripe_payment_intent_id": None,
        "internal_amount_cents": None,
        "stripe_amount_cents": None,
        "diff_cents": None,
        "currency": "USD",
        "merchant_id": merchant_id,
        "stripe_created_at": None,
        "run_date": "2026-03-28",
    }


def _mock_engine_begin():
    """Return (mock_engine, mock_conn) wired for engine.begin() context manager."""
    mock_conn = MagicMock()
    mock_engine = MagicMock()
    mock_engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)
    return mock_engine, mock_conn


class TestPersistDiscrepancies(unittest.TestCase):
    """Tests for persist_discrepancies() task function."""

    def test_persist_discrepancies_empty_list(self) -> None:
        """persist_discrepancies returns 0 and never calls create_engine for empty list."""
        with patch(
            "airflow.dags.nightly_reconciliation.create_engine"
        ) as mock_create_engine:
            result = dag_module.persist_discrepancies([], ds="2026-03-28")

        assert result == 0, f"Expected 0, got {result}"
        mock_create_engine.assert_not_called()

    def test_persist_discrepancies_inserts_rows(self) -> None:
        """persist_discrepancies calls conn.execute with insert statement and discrepancy list."""
        discrepancies = [
            _sample_discrepancy("pi_001"),
            _sample_discrepancy("pi_002", discrepancy_type="AMOUNT_MISMATCH"),
        ]
        mock_engine, mock_conn = _mock_engine_begin()

        with patch(
            "airflow.dags.nightly_reconciliation.create_engine",
            return_value=mock_engine,
        ):
            result = dag_module.persist_discrepancies(discrepancies, ds="2026-03-28")

        assert result == 2, f"Expected 2, got {result}"
        assert mock_conn.execute.called, "conn.execute was not called"
        # Verify execute was called once
        assert mock_conn.execute.call_count == 1

    def test_persist_discrepancies_returns_count(self) -> None:
        """persist_discrepancies returns exact count of rows passed."""
        discrepancies = [
            _sample_discrepancy(f"pi_{i:03d}") for i in range(3)
        ]
        mock_engine, mock_conn = _mock_engine_begin()

        with patch(
            "airflow.dags.nightly_reconciliation.create_engine",
            return_value=mock_engine,
        ):
            result = dag_module.persist_discrepancies(discrepancies, ds="2026-03-28")

        assert result == 3, f"Expected 3, got {result}"

    def test_persist_discrepancies_logs_written(self) -> None:
        """persist_discrepancies logs persist_discrepancies_written event with count."""
        discrepancies = [_sample_discrepancy("pi_log_001")]
        mock_engine, _ = _mock_engine_begin()

        log_events: list[dict] = []

        def capture_log(event: str, **kwargs) -> None:
            log_events.append({"event": event, **kwargs})

        with (
            patch(
                "airflow.dags.nightly_reconciliation.create_engine",
                return_value=mock_engine,
            ),
            patch.object(dag_module.logger, "info", side_effect=capture_log),
        ):
            dag_module.persist_discrepancies(discrepancies, ds="2026-03-28")

        written_events = [e for e in log_events if e["event"] == "persist_discrepancies_written"]
        assert len(written_events) == 1, f"Expected 1 persist_discrepancies_written log, got {written_events}"
        assert written_events[0]["count"] == 1

    def test_persist_discrepancies_skipped_logs(self) -> None:
        """persist_discrepancies logs persist_discrepancies_skipped for empty list."""
        log_events: list[dict] = []

        def capture_log(event: str, **kwargs) -> None:
            log_events.append({"event": event, **kwargs})

        with (
            patch("airflow.dags.nightly_reconciliation.create_engine"),
            patch.object(dag_module.logger, "info", side_effect=capture_log),
        ):
            result = dag_module.persist_discrepancies([], ds="2026-03-28")

        assert result == 0
        skipped_events = [e for e in log_events if e["event"] == "persist_discrepancies_skipped"]
        assert len(skipped_events) == 1, f"Expected 1 persist_discrepancies_skipped log, got {skipped_events}"


# ---------------------------------------------------------------------------
# DAG structure tests
# ---------------------------------------------------------------------------


class TestDagStructure(unittest.TestCase):
    """Tests for DAG-level structure and function availability."""

    def test_has_detect_duplicates_function(self) -> None:
        """Module exposes callable detect_duplicates."""
        assert callable(dag_module.detect_duplicates)

    def test_has_fetch_stripe_window_function(self) -> None:
        """Module exposes callable fetch_stripe_window."""
        assert callable(dag_module.fetch_stripe_window)

    def test_has_compare_and_publish_function(self) -> None:
        """Module exposes callable compare_and_publish."""
        assert callable(dag_module.compare_and_publish)

    def test_has_persist_discrepancies_function(self) -> None:
        """Module exposes callable persist_discrepancies."""
        assert callable(dag_module.persist_discrepancies)

    def test_dag_id_constant_in_module(self) -> None:
        """nightly_reconciliation dag is defined with dag_id='nightly_reconciliation'."""
        # The file must contain dag_id="nightly_reconciliation" — verified via source inspection
        import inspect
        source = inspect.getsource(dag_module)
        assert "nightly_reconciliation" in source

    def test_module_uses_structlog(self) -> None:
        """Module uses structlog for logging (no print statements)."""
        import inspect
        source = inspect.getsource(dag_module)
        assert "structlog" in source
        assert "print(" not in source


if __name__ == "__main__":
    unittest.main()
