-- fact_ledger_balanced: One row per transaction with debit and credit amounts.
-- For a balanced ledger, debit_amount_cents = credit_amount_cents always.
-- The assert_ledger_balanced singular test queries this table and returns
-- rows on failure (where debit != credit).

select
    transaction_id,
    merchant_id,
    currency,
    max(case when entry_type = 'DEBIT'   then amount_cents else null end) as debit_amount_cents,
    max(case when entry_type = 'CREDIT'  then amount_cents else null end) as credit_amount_cents,
    min(created_at) as settled_at
from {{ ref('stg_ledger_entries') }}
group by transaction_id, merchant_id, currency
