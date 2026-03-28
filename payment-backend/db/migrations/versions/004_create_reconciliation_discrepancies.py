"""Create reconciliation_discrepancies table with append-only trigger.

Revision ID: 004
Revises: 003

Table: reconciliation_discrepancies
- Stores discrepancies detected by the nightly Airflow reconciliation DAG.
- Append-only: DB trigger raises exception on UPDATE or DELETE.
- Used as the dbt source for the reconciliation_summary mart (Phase 8, Plan 02).
- Columns mirror ReconciliationMessage fields plus server-assigned id and created_at.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create reconciliation_discrepancies table with append-only trigger."""

    op.create_table(
        "reconciliation_discrepancies",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("discrepancy_type", sa.Text(), nullable=False),
        sa.Column("stripe_payment_intent_id", sa.Text(), nullable=True),
        sa.Column("internal_amount_cents", sa.BigInteger(), nullable=True),
        sa.Column("stripe_amount_cents", sa.BigInteger(), nullable=True),
        sa.Column("diff_cents", sa.BigInteger(), nullable=True),
        sa.Column(
            "currency",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("stripe_created_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_recon_discrepancies_transaction_id",
        "reconciliation_discrepancies",
        ["transaction_id"],
    )
    op.create_index(
        "ix_recon_discrepancies_run_date",
        "reconciliation_discrepancies",
        ["run_date"],
    )
    op.create_index(
        "ix_recon_discrepancies_discrepancy_type",
        "reconciliation_discrepancies",
        ["discrepancy_type"],
    )

    # Create PL/pgSQL trigger function to enforce append-only constraint.
    # New function name — does not conflict with prevent_ledger_mutation() in 003.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_discrepancies_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
          RAISE EXCEPTION 'reconciliation_discrepancies is append-only: UPDATE and DELETE are prohibited';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Trigger: block all UPDATE operations
    op.execute(
        """
        CREATE TRIGGER no_update_reconciliation_discrepancies
          BEFORE UPDATE ON reconciliation_discrepancies
          FOR EACH ROW EXECUTE FUNCTION prevent_discrepancies_mutation();
        """
    )

    # Trigger: block all DELETE operations
    op.execute(
        """
        CREATE TRIGGER no_delete_reconciliation_discrepancies
          BEFORE DELETE ON reconciliation_discrepancies
          FOR EACH ROW EXECUTE FUNCTION prevent_discrepancies_mutation();
        """
    )


def downgrade() -> None:
    """Drop all triggers, functions, indexes, and reconciliation_discrepancies table."""

    op.execute(
        "DROP TRIGGER IF EXISTS no_delete_reconciliation_discrepancies "
        "ON reconciliation_discrepancies;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS no_update_reconciliation_discrepancies "
        "ON reconciliation_discrepancies;"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_discrepancies_mutation();")

    op.drop_index(
        "ix_recon_discrepancies_discrepancy_type",
        table_name="reconciliation_discrepancies",
    )
    op.drop_index(
        "ix_recon_discrepancies_run_date",
        table_name="reconciliation_discrepancies",
    )
    op.drop_index(
        "ix_recon_discrepancies_transaction_id",
        table_name="reconciliation_discrepancies",
    )
    op.drop_table("reconciliation_discrepancies")
