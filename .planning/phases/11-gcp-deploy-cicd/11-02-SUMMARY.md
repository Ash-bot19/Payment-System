---
phase: 11-gcp-deploy-cicd
plan: "02"
subsystem: infra
tags: [gcp, artifact-registry, compute-engine, kafka, zookeeper, spark, docker, vm, kafka-topics]

# Dependency graph
requires:
  - phase: 11-01
    provides: Artifact Registry repo, vm-docker-compose.yml, Cloud SQL private IP (172.31.0.3), Memorystore Redis host (10.101.168.91), GCS bucket name
provides:
  - 7 Docker images in Artifact Registry: webhook-service, validation-consumer, scoring-consumer, ml-scoring-service, ledger-consumer, spark-feature-engine, streamlit-dashboard
  - Compute Engine VM (e2-standard-2, asia-south1-a): kafka-vm, internal IP 10.160.0.2
  - Running Kafka stack on VM: Zookeeper, Kafka, Spark feature engine, validation-consumer, scoring-consumer, ledger-consumer
  - All 7 Kafka topics created: payment.webhook.received, payment.transaction.validated, payment.transaction.scored, payment.ledger.entry, payment.reconciliation.queue, payment.alert.triggered, payment.dlq
  - cloudbuild.yaml for automated image builds in CI
  - Firewall rule: allow-kafka-internal (TCP 9092/29092, source 10.0.0.0/8)
affects: [11-03-cloud-run, 11-04-bigquery-airflow, 11-05-cicd, 11-06-loom-teardown]

# Tech tracking
tech-stack:
  added: [Cloud Build (cloudbuild.yaml), Compute Engine (e2-standard-2), gcloud compute scp, gcloud compute ssh]
  patterns:
    - Cloud Build yaml pattern for multi-image builds (7 images, 7 steps)
    - VM bootstrap via startup-script (Docker install, systemctl enable)
    - gcloud compute scp for docker-compose file delivery to VM
    - docker compose up -d with --env-file for GCP environment injection on VM

key-files:
  created:
    - payment-backend/infra/cloudbuild.yaml
  modified:
    - payment-backend/infra/vm-docker-compose.yml (deployed and running on VM)

key-decisions:
  - "cloudbuild.yaml committed for CI/CD pipeline use (Plan 11-05) — also serves as build reference"
  - "VM internal IP 10.160.0.2 — Plan 03 uses this as KAFKA_BOOTSTRAP_SERVERS value"
  - "Firewall rule allow-kafka-internal allows TCP 9092/29092 from 10.0.0.0/8 — VPC-internal only, no public exposure"
  - "docker compose up with .env file on VM sets VM_INTERNAL_IP, CLOUD_SQL_HOST, REDIS_HOST, GCS_BUCKET_NAME at container launch time"

patterns-established:
  - "Pattern: VM-resident Kafka stack uses Artifact Registry images, never local builds — consistent with Cloud Run services"
  - "Pattern: .env file on VM injects runtime IPs — VM_INTERNAL_IP sourced from GCE metadata server at startup"

requirements-completed: []

# Metrics
duration: ~60min (including human UAT verification)
completed: 2026-04-02
---

# Phase 11 Plan 02: VM Setup + Docker Build/Push Summary

**7 Docker images built and pushed to Artifact Registry; Compute Engine VM (kafka-vm, 10.160.0.2) running full Kafka stack with all 7 topics — Cloud Run services in Plan 03 can connect via KAFKA_BOOTSTRAP_SERVERS=10.160.0.2:9092**

## Performance

- **Duration:** ~60 min (including human UAT verification)
- **Started:** 2026-04-02T14:22:07Z
- **Completed:** 2026-04-02
- **Tasks:** 2 auto + 1 checkpoint (human-verify)
- **Files modified:** 2

## Accomplishments

- `cloudbuild.yaml` created with 7 build steps covering all Docker images — committed to repo for CI/CD reuse in Plan 11-05
- All 7 images pushed to `asia-south1-docker.pkg.dev/project-2f9d2775-493e-4e59-9b8/payment-system` and confirmed in Artifact Registry
- Compute Engine VM (e2-standard-2, asia-south1-a) provisioned with Docker + Docker Compose; vm-docker-compose.yml SCP'd and `docker compose up -d` executed
- All 6 VM services healthy (zookeeper, kafka, spark-feature-engine, validation-consumer, scoring-consumer, ledger-consumer) with all 7 Kafka topics created
- VM internal IP 10.160.0.2 recorded — required for Plan 03 KAFKA_BOOTSTRAP_SERVERS

## Task Commits

Each task was committed atomically:

1. **Task 1: Build and push all 7 Docker images to Artifact Registry** - `8a11b1b` (feat — cloudbuild.yaml)
2. **Task 2: Provision Compute Engine VM, install Docker Compose, deploy Kafka stack** - GCP-only (no repo changes)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `payment-backend/infra/cloudbuild.yaml` - Cloud Build config for all 7 images; 7 build steps using payment-backend/ as context with per-Dockerfile -f flags
- `payment-backend/infra/vm-docker-compose.yml` - Deployed and running on VM (no repo changes; GCP-side deployment)

## Decisions Made

- **cloudbuild.yaml in repo:** Committed alongside vm-docker-compose.yml so Plan 11-05 (CI/CD) can reference it for automated triggers without rewriting build logic.
- **VM internal IP 10.160.0.2:** This must be set as `KAFKA_BOOTSTRAP_SERVERS=10.160.0.2:9092` in Plan 03 Cloud Run service deployments.
- **Firewall rule scope:** TCP 9092/29092 allowed only from 10.0.0.0/8 (VPC internal) — Kafka port not exposed to public internet.

## Deviations from Plan

None - plan executed exactly as written. VM provisioned, images built and pushed, Kafka stack started, all 7 topics confirmed by human UAT.

## Issues Encountered

None — VM startup script installed Docker and Docker Compose cleanly. All 6 services came up healthy on first `docker compose up -d`.

## User Setup Required

None — all provisioning completed automatically. Artifact Registry images are now available for Cloud Run deploy in Plan 03.

**Critical value for Plan 03:** `KAFKA_BOOTSTRAP_SERVERS=10.160.0.2:9092`

## Next Phase Readiness

- Plan 11-03 (Cloud Run deploy): Ready — VM Kafka is running at 10.160.0.2:9092, all images exist in Artifact Registry, Cloud SQL (172.31.0.3) and Memorystore (10.101.168.91) are accessible via VPC
- Plan 11-04 (BigQuery + Airflow): Ready — no VM dependencies
- Plan 11-05 (CI/CD): Ready — cloudbuild.yaml committed, Artifact Registry repo exists

---
*Phase: 11-gcp-deploy-cicd*
*Completed: 2026-04-02*
