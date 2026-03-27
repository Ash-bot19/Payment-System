"""Create manual_review_queue table with append-only trigger.

Revision ID: 002
Revises: 001
Create Date: 2026-03-27

Table: manual_review_queue
- Rows inserted when a FLAGGED event has manual_review=True
- Append-only: DB trigger raises exception on UPDATE or DELETE
- Indexes on transaction_id and status for query performance
- status defaults to 'PENDING'
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create manual_review_queue table with append-only enforcement."""

    # Create the manual_review_queue table
    op.create_table(
        "manual_review_queue",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Index on transaction_id for payment lookup queries
    op.create_index(
        "ix_manual_review_queue_transaction_id",
        "manual_review_queue",
        ["transaction_id"],
    )

    # Index on status for queue processing queries (PENDING, REVIEWED, APPROVED, REJECTED)
    op.create_index(
        "ix_manual_review_queue_status",
        "manual_review_queue",
        ["status"],
    )

    # Create PL/pgSQL trigger function to enforce append-only constraint
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_review_queue_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
          RAISE EXCEPTION 'manual_review_queue is append-only: UPDATE and DELETE are prohibited';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Trigger: block all UPDATE operations
    op.execute(
        """
        CREATE TRIGGER no_update_review_queue
          BEFORE UPDATE ON manual_review_queue
          FOR EACH ROW EXECUTE FUNCTION prevent_review_queue_mutation();
        """
    )

    # Trigger: block all DELETE operations
    op.execute(
        """
        CREATE TRIGGER no_delete_review_queue
          BEFORE DELETE ON manual_review_queue
          FOR EACH ROW EXECUTE FUNCTION prevent_review_queue_mutation();
        """
    )


def downgrade() -> None:
    """Drop triggers, trigger function, indexes, and manual_review_queue table."""

    op.execute("DROP TRIGGER IF EXISTS no_delete_review_queue ON manual_review_queue;")
    op.execute("DROP TRIGGER IF EXISTS no_update_review_queue ON manual_review_queue;")
    op.execute("DROP FUNCTION IF EXISTS prevent_review_queue_mutation();")

    op.drop_index("ix_manual_review_queue_status", table_name="manual_review_queue")
    op.drop_index(
        "ix_manual_review_queue_transaction_id", table_name="manual_review_queue"
    )
    op.drop_table("manual_review_queue")
