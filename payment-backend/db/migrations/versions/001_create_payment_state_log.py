"""Create payment_state_log table with append-only trigger.

Revision ID: 001
Revises: None
Create Date: 2026-03-24

Table: payment_state_log
- Append-only: DB trigger raises exception on UPDATE or DELETE
- Indexes on transaction_id and event_id for query performance
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create payment_state_log table with append-only enforcement."""

    # Create the payment_state_log table
    op.create_table(
        "payment_state_log",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=True),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Index on transaction_id for state lookup queries
    op.create_index(
        "ix_payment_state_log_transaction_id",
        "payment_state_log",
        ["transaction_id"],
    )

    # Index on event_id for idempotency lookups
    op.create_index(
        "ix_payment_state_log_event_id",
        "payment_state_log",
        ["event_id"],
    )

    # Create PL/pgSQL trigger function to enforce append-only constraint
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_state_log_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
          RAISE EXCEPTION 'payment_state_log is append-only: UPDATE and DELETE are prohibited';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Trigger: block all UPDATE operations
    op.execute(
        """
        CREATE TRIGGER no_update_state_log
          BEFORE UPDATE ON payment_state_log
          FOR EACH ROW EXECUTE FUNCTION prevent_state_log_mutation();
        """
    )

    # Trigger: block all DELETE operations
    op.execute(
        """
        CREATE TRIGGER no_delete_state_log
          BEFORE DELETE ON payment_state_log
          FOR EACH ROW EXECUTE FUNCTION prevent_state_log_mutation();
        """
    )


def downgrade() -> None:
    """Drop triggers, trigger function, and payment_state_log table."""

    op.execute("DROP TRIGGER IF EXISTS no_delete_state_log ON payment_state_log;")
    op.execute("DROP TRIGGER IF EXISTS no_update_state_log ON payment_state_log;")
    op.execute("DROP FUNCTION IF EXISTS prevent_state_log_mutation();")

    op.drop_index("ix_payment_state_log_event_id", table_name="payment_state_log")
    op.drop_index(
        "ix_payment_state_log_transaction_id", table_name="payment_state_log"
    )
    op.drop_table("payment_state_log")
