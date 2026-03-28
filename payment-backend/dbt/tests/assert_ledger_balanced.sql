-- assert_ledger_balanced: Singular test on fact_ledger_balanced.
-- Returns rows where debit_amount_cents != credit_amount_cents.
-- dbt test PASSES when this query returns ZERO rows.
-- dbt test FAILS when this query returns any rows (imbalanced transactions found).
--
-- Note: No semicolon at end — dbt wraps this query; trailing semicolon causes
-- a parse error.

select
    transaction_id,
    debit_amount_cents,
    credit_amount_cents,
    merchant_id
from {{ ref('fact_ledger_balanced') }}
where debit_amount_cents != credit_amount_cents