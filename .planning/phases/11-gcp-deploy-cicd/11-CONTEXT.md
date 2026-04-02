# Phase 11: GCP Deploy + CI/CD — Context

**Gathered:** 2026-04-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Deploy the full payment system to GCP for a one-run Loom recording demo, targeting ~₹15–20 total credit spend. The strategy prioritises cost efficiency: stateful services (Kafka, Spark, Zookeeper) run on a single Compute Engine VM via Docker Compose (identical to local), managed services (Cloud SQL, Memorystore) are minimum-spec, and the VM + managed services are torn down the same day as the recording. Cloud Run, BigQuery, GCS, and GitHub Actions pipeline remain permanently at zero/near-zero ongoing cost.

This phase ends with: all services live on GCP, a recorded Loom walkthrough of the full pipeline, and the VM + paid managed services deleted.
</domain>

<decisions>
## Implementation Decisions

### GCP Project
- **D-01:** Project ID = `project-2f9d2775-493e-4e59-9b8`. Already created and authenticated.
- **D-02:** Region = `asia-south1` (Mumbai). All services deployed here.
- **D-03:** Billing account already linked and active. Budget alert at ₹20,000 to be created. Tax info (PAN) to be added in billing console.
- **D-04:** APIs already enabled: Cloud Run, Cloud SQL Admin, Memorystore, Compute Engine, BigQuery, Artifact Registry, Cloud Scheduler, Secret Manager.

### Artifact Registry + Container Builds
- **D-05:** Create one Artifact Registry repository in `asia-south1`.
- **D-06:** Build and push Docker images for all 7 services: `webhook-service`, `validation-consumer`, `spark-feature-engine`, `scoring-consumer`, `ml-scoring-service`, `ledger-consumer`, `streamlit-dashboard`.
- **D-07:** Image tag strategy: use `latest` for Loom demo simplicity. GitHub Actions will use commit SHA tags in CI/CD.

### Compute Engine VM (Kafka + Spark)
- **D-08:** Machine type: `e2-standard-2` (2 vCPU, 8GB RAM) — minimum viable for Kafka + Zookeeper + Spark JVMs.
- **D-09:** Install Docker + Docker Compose on VM. Copy the Kafka + Zookeeper + Spark subset of `docker-compose.yml` — no code changes, identical config to local.
- **D-10:** VM is EPHEMERAL — delete same day as Loom recording to stop billing.
- **D-11:** Kafka advertised listener must use the VM's internal IP so Cloud Run services can reach it. External IP for SSH only.

### Managed Services
- **D-12:** Cloud SQL: `db-f1-micro`, PostgreSQL 15. Private IP only (no public IP). Same credentials as local (`payment`/`payment`/`payment_db`).
- **D-13:** Cloud Memorystore: 1 GB Redis instance, `BASIC` tier. No auth for MVP simplicity (VPC-internal access only).
- **D-14:** GCS bucket: single bucket for Spark checkpoints (replaces `spark_checkpoints` Docker volume). Bucket name stored in `GCS_BUCKET_NAME` env var.
- **D-15:** Update `.env` (and Secret Manager) with real host/port values for Cloud SQL, Memorystore, and GCS bucket after provisioning.
- **D-16:** Cloud SQL and Memorystore are EPHEMERAL — delete same day as Loom recording.

### Cloud Run Services
- **D-17:** Deploy as separate Cloud Run services: `webhook-service` (port 8000), `ml-scoring-service` (port 8001), `streamlit-dashboard` (port 8501).
- **D-18:** Remaining consumers (`validation-consumer`, `scoring-consumer`, `ledger-consumer`) run on the Compute Engine VM alongside Kafka — they need direct Kafka access and don't need public endpoints.
- **D-19:** All secrets (DB passwords, Redis host, Stripe secret, API keys) stored in Secret Manager. Cloud Run services pull secrets at runtime — no `.env` files in production.
- **D-20:** Cloud Run services connect to Cloud SQL via Cloud SQL Auth Proxy sidecar or direct private IP within VPC.
- **D-21:** Minimum instances: 0 (scale to zero when idle). Max instances: 1 for demo.

### BigQuery + Airflow
- **D-22:** Fill in `export_to_bigquery` stub in `airflow/dags/nightly_reconciliation.py` with a real BigQuery client call (using `google-cloud-bigquery`).
- **D-23:** Deploy Airflow DAGs as Cloud Run Jobs (not always-on Airflow server). Each DAG = one Cloud Run Job.
- **D-24:** Cloud Scheduler triggers each Cloud Run Job on its cron schedule (nightly for reconciliation DAG).
- **D-25:** BigQuery dataset name stored in `BIGQUERY_DATASET` env var. Table: `historical_features` for feature store data, `reconciliation_discrepancies` for Airflow output.

### GitHub Actions CI/CD
- **D-26:** Pipeline trigger: push to `main` branch.
- **D-27:** Pipeline steps: Build + test → Push image to Artifact Registry → Deploy to staging (auto) → Deploy to production (manual approval gate).
- **D-28:** Staging environment: auto-deploy on every push to main. Production: requires manual approval in GitHub Actions UI.
- **D-29:** GitHub Actions secrets: `GCP_PROJECT_ID`, `GCP_SA_KEY` (service account JSON), `AR_REPO` (Artifact Registry repo URL), `STRIPE_WEBHOOK_SECRET`, `DATABASE_URL`.
- **D-30:** Service account for GitHub Actions needs roles: `roles/run.admin`, `roles/artifactregistry.writer`, `roles/storage.admin`, `roles/iam.serviceAccountUser`.

### Loom Recording Plan
- **D-31:** Record in one session — all 9 steps in order:
  1. Send 50–100 synthetic webhook events
  2. Show Kafka topics receiving messages
  3. Confirm Spark features computed
  4. Check Redis online feature store populated
  5. Verify PostgreSQL ledger entries written
  6. Query BigQuery — show exported data
  7. Show Streamlit dashboard live
  8. Show Grafana metrics (Prometheus scraping Cloud Run)
  9. Show GitHub Actions pipeline green
- **D-32:** Synthetic event script: extend `scripts/seed_demo_data.py` or write a separate `scripts/send_webhook_events.py` that POSTs signed Stripe-format events to the deployed webhook-service URL.

### Teardown (same day as recording)
- **D-33:** DELETE after recording: Compute Engine VM, Cloud SQL instance, Cloud Memorystore instance.
- **D-34:** KEEP permanently (always-free or zero idle cost): Cloud Run services, BigQuery dataset, GCS bucket, Artifact Registry, GitHub Actions pipeline, Secret Manager secrets.

### Prerequisites Status
- **P-01:** GCP Project ID — DONE (`project-2f9d2775-493e-4e59-9b8`)
- **P-02:** Billing account — DONE (linked and active)
- **P-03:** APIs enabled — DONE (all 9 APIs enabled 2026-04-02)
- **P-04:** Budget alert at ₹20,000 — TODO (Step 1)
- **P-05:** Tax info (PAN) in billing — TODO (Step 1)
- **P-06:** Full stack pushed to GitHub — TODO (verify before Step 7)
- **P-07:** All dbt tests passing locally — DONE

</decisions>

<specifics>
## Specific Ideas

- "One-run Loom strategy" — the whole point is a single recording session that demonstrates the full pipeline end-to-end.
- VM teardown on the same day is non-negotiable for cost control. Cloud Run + BigQuery are always-free after the demo.
- Kafka on a VM (not managed) is intentional — MSK/Confluent Cloud would blow the ₹20 budget in hours.
- Consumer services run on the VM (not Cloud Run) because they need persistent Kafka connections — Cloud Run's request/response model is wrong for long-running consumers.
- Secret Manager over `.env` in prod — this is the GCP-idiomatic pattern and a portfolio signal.
- Cloud Run Jobs for Airflow is cleaner than running an always-on Airflow webserver (and much cheaper).
</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

- `CLAUDE.md` — full stack definition, folder structure, all locked contracts
- `payment-backend/infra/docker-compose.yml` — source of truth for service config; VM deployment copies the Kafka/Spark subset
- `payment-backend/services/` — FastAPI services to be containerised for Cloud Run
- `payment-backend/airflow/dags/nightly_reconciliation.py` — `export_to_bigquery` stub to fill in (D-22)
- `payment-backend/.env.example` — placeholders to fill: `GCP_PROJECT_ID`, `BIGQUERY_DATASET`, `CLOUD_SQL_HOST`, `REDIS_HOST`, `GCS_BUCKET_NAME`
- `payment-backend/scripts/seed_demo_data.py` — pattern for synthetic event generation (D-32)
</canonical_refs>

---

*Phase: 11-gcp-deploy-cicd*
*Context gathered: 2026-04-02*
