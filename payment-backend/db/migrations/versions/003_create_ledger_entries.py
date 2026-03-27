"""Create ledger_entries table with append-only and balance triggers.

Revision ID: 003
Revises: 002
Create Date: 2026-03-27

Table: ledger_entries
- Double-entry ledger: 1 DEBIT + 1 CREDIT per SETTLED transaction
- Append-only: DB trigger raises exception on UPDATE or DELETE
- Balance constraint: DEFERRABLE INITIALLY DEFERRED trigger enforces
  SUM(amount_cents per entry_type) = 0 per transaction_id at commit time
- Indexes on transaction_id and source_event_id for query performance
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ledger_entries table with append-only and balance enforcement."""

    # Create the ledger_entries table
    op.create_table(
        "ledger_entries",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("entry_type", sa.Text(), nullable=False),  # 'DEBIT' or 'CREDIT'
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column(
            "currency",
            sa.Text(),
            server_default=sa.text("'USD'"),
            nullable=False,
        ),
        sa.Column("source_event_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Index on transaction_id for balance lookups and reconciliation queries
    op.create_index(
        "ix_ledger_entries_transaction_id",
        "ledger_entries",
        ["transaction_id"],
    )

    # Index on source_event_id for idempotency lookups
    op.create_index(
        "ix_ledger_entries_source_event_id",
        "ledger_entries",
        ["source_event_id"],
    )

    # Create PL/pgSQL trigger function to enforce append-only constraint
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_ledger_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
          RAISE EXCEPTION 'ledger_entries is append-only: UPDATE and DELETE are prohibited';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Trigger: block all UPDATE operations
    op.execute(
        """
        CREATE TRIGGER no_update_ledger_entries
          BEFORE UPDATE ON ledger_entries
          FOR EACH ROW EXECUTE FUNCTION prevent_ledger_mutation();
        """
    )

    # Trigger: block all DELETE operations
    op.execute(
        """
        CREATE TRIGGER no_delete_ledger_entries
          BEFORE DELETE ON ledger_entries
          FOR EACH ROW EXECUTE FUNCTION prevent_ledger_mutation();
        """
    )

    # Create PL/pgSQL balance check trigger function.
    # Computes: CREDIT amount_cents - DEBIT amount_cents per transaction_id.
    # Both DEBIT and CREDIT store positive amounts; balance must sum to 0.
    # DEFERRABLE INITIALLY DEFERRED ensures this runs at transaction COMMIT,
    # not after each individual INSERT — allows both entries to be inserted
    # in a single transaction before the balance is verified.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_ledger_balance()
        RETURNS TRIGGER AS $$
        DECLARE
          balance BIGINT;
        BEGIN
          SELECT COALESCE(
            SUM(CASE WHEN entry_type = 'CREDIT' THEN amount_cents ELSE 0 END) -
            SUM(CASE WHEN entry_type = 'DEBIT' THEN amount_cents ELSE 0 END),
            0
          ) INTO balance
          FROM ledger_entries
          WHERE transaction_id = NEW.transaction_id;

          IF balance != 0 THEN
            RAISE EXCEPTION 'Ledger imbalance for transaction_id %: balance = % (must be 0)', NEW.transaction_id, balance;
          END IF;

          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # Constraint trigger: fires AFTER INSERT, DEFERRABLE INITIALLY DEFERRED.
    # Deferred execution ensures both DEBIT and CREDIT entries are visible
    # before the balance is checked at transaction commit.
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER check_ledger_balance
          AFTER INSERT ON ledger_entries
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION enforce_ledger_balance();
        """
    )


def downgrade() -> None:
    """Drop all triggers, functions, indexes, and ledger_entries table."""

    op.execute("DROP TRIGGER IF EXISTS check_ledger_balance ON ledger_entries;")
    op.execute("DROP TRIGGER IF EXISTS no_delete_ledger_entries ON ledger_entries;")
    op.execute("DROP TRIGGER IF EXISTS no_update_ledger_entries ON ledger_entries;")
    op.execute("DROP FUNCTION IF EXISTS enforce_ledger_balance();")
    op.execute("DROP FUNCTION IF EXISTS prevent_ledger_mutation();")

    op.drop_index("ix_ledger_entries_source_event_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_transaction_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")
