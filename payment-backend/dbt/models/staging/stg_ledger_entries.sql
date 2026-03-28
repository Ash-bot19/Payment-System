-- stg_ledger_entries: All rows from ledger_entries source.
-- Thin staging model — no transformations, just column aliasing for clarity.

select
    id              as ledger_entry_id,
    transaction_id,
    amount_cents,
    entry_type,
    merchant_id,
    currency,
    source_event_id,
    created_at
from {{ source('payment_db', 'ledger_entries') }}
