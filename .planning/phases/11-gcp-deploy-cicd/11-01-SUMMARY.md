---
phase: 11-gcp-deploy-cicd
plan: "01"
subsystem: infra
tags: [gcp, cloud-sql, memorystore, redis, artifact-registry, secret-manager, gcs, kafka, spark, docker-compose]

# Dependency graph
requires:
  - phase: 10-feature-replay-engine
    provides: completed local stack — all services containerized, ready for GCP deploy
provides:
  - GCP Artifact Registry repo: asia-south1-docker.pkg.dev/project-2f9d2775-493e-4e59-9b8/payment-system
  - Cloud SQL: payment-db (POSTGRES_15, db-f1-micro, private IP 172.31.0.3)
  - Cloud Memorystore: payment-redis (1GB BASIC, host 10.101.168.91)
  - GCS bucket: payment-spark-checkpoints-1775139408 in asia-south1
  - Secret Manager: 7 secrets with production values
  - vm-docker-compose.yml: VM subset ready for SCP and docker-compose up
  - VPC private service peering on default network
affects: [11-02-vm-setup, 11-03-cloud-run, 11-04-bigquery-airflow, 11-05-cicd, 11-06-loom-teardown]

# Tech tracking
tech-stack:
  added: [gcloud CLI, Cloud SQL Postgres 15, Cloud Memorystore Redis 1GB, Artifact Registry, Secret Manager, GCS, billingbudgets API, servicenetworking API]
  patterns:
    - VM-only docker-compose subset pattern (no Cloud Run services, no managed service containers)
    - Secret Manager over .env for production secrets
    - KAFKA_ADVERTISED_LISTENERS with VM_INTERNAL_IP env var for Cloud Run connectivity
    - GCS-backed Spark checkpoints replacing local Docker volume

key-files:
  created:
    - payment-backend/infra/vm-docker-compose.yml
  modified:
    - payment-backend/.env.example

key-decisions:
  - "VPC private peering created on default network — required for Cloud SQL private IP; used google-managed-services-default /16 range"
  - "billingbudgets.googleapis.com and servicenetworking.googleapis.com APIs enabled (not in original API list)"
  - "vm-docker-compose.yml uses image: references to Artifact Registry (built in Plan 11-02) — no build: directives, no env_file"
  - "GCS bucket name: payment-spark-checkpoints-1775139408 (timestamp suffix for uniqueness)"
  - "Cloud SQL private IP: 172.31.0.3 — consumers access via VPC internal network only"
  - "Memorystore Redis host: 10.101.168.91 — VPC-internal access, no auth (D-13)"

patterns-established:
  - "Pattern: VM compose uses \${VM_INTERNAL_IP:-localhost} for Kafka advertised listener — set before docker-compose up on VM"
  - "Pattern: All secrets pulled from shell env vars on VM (not .env file) — matches Secret Manager idiomatic approach"

requirements-completed: []

# Metrics
duration: 24min
completed: 2026-04-02
---

# Phase 11 Plan 01: GCP Infrastructure Provisioning Summary

**Artifact Registry, Cloud SQL (private IP), Memorystore, GCS bucket, 7 Secret Manager secrets, and VM docker-compose subset provisioned in asia-south1 — full foundation for Plans 02-06**

## Performance

- **Duration:** 24 min
- **Started:** 2026-04-02T13:56:14Z
- **Completed:** 2026-04-02T14:20:40Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- All GCP managed infrastructure provisioned: Artifact Registry repo, Cloud SQL POSTGRES_15 (RUNNABLE, private IP 172.31.0.3), Memorystore Redis 1GB BASIC (READY, host 10.101.168.91), GCS bucket in asia-south1
- All 7 production secrets stored in Secret Manager: DATABASE_URL, DATABASE_URL_SYNC, REDIS_URL, REDIS_HOST, GCS_BUCKET_NAME, STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET
- `vm-docker-compose.yml` created: VM subset with Kafka, Zookeeper, Spark + 3 consumers using Artifact Registry image references and managed service env var placeholders

## Task Commits

Each task was committed atomically:

1. **Task 1: GCP Infrastructure (Artifact Registry, Cloud SQL, Memorystore, GCS)** - `d7b3dfb` (feat)
2. **Task 2: Secret Manager + vm-docker-compose.yml** - `cdd1307` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `payment-backend/infra/vm-docker-compose.yml` - VM-only Docker Compose subset: Kafka, Zookeeper, kafka-init, Spark, validation-consumer, scoring-consumer, ledger-consumer
- `payment-backend/.env.example` - Updated with Phase 11 GCP placeholders (CLOUD_SQL_HOST, REDIS_HOST, GCS_BUCKET_NAME, etc.) and actual IPs in comments

## Decisions Made

- **VPC peering required:** Cloud SQL private IP requires Service Networking API + VPC peering range. Created `google-managed-services-default` /16 range and peered `servicenetworking.googleapis.com` on the default network before Cloud SQL creation could succeed.
- **Two extra APIs enabled:** `billingbudgets.googleapis.com` (for billing alert) and `servicenetworking.googleapis.com` (for Cloud SQL private IP) were not in the D-04 API list — enabled as Rule 3 auto-fixes.
- **vm-docker-compose.yml has no `volumes` section for spark_checkpoints:** GCS replaces the Docker volume per D-14; `SPARK_CHECKPOINT_DIR=gs://${GCS_BUCKET_NAME}/checkpoints` is the replacement.
- **Stripe secrets stored with placeholder values:** Local `.env` contains `whsec_test_...` and `sk_test_...` (not real keys). Secrets stored as-is per plan — real values to be updated before Loom recording in Plan 11-06.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Enabled billingbudgets.googleapis.com API**
- **Found during:** Task 1 (billing budget creation)
- **Issue:** `gcloud billing budgets create` failed: API not enabled on project
- **Fix:** `gcloud services enable billingbudgets.googleapis.com` before retrying
- **Files modified:** None (GCP-side API enable)
- **Verification:** Budget `bc1e5575-7a0f-4aa3-b024-c510fa53f1ee` created successfully
- **Committed in:** d7b3dfb (Task 1 commit)

**2. [Rule 3 - Blocking] Enabled Service Networking API + created VPC peering for Cloud SQL private IP**
- **Found during:** Task 1 (Cloud SQL creation)
- **Issue:** `gcloud sql instances create --no-assign-ip` failed with SERVICE_NETWORKING_NOT_ENABLED
- **Fix:** Enabled `servicenetworking.googleapis.com`, created VPC peering IP range `google-managed-services-default` (/16), peered with `servicenetworking.googleapis.com` on default network
- **Files modified:** None (GCP-side network config)
- **Verification:** Cloud SQL created successfully with private IP 172.31.0.3
- **Committed in:** d7b3dfb (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 3 - blocking)
**Impact on plan:** Both API/network setups are prerequisites for private-IP Cloud SQL. No scope creep — required infrastructure for D-12.

## Issues Encountered

- Cloud SQL private IP requires Service Networking peering which is a one-time VPC setup step not mentioned in the plan — handled automatically. The VPC peering persists (zero cost) and is required for both Cloud SQL and Memorystore to be accessible from the VM and Cloud Run services.

## User Setup Required

None — all infrastructure was provisioned automatically. Before Loom recording (Plan 11-06), update the Stripe secrets in Secret Manager with real test-mode keys:
```
echo -n "whsec_real_key" | gcloud secrets versions add STRIPE_WEBHOOK_SECRET --data-file=- --project=project-2f9d2775-493e-4e59-9b8
echo -n "sk_test_real_key" | gcloud secrets versions add STRIPE_API_KEY --data-file=- --project=project-2f9d2775-493e-4e59-9b8
```

## Next Phase Readiness

- Plan 11-02 (VM setup + Docker build + push): Ready — Artifact Registry exists, vm-docker-compose.yml ready for SCP
- Plan 11-03 (Cloud Run deploy): Ready — Cloud SQL and Memorystore exist with known IPs, secrets in Secret Manager
- All 7 secrets ready for `--set-secrets` flags in `gcloud run deploy`
- VPC peering established — Cloud Run services with VPC connector can reach Cloud SQL and Memorystore

---
*Phase: 11-gcp-deploy-cicd*
*Completed: 2026-04-02*
