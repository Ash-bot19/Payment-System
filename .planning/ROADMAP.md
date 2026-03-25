# Roadmap: Payment System

## Milestones

- ✅ **v1.0 Foundation + Ingestion** — Phase 1 (shipped 2026-03-22)
- ✅ **v1.1 Validation + State Machine** — Phases 2-3 (shipped 2026-03-24)
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

<details>
<summary>✅ v1.1 Validation + State Machine (Phases 2-3) — SHIPPED 2026-03-24</summary>

**Milestone Goal:** Consume raw webhook events, validate schema and business rules, drive the payment state machine into PostgreSQL, rate-limit by merchant, and publish clean events downstream to `payment.transaction.validated`.

- [x] Phase 2: Kafka Consumer + Validation + DLQ (2/2 plans) — completed 2026-03-23
- [x] Phase 3: State Machine + Rate Limiting + Downstream Publish (3/3 plans) — completed 2026-03-24

See `.planning/milestones/v1.1-ROADMAP.md` for full phase details.

</details>

### v1.2 Spark + ML Scoring (Planned)

**Milestone Goal:** Engineer the 8 ML input features via Spark Structured Streaming and score transactions with XGBoost (p99 < 100ms).

#### Phase 4: Spark Feature Engineering

**Goal**: Spark Structured Streaming job consuming `payment.transaction.validated`, engineering all 8 ML input features, writing to Redis online feature store with GCS checkpoint.
**Depends on**: Phase 3
**Requirements**: FEAT-01, FEAT-02, FEAT-03, FEAT-04, FEAT-05, FEAT-06, FEAT-07, FEAT-08, FEAT-09, FEAT-10, FEAT-11, FEAT-12
**Plans**: 3 plans

Plans:
- [x] 04-01-PLAN.md — Pure feature functions (Welford zscore, hour/weekend/log transforms) + Redis sink foreachBatch + unit tests
- [x] 04-02-PLAN.md — Main streaming job entry point: Kafka readStream, velocity windows, column transforms, 3 writeStream queries + integration tests
- [ ] 04-03-PLAN.md — Spark Dockerfile, docker-compose service, end-to-end Kafka-to-Redis integration test

#### Phase 5: ML Risk Scoring Service

**Goal**: FastAPI ML scoring service running XGBoost inference (p99 < 100ms), with Redis fallback → manual_review=true on timeout, publishing to `payment.transaction.scored` and `payment.alert.triggered`.
**Depends on**: Phase 4
**Plans**: TBD

### v1.3 Ledger + Reconciliation (Planned)

**Milestone Goal:** Persist every settled transaction as balanced double-entry ledger entries and run nightly Airflow reconciliation against Stripe.

#### Phase 6: Financial Ledger

**Goal**: Consume `payment.ledger.entry`, write balanced double-entry ledger entries to PostgreSQL (append-only, DB trigger enforces SUM=0 per transaction_id), update state to SETTLED/FLAGGED.
**Depends on**: Phase 5
**Plans**: TBD

#### Phase 7: Reconciliation + Airflow

**Goal**: Airflow DAG reconciling internal ledger against Stripe API nightly, feeding `payment.reconciliation.queue`, surfacing discrepancies.
**Depends on**: Phase 6
**Plans**: TBD

### v2.0 Analytics + Deploy (Planned)

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

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation + Ingestion | v1.0 | 1/1 | Complete | 2026-03-22 |
| 2. Kafka Consumer + Validation + DLQ | v1.1 | 2/2 | Complete   | 2026-03-23 |
| 3. State Machine + Rate Limiting + Downstream Publish | v1.1 | 3/3 | Complete   | 2026-03-24 |
| 4. Spark Feature Engineering | v1.2 | 2/3 | In Progress|  |
| 5. ML Risk Scoring Service | v1.2 | 0/TBD | Not started | - |
| 6. Financial Ledger | v1.3 | 0/TBD | Not started | - |
| 7. Reconciliation + Airflow | v1.3 | 0/TBD | Not started | - |
| 8. BigQuery + dbt | v2.0 | 0/TBD | Not started | - |
| 9. Dashboard + Monitoring | v2.0 | 0/TBD | Not started | - |
| 10. Feature Replay Engine | v2.0 | 0/TBD | Not started | - |
| 11. GCP Deploy + CI/CD | v2.0 | 0/TBD | Not started | - |
