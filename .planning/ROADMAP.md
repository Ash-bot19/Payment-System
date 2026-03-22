# Roadmap: Payment System

## Milestones

- ✅ **v1.0 Foundation + Ingestion** — Phase 1 (shipped 2026-03-22)
- 🚧 **v1.1 Validation + Scoring** — Phases 2-4 (planned)
- 📋 **v1.2 Ledger + Reconciliation** — Phases 5-6 (planned)
- 📋 **v2.0 Analytics + Deploy** — Phases 7-10 (planned)

## Phases

<details>
<summary>✅ v1.0 Foundation + Ingestion (Phase 1) — SHIPPED 2026-03-22</summary>

### Phase 1: Foundation + Ingestion

**Goal**: Establish the full project scaffold, containerised infrastructure, and Stripe webhook ingestion pipeline into Kafka with Redis idempotency.
**Depends on**: Nothing (first phase)
**Plans**: 1 plan (bootstrapped from M1 session)

Plans:

- [x] 01-01: Scaffold, Docker Compose, Stripe webhook receiver + Redis idempotency + Kafka publish + 5 unit tests

</details>

### 🚧 v1.1 Validation + Scoring (Planned)

**Milestone Goal:** Validate inbound events against schema and business rules, run the payment state machine, engineer Spark features, and score transactions with XGBoost ML.

#### Phase 2: Validation Layer + State Machine

**Goal**: Consume `payment.webhook.received`, validate schema + business rules, drive the payment state machine (INITIATED → VALIDATED → FAILED/DLQ), publish to `payment.transaction.validated`.
**Depends on**: Phase 1
**Plans**: TBD

Plans:
- [ ] 02-01: Kafka consumer, Pydantic validation, DLQ contract
- [ ] 02-02: Payment state machine (PostgreSQL append-only state log)

#### Phase 3: Spark Feature Engineering

**Goal**: Spark Structured Streaming job consuming `payment.transaction.validated`, engineering the 8 ML input features, writing to Redis online feature store.
**Depends on**: Phase 2
**Plans**: TBD

Plans:
- [ ] 03-01: Spark job scaffold, feature engineering (velocity, z-score, merchant risk)
- [ ] 03-02: Redis online feature store writer + GCS checkpoint

#### Phase 4: ML Risk Scoring Service

**Goal**: FastAPI ML scoring service consuming scored features from Redis, running XGBoost inference (p99 < 100ms), publishing to `payment.transaction.scored` and `payment.alert.triggered`.
**Depends on**: Phase 3
**Plans**: TBD

Plans:
- [ ] 04-01: XGBoost model training + feature pipeline
- [ ] 04-02: FastAPI scoring service + Redis fallback + Kafka publish

### 📋 v1.2 Ledger + Reconciliation (Planned)

**Milestone Goal:** Persist every settled transaction as balanced double-entry ledger entries and run nightly Airflow reconciliation against Stripe.

#### Phase 5: Financial Ledger

**Goal**: Consume `payment.ledger.entry`, write balanced double-entry ledger entries to PostgreSQL (append-only, DB trigger enforces SUM=0), update state machine to SETTLED/FLAGGED.
**Depends on**: Phase 4
**Plans**: TBD

Plans:
- [ ] 05-01: Ledger schema, Alembic migrations, append-only enforcement
- [ ] 05-02: Ledger consumer, DEBIT/CREDIT pair writer, DB trigger

#### Phase 6: Reconciliation + Airflow

**Goal**: Airflow DAG reconciling internal ledger against Stripe API nightly, feeding `payment.reconciliation.queue`, surfacing discrepancies.
**Depends on**: Phase 5
**Plans**: TBD

Plans:
- [ ] 06-01: Airflow DAG scaffold, Stripe API reconciliation logic
- [ ] 06-02: Discrepancy surfacing + alerting

### 📋 v2.0 Analytics + Deploy (Planned)

**Milestone Goal:** BigQuery warehouse, dbt transformation layer, Streamlit dashboard, and full GCP production deployment with CI/CD.

#### Phase 7: BigQuery + dbt

**Goal**: Stream ledger and transaction data to BigQuery, build dbt models (staging → dimensions → facts → marts).
**Depends on**: Phase 6
**Plans**: TBD

#### Phase 8: Dashboard + Monitoring

**Goal**: Streamlit observability dashboard wired to BigQuery marts + Prometheus; Grafana alerts for SLA breaches.
**Depends on**: Phase 7
**Plans**: TBD

#### Phase 9: Feature Replay Engine

**Goal**: Replay historical events through the feature engineering pipeline to bootstrap the offline feature store and backfill ML training data.
**Depends on**: Phase 7
**Plans**: TBD

#### Phase 10: GCP Deploy + CI/CD

**Goal**: Cloud Run services, Cloud SQL, Cloud Memorystore, BigQuery in GCP; GitHub Actions CI/CD pipeline with staging + production environments.
**Depends on**: Phases 7-9
**Plans**: TBD

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation + Ingestion | v1.0 | 1/1 | Complete | 2026-03-22 |
| 2. Validation Layer + State Machine | v1.1 | 0/2 | Not started | - |
| 3. Spark Feature Engineering | v1.1 | 0/2 | Not started | - |
| 4. ML Risk Scoring Service | v1.1 | 0/2 | Not started | - |
| 5. Financial Ledger | v1.2 | 0/2 | Not started | - |
| 6. Reconciliation + Airflow | v1.2 | 0/2 | Not started | - |
| 7. BigQuery + dbt | v2.0 | 0/TBD | Not started | - |
| 8. Dashboard + Monitoring | v2.0 | 0/TBD | Not started | - |
| 9. Feature Replay Engine | v2.0 | 0/TBD | Not started | - |
| 10. GCP Deploy + CI/CD | v2.0 | 0/TBD | Not started | - |
