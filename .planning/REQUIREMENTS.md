# Requirements: Payment System — v1.1

**Defined:** 2026-03-22
**Core Value:** Every payment event is reliably ingested, deduplicated, scored for fraud risk, and recorded in an auditable double-entry ledger with no data loss.

## v1.1 Requirements

### Consumer

- [ ] **CONSUMER-01**: Validation service consumes from `payment.webhook.received` with manual offset commit only (never auto-commit)
- [ ] **CONSUMER-02**: Consumer group ID is `validation-service`

### Validation

- [ ] **VALID-01**: Service validates event schema (required fields present, correct types) — malformed events rejected
- [ ] **VALID-02**: Service validates business rules (supported event types, amount > 0 for payment events)
- [ ] **VALID-03**: Failed validation publishes to `payment.dlq` with full DLQ contract (failure_reason: SCHEMA_INVALID, original_topic, original_offset, retry_count, first_failure_ts, payload verbatim)

### Rate Limiting

- [ ] **RATELIMIT-01**: Requests exceeding 100/min per merchant trigger rate-limit path via `INCR rate_limit:{merchant_id}:{minute_bucket}` in Redis

### State Machine

- [ ] **SM-01**: `payment_state_log` table created via Alembic migration (append-only — no UPDATE, no DELETE)
- [ ] **SM-02**: Valid event writes INITIATED → VALIDATED transition to `payment_state_log`
- [ ] **SM-03**: Failed or rate-limited event writes → FAILED transition to `payment_state_log`
- [ ] **SM-04**: Validated event published to `payment.transaction.validated` Kafka topic

### Quality

- [ ] **QUAL-01**: Integration tests covering happy path, schema failure → DLQ, rate limit, and state transitions
- [ ] **QUAL-02**: Unit tests for business rule validation logic

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
| CONSUMER-01 | TBD | Pending |
| CONSUMER-02 | TBD | Pending |
| VALID-01 | TBD | Pending |
| VALID-02 | TBD | Pending |
| VALID-03 | TBD | Pending |
| RATELIMIT-01 | TBD | Pending |
| SM-01 | TBD | Pending |
| SM-02 | TBD | Pending |
| SM-03 | TBD | Pending |
| SM-04 | TBD | Pending |
| QUAL-01 | TBD | Pending |
| QUAL-02 | TBD | Pending |

**Coverage:**
- v1.1 requirements: 12 total
- Mapped to phases: TBD (roadmapper fills this)
- Unmapped: 12 ⚠️ (pre-roadmap)

---
*Requirements defined: 2026-03-22*
*Last updated: 2026-03-22 after v1.1 milestone start*
