# Roadmap: Payment System

## Milestones

- ✅ **v1.0 Foundation + Ingestion** — Phase 1 (shipped 2026-03-22)
- ✅ **v1.1 Validation + State Machine** — Phases 2-3 (shipped 2026-03-24)
- ✅ **v1.2 Spark Feature Engineering** — Phase 4 (shipped 2026-03-25)
- ✅ **v1.3 ML Risk Scoring** — Phase 5 (shipped 2026-03-26)
- 📋 **v1.4 Ledger + Reconciliation** — Phases 6-7 (planned)
- 📋 **v2.0 Analytics + Deploy** — Phases 8-11 (planned)

## Phases

<details>
<summary>✅ v1.0 Foundation + Ingestion (Phase 1) — SHIPPED 2026-03-22</summary>

- [x] Phase 1: Foundation + Ingestion (1/1 plan) — completed 2026-03-22

See `.planning/milestones/v1.0-ROADMAP.md` for full phase details.

</details>

<details>
<summary>✅ v1.1 Validation + State Machine (Phases 2-3) — SHIPPED 2026-03-24</summary>

- [x] Phase 2: Kafka Consumer + Validation + DLQ (2/2 plans) — completed 2026-03-23
- [x] Phase 3: State Machine + Rate Limiting + Downstream Publish (3/3 plans) — completed 2026-03-24

See `.planning/milestones/v1.1-ROADMAP.md` for full phase details.

</details>

<details>
<summary>✅ v1.2 Spark Feature Engineering (Phase 4) — SHIPPED 2026-03-25</summary>

**Milestone Goal:** Engineer all 8 ML input features via Spark Structured Streaming consuming `payment.transaction.validated`, writing to Redis online feature store.

- [x] Phase 4: Spark Feature Engineering (3/3 plans) — completed 2026-03-25

See `.planning/milestones/v1.2-ROADMAP.md` for full phase details.

</details>

<details>
<summary>✅ v1.3 ML Risk Scoring (Phase 5) — SHIPPED 2026-03-26</summary>

- [x] Phase 5: ML Risk Scoring Service (3/3 plans) — completed 2026-03-26

See `.planning/milestones/v1.3-ROADMAP.md` for full phase details.

</details>

### v1.4 Ledger + Reconciliation (Planned)

**Milestone Goal:** Persist every settled transaction as balanced double-entry ledger entries and run nightly Airflow reconciliation against Stripe.

#### Phase 6: Financial Ledger

**Goal**: Consume `payment.ledger.entry`, write balanced double-entry ledger entries to PostgreSQL (append-only, DB trigger enforces SUM=0 per transaction_id), update state to SETTLED/FLAGGED.
**Depends on**: Phase 5
**Plans**: 2 plans

Plans:
- [x] 06-01-PLAN.md — Models, migrations, LedgerEntryProducer, ManualReviewRepository, scoring consumer wiring
- [x] 06-02-PLAN.md — Ledger consumer, Docker containerization, E2E tests

#### Phase 7: Reconciliation + Airflow

**Goal**: Airflow DAG reconciling internal ledger against Stripe API nightly, feeding `payment.reconciliation.queue`, surfacing discrepancies.
**Depends on**: Phase 6
**Plans**: 3 plans

Plans:
- [x] 07-01-PLAN.md — ReconciliationMessage Pydantic model + ReconciliationProducer + unit tests
- [x] 07-02-PLAN.md — nightly_reconciliation Airflow DAG (detect_duplicates, fetch_stripe_window, compare_and_publish) + unit tests
- [x] 07-03-PLAN.md — Airflow in docker-compose, .env.example updates, integration + E2E tests

### v2.0 Analytics + Deploy (Planned)

**Milestone Goal:** BigQuery warehouse, dbt transformation layer, Streamlit dashboard, and full GCP production deployment with CI/CD.

#### Phase 8: BigQuery + dbt

**Goal**: Build dbt transformation layer (staging → dimensions → facts → marts) against PostgreSQL dev adapter, persist Airflow reconciliation discrepancies, run assert_ledger_balanced custom test. BigQuery export is a Phase 11 placeholder.
**Depends on**: Phase 7
**Plans**: 3 plans

Plans:
- [ ] 08-01-PLAN.md — Migration 004 (reconciliation_discrepancies) + ReconciliationDiscrepancy ORM + persist_discrepancies DAG task + updated unit tests
- [ ] 08-02-PLAN.md — Full dbt project scaffold: 10 models (staging/dimensions/facts/marts) + assert_ledger_balanced singular test + imbalanced_transaction seed
- [ ] 08-03-PLAN.md — export_to_bigquery placeholder + .env.example GCP vars + integration tests (persist_discrepancies live) + E2E tests (dbt run + dbt test)

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
| 2. Kafka Consumer + Validation + DLQ | v1.1 | 2/2 | Complete | 2026-03-23 |
| 3. State Machine + Rate Limiting + Downstream Publish | v1.1 | 3/3 | Complete | 2026-03-24 |
| 4. Spark Feature Engineering | v1.2 | 3/3 | Complete | 2026-03-25 |
| 5. ML Risk Scoring Service | v1.3 | 3/3 | Complete   | 2026-03-26 |
| 6. Financial Ledger | v1.4 | 2/2 | Complete | 2026-03-27 |
| 7. Reconciliation + Airflow | v1.4 | 3/3 | Complete   | 2026-03-27 |
| 8. BigQuery + dbt | v2.0 | 0/3 | Not started | - |
| 9. Dashboard + Monitoring | v2.0 | 0/TBD | Not started | - |
| 10. Feature Replay Engine | v2.0 | 0/TBD | Not started | - |
| 11. GCP Deploy + CI/CD | v2.0 | 0/TBD | Not started | - |
