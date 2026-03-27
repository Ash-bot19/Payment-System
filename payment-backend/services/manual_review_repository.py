"""Repository for manual_review_queue writes — Phase 06.

Inserts PENDING rows into manual_review_queue when a FLAGGED event has
manual_review=True (risk_score >= 0.85 per CLAUDE.md ML contract).

Uses SQLAlchemy Core insert() with engine.begin() for explicit append-only
semantics (same pattern as PaymentStateMachine in services/state_machine.py).
"""

import structlog
from sqlalchemy import Engine, insert

from models.ledger import ManualReviewQueueEntry

logger = structlog.get_logger(__name__)


class ManualReviewRepository:
    """Writes FLAGGED+manual_review payment events to manual_review_queue.

    Injectable via constructor — tests pass a real or mock engine without
    changing class internals.

    Uses Core insert() (not ORM session) for explicit append-only semantics.
    Each insert() call is an autocommitted INSERT via engine.begin().
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert(self, transaction_id: str, risk_score: float, payload: dict) -> None:
        """Insert a PENDING manual review row into manual_review_queue.

        Args:
            transaction_id: The payment transaction identifier (Stripe event id).
            risk_score: XGBoost risk score for the flagged event (>= 0.7).
            payload: Full ScoredPaymentEvent dict for reviewer context (JSONB).
        """
        with self._engine.begin() as conn:
            conn.execute(
                insert(ManualReviewQueueEntry.__table__).values(
                    transaction_id=transaction_id,
                    risk_score=risk_score,
                    payload=payload,
                    status="PENDING",
                )
            )
        logger.info(
            "manual_review_queued",
            transaction_id=transaction_id,
            risk_score=risk_score,
        )
