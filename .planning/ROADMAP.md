# Roadmap: Payment System

## Milestones

- ✅ **v1.0 Foundation + Ingestion** — Phase 1 (shipped 2026-03-22)
- 🚧 **v1.1 Validation + State Machine** — Phases 2-3 (active)
- 📋 **v1.2 Spark + ML Scoring** — Phases 4-5 (planned)
- 📋 **v1.3 Ledger + Reconciliation** — Phases 6-7 (planned)
- 📋 **v2.0 Analytics + Deploy** — Phases 8-11 (planned)

## Phases

<details>
<summary>✅ v1.0 Foundation + Ingestion (Phase 1) — SHIPPED 2026-03-22</summary>

### Phase 1: Foundation + Ingestion

**Goal**: Establish the full project scaffold, containerised infrastructure, and Stripe webhook ingestion pipeline into Kafka with Redis idempotency.
**Depends on**: Nothing (first phase)
**Requirements**: (v1.0 — archived in .planning/milestones/v1.0-REQUIREMENTS.md)
**Success Criteria**:
  1. POST /webhook rejects requests with invalid Stripe HMAC signatures with 400
  2. Duplicate Stripe event IDs return 200 immediately without re-publishing to Kafka
  3. Valid webhooks appear on `payment.webhook.received` with stripe_event_id as partition key
  4. All 7 Kafka topics exist with 3 partitions and correct locked names
  5. docker-compose up -d brings all 8 services healthy with correct startup order
**Plans**: 1 plan (bootstrapped from M1 session)

Plans:

- [x] 01-01: Scaffold, Docker Compose, Stripe webhook receiver + Redis idempotency + Kafka publish + 5 unit tests

</details>

### 🚧 v1.1 Validation + State Machine (Active)

**Milestone Goal:** Consume raw webhook events, validate schema and business rules, drive the payment state machine into PostgreSQL, rate-limit by merchant, and publish clean events downstream to `payment.transaction.validated`.

#### Phase 2: Kafka Consumer + Validation + DLQ

**Goal**: Stand up the `validation-service` Kafka consumer, validate every inbound event against schema and business rules, and route every failure to the DLQ with the full locked contract.
**Depends on**: Phase 1
**Requirements**: CONSUMER-01, CONSUMER-02, VALID-01, VALID-02, VALID-03, QUAL-02
**Success Criteria**:
  1. Consumer reads from `payment.webhook.received` using consumer group `validation-service` with manual offset commit — offsets only advance after processing completes
  2. Events missing required fields or carrying wrong types are rejected before any downstream write occurs
  3. Events with unsupported event types or amount <= 0 are rejected by business-rule validation
  4. Every rejected event appears on `payment.dlq` with all six required DLQ fields: original_topic, original_offset, failure_reason (SCHEMA_INVALID), retry_count, first_failure_ts, payload verbatim
  5. Unit tests covering at least three business-rule cases (valid, schema failure, business-rule failure) pass in CI
**Plans**: 2 plans

Plans:
- [x] 02-01-PLAN.md — Validation models, schema/business-rule logic, DLQ producer, unit tests
- [x] 02-02-PLAN.md — Kafka validation consumer, Docker container, docker-compose service

#### Phase 3: State Machine + Rate Limiting + Downstream Publish

**Goal**: Persist payment state transitions to PostgreSQL, apply Redis rate limiting per merchant, write FAILED transitions for rejected or rate-limited events, and publish validated events to `payment.transaction.validated`.
**Depends on**: Phase 2
**Requirements**: SM-01, SM-02, SM-03, SM-04, RATELIMIT-01, QUAL-01
**Success Criteria**:
  1. `payment_state_log` table exists in PostgreSQL via Alembic migration and rejects any UPDATE or DELETE at the application layer
  2. A valid event produces exactly two append-only rows in `payment_state_log`: INITIATED then VALIDATED, in order, for the same transaction_id
  3. A rejected (schema/business-rule failure) or rate-limited event produces an INITIATED then FAILED row in `payment_state_log`
  4. Merchants sending more than 100 events within a 60-second bucket are blocked via `INCR rate_limit:{merchant_id}:{minute_bucket}` in Redis and their events write FAILED to the state log
  5. Validated events appear on `payment.transaction.validated` after the state transition commits — no publish occurs for failed events
  6. Integration tests covering happy path, schema failure → DLQ, rate-limit block, and state transitions all pass
**Plans**: 3 plans

Plans:
- [x] 03-01-PLAN.md — Alembic migration, SQLAlchemy models, PaymentStateMachine, MerchantRateLimiter, ValidatedEventProducer
- [ ] 03-02-PLAN.md — Wire state machine + rate limiter + producer into ValidationConsumer, update Docker config
- [ ] 03-03-PLAN.md — Integration tests (QUAL-01): state transitions, rate limiting, downstream publish

### 📋 v1.2 Spark + ML Scoring (Planned)

**Milestone Goal:** Engineer the 8 ML input features via Spark Structured Streaming and score transactions with XGBoost (p99 < 100ms).

#### Phase 4: Spark Feature Engineering

**Goal**: Spark Structured Streaming job consuming `payment.transaction.validated`, engineering all 8 ML input features, writing to Redis online feature store with GCS checkpoint.
**Depends on**: Phase 3
**Plans**: TBD

#### Phase 5: ML Risk Scoring Service

**Goal**: FastAPI ML scoring service running XGBoost inference (p99 < 100ms), with Redis fallback → manual_review=true on timeout, publishing to `payment.transaction.scored` and `payment.alert.triggered`.
**Depends on**: Phase 4
**Plans**: TBD

### 📋 v1.3 Ledger + Reconciliation (Planned)

**Milestone Goal:** Persist every settled transaction as balanced double-entry ledger entries and run nightly Airflow reconciliation against Stripe.

#### Phase 6: Financial Ledger

**Goal**: Consume `payment.ledger.entry`, write balanced double-entry ledger entries to PostgreSQL (append-only, DB trigger enforces SUM=0 per transaction_id), update state to SETTLED/FLAGGED.
**Depends on**: Phase 5
**Plans**: TBD

#### Phase 7: Reconciliation + Airflow

**Goal**: Airflow DAG reconciling internal ledger against Stripe API nightly, feeding `payment.reconciliation.queue`, surfacing discrepancies.
**Depends on**: Phase 6
**Plans**: TBD

### 📋 v2.0 Analytics + Deploy (Planned)

**Milestone Goal:** BigQuery warehouse, dbt transformation layer, Streamlit dashboard, and full GCP production deployment with CI/CD.

#### Phase 8: BigQuery + dbt

**Goal**: Stream ledger and transaction data to BigQuery, build dbt models (staging → dimensions → facts → marts), run custom assert_ledger_balanced test.
**Depends on**: Phase 7
**Plans**: TBD

#### Phase 9: Dashboard + Monitoring

**Goal**: Streamlit observability dashboard wired to BigQuery marts + Prometheus; Grafana alerts for SLA breaches.
**Depends on**: Phase 8
**Plans**: TBD

#### Phase 10: Feature Replay Engine

**Goal**: Replay historical events through the feature engineering pipeline to bootstrap the offline feature store and backfill ML training data.
**Depends on**: Phase 8
**Plans**: TBD

#### Phase 11: GCP Deploy + CI/CD

**Goal**: Cloud Run services, Cloud SQL, Cloud Memorystore, BigQuery in GCP; GitHub Actions CI/CD pipeline with staging + production environments.
**Depends on**: Phases 8-10
**Plans**: TBD

## Phase Details

### Phase 2: Kafka Consumer + Validation + DLQ

**Goal**: Stand up the `validation-service` Kafka consumer, validate every inbound event against schema and business rules, and route every failure to the DLQ with the full locked contract.
**Depends on**: Phase 1
**Requirements**: CONSUMER-01, CONSUMER-02, VALID-01, VALID-02, VALID-03, QUAL-02
**Success Criteria** (what must be TRUE):
  1. Consumer reads from `payment.webhook.received` using consumer group `validation-service` with manual offset commit — offsets only advance after processing completes
  2. Events missing required fields or carrying wrong types are rejected before any downstream write occurs
  3. Events with unsupported event types or amount <= 0 are rejected by business-rule validation
  4. Every rejected event appears on `payment.dlq` with all six required DLQ fields: original_topic, original_offset, failure_reason (SCHEMA_INVALID), retry_count, first_failure_ts, payload verbatim
  5. Unit tests covering at least three business-rule cases (valid, schema failure, business-rule failure) pass in CI
**Plans**: 2 plans

### Phase 3: State Machine + Rate Limiting + Downstream Publish

**Goal**: Persist payment state transitions to PostgreSQL, apply Redis rate limiting per merchant, write FAILED transitions for rejected or rate-limited events, and publish validated events to `payment.transaction.validated`.
**Depends on**: Phase 2
**Requirements**: SM-01, SM-02, SM-03, SM-04, RATELIMIT-01, QUAL-01
**Success Criteria** (what must be TRUE):
  1. `payment_state_log` table exists in PostgreSQL via Alembic migration and rejects any UPDATE or DELETE at the application layer
  2. A valid event produces exactly two append-only rows in `payment_state_log`: INITIATED then VALIDATED, in order, for the same transaction_id
  3. A rejected (schema/business-rule failure) or rate-limited event produces an INITIATED then FAILED row in `payment_state_log`
  4. Merchants sending more than 100 events within a 60-second bucket are blocked via `INCR rate_limit:{merchant_id}:{minute_bucket}` in Redis and their events write FAILED to the state log
  5. Validated events appear on `payment.transaction.validated` after the state transition commits — no publish occurs for failed events
  6. Integration tests covering happy path, schema failure → DLQ, rate-limit block, and state transitions all pass
**Plans**: 3 plans

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation + Ingestion | v1.0 | 1/1 | Complete | 2026-03-22 |
| 2. Kafka Consumer + Validation + DLQ | v1.1 | 2/2 | Complete   | 2026-03-23 |
| 3. State Machine + Rate Limiting + Downstream Publish | v1.1 | 1/3 | In Progress|  |
| 4. Spark Feature Engineering | v1.2 | 0/TBD | Not started | - |
| 5. ML Risk Scoring Service | v1.2 | 0/TBD | Not started | - |
| 6. Financial Ledger | v1.3 | 0/TBD | Not started | - |
| 7. Reconciliation + Airflow | v1.3 | 0/TBD | Not started | - |
| 8. BigQuery + dbt | v2.0 | 0/TBD | Not started | - |
| 9. Dashboard + Monitoring | v2.0 | 0/TBD | Not started | - |
| 10. Feature Replay Engine | v2.0 | 0/TBD | Not started | - |
| 11. GCP Deploy + CI/CD | v2.0 | 0/TBD | Not started | - |
