# Requirements: Payment System — v1.1

**Defined:** 2026-03-22
**Core Value:** Every payment event is reliably ingested, deduplicated, scored for fraud risk, and recorded in an auditable double-entry ledger with no data loss.

## v1.1 Requirements

### Consumer

- [x] **CONSUMER-01**: Validation service consumes from `payment.webhook.received` with manual offset commit only (never auto-commit)
- [x] **CONSUMER-02**: Consumer group ID is `validation-service`

### Validation

- [x] **VALID-01**: Service validates event schema (required fields present, correct types) — malformed events rejected
- [x] **VALID-02**: Service validates business rules (supported event types, amount > 0 for payment events)
- [x] **VALID-03**: Failed validation publishes to `payment.dlq` with full DLQ contract (failure_reason: SCHEMA_INVALID, original_topic, original_offset, retry_count, first_failure_ts, payload verbatim)

### Rate Limiting

- [x] **RATELIMIT-01**: Requests exceeding 100/min per merchant trigger rate-limit path via `INCR rate_limit:{merchant_id}:{minute_bucket}` in Redis

### State Machine

- [x] **SM-01**: `payment_state_log` table created via Alembic migration (append-only — no UPDATE, no DELETE)
- [x] **SM-02**: Valid event writes INITIATED → VALIDATED transition to `payment_state_log`
- [x] **SM-03**: Failed or rate-limited event writes → FAILED transition to `payment_state_log`
- [x] **SM-04**: Validated event published to `payment.transaction.validated` Kafka topic

### Quality

- [ ] **QUAL-01**: Integration tests covering happy path, schema failure → DLQ, rate limit, and state transitions
- [x] **QUAL-02**: Unit tests for business rule validation logic

## Future Requirements (Deferred)

### Spark Feature Engineering (v1.2)
- Spark Structured Streaming consuming `payment.transaction.validated`
- 8 ML input features engineered (tx_velocity_1m, tx_velocity_5m, amount_zscore, merchant_risk_score, device_switch_flag, hour_of_day, weekend_flag, amount_cents_log)
- Features written to Redis online feature store

### ML Scoring (v1.2)
- XGBoost inference service (p99 < 100ms SLA)
- Fallback on Redis timeout > 20ms → manual_review=true
- Publishes to `payment.transaction.scored` and `payment.alert.triggered`

### Ledger + Reconciliation (v1.3+)
- Double-entry ledger consumer
- Airflow nightly reconciliation DAG

### Analytics + Deploy (v2.0)
- BigQuery + dbt transformation layer
- Streamlit observability dashboard
- GCP deployment + GitHub Actions CI/CD

## Out of Scope

| Feature | Reason |
|---------|--------|
| Multi-currency | USD only in MVP |
| Live mode Stripe | Test mode only until GCP verified |
| Auto offset commit | Explicitly prohibited by CLAUDE.md |
| Spark / ML scoring | Deferred to v1.2 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CONSUMER-01 | Phase 2 | Complete |
| CONSUMER-02 | Phase 2 | Complete |
| VALID-01 | Phase 2 | Complete |
| VALID-02 | Phase 2 | Complete |
| VALID-03 | Phase 2 | Complete |
| QUAL-02 | Phase 2 | Complete |
| SM-01 | Phase 3 | Complete |
| SM-02 | Phase 3 | Complete |
| SM-03 | Phase 3 | Complete |
| SM-04 | Phase 3 | Complete |
| RATELIMIT-01 | Phase 3 | Complete |
| QUAL-01 | Phase 3 | Pending |

**Coverage:**
- v1.1 requirements: 12 total
- Mapped to phases: 12
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-22*
*Last updated: 2026-03-22 — traceability filled after v1.1 roadmap creation*
