---
gsd_state_version: 1.0
milestone: v1.3
milestone_name: ML Risk Scoring
status: unknown
stopped_at: Completed 05-03-PLAN.md
last_updated: "2026-03-26T18:05:19.743Z"
progress:
  total_phases: 1
  completed_phases: 1
  total_plans: 3
  completed_plans: 3
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-25 after v1.2 milestone complete)

**Core value:** Every payment event is reliably ingested, deduplicated, scored for fraud risk, and recorded in an auditable double-entry ledger with no data loss.
**Current focus:** Phase 05 — ml-risk-scoring

## Current Position

Phase: 05 (ml-risk-scoring) — EXECUTING
Plan: 2 of 3

## Performance Metrics

**Velocity:**

- Total plans completed: 1
- Sessions: 1

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Foundation + Ingestion | 1 | 1 session | - |
| Phase 02-kafka-consumer-validation-dlq P01 | pre-committed | 3 tasks | 4 files |
| Phase 02-kafka-consumer-validation-dlq P02 | 4 | 2 tasks | 4 files |
| Phase 03 P01 | 186 | 2 tasks | 9 files |
| Phase 03-state-machine-rate-limiting-downstream-publish P02 | 15 | 2 tasks | 5 files |
| Phase 03-state-machine-rate-limiting-downstream-publish P03 | 2 | 2 tasks | 2 files |
| Phase 04-spark-feature-engineering P01 | 13 | 2 tasks | 8 files |
| Phase 04 P02 | 4 | 2 tasks | 4 files |
| Phase 04 P03 | 184 | 2 tasks | 4 files |
| Phase 05-ml-risk-scoring P01 | 8 | 1 tasks | 8 files |
| Phase 05 P02 | 387 | 2 tasks | 8 files |
| Phase 05-ml-risk-scoring P03 | 45 | 2 tasks | 4 files |

## Accumulated Context

### Decisions

See PROJECT.md Key Decisions table for full log.

Decisions affecting Phase 2 and Phase 3:

- **Kafka consumer group LOCKED** — must be `validation-service`; manual offset commit only (no auto-commit)
- **DLQ contract LOCKED** — failures must include: original_topic, original_offset, failure_reason (SCHEMA_INVALID | IDEMPOTENCY_COLLISION | ML_TIMEOUT | LEDGER_WRITE_FAIL | UNKNOWN), retry_count, first_failure_ts, payload verbatim
- **Redis rate limiting LOCKED** — key format `rate_limit:{merchant_id}:{minute_bucket}`, INCR → 429 if > 100/min
- **State machine LOCKED** — transitions: INITIATED → VALIDATED (valid), INITIATED → FAILED (invalid/rate-limited); table `payment_state_log` is append-only, no UPDATE/DELETE
- **Pydantic v2** — use `model_validator`, not `validator`; all schemas in `models/`
- **PostgreSQL Alembic** — always run migrations in a transaction
- [Phase 02]: validate_event raises ValidationError (not returns optional) — unambiguous failure path for DLQ routing
- [Phase 02]: Amount positivity enforced for payment_intent.succeeded only (D-12); canceled/failed events may have amount=0
- [Phase 02]: DLQProducer crash-on-exhaustion (re-raise KafkaException) preferred over silent drop — Docker restart replays message per D-17
- [Phase 02-kafka-consumer-validation-dlq]: Threaded http.server for /health endpoint — zero-dependency health check, avoids uvicorn for pure consumer service
- [Phase 02-kafka-consumer-validation-dlq]: DLQ publish before offset commit — silent drops impossible; DLQ failure triggers crash-and-restart via Docker
- [Phase 03-01]: SQLAlchemy Core insert() for PaymentStateMachine writes — explicit append-only semantics, no ORM session complexity
- [Phase 03-01]: DB-level PL/pgSQL trigger enforces append-only on payment_state_log — physically immutable audit log
- [Phase 03-01]: DATABASE_URL_SYNC (psycopg2) separate from DATABASE_URL (asyncpg) for Alembic vs FastAPI
- [Phase 03]: merchant_id added to ValidatedPaymentEvent so all downstream consumers receive merchant context without re-parsing raw Stripe payload
- [Phase 03]: Rate-limiting applied only to payment_intent.succeeded events — canceled/failed events have no revenue impact and should not be throttled
- [Phase 03]: Alembic migrations run at ValidationConsumer startup via _run_migrations() — consumer owns its schema, no separate migration job needed
- [Phase 03]: DB isolation via unique transaction_ids per test — append-only trigger blocks DELETE so UUID-keyed rows are isolated by value
- [Phase 03]: Kafka test isolation via AdminClient-created UUID-suffixed topics — avoids auto-create restriction and guarantees zero cross-test contamination
- [Phase 04-spark-feature-engineering]: Welford count<3 threshold: need 2 prior observations for reliable variance; cold start and single-point both return 0.0
- [Phase 04-spark-feature-engineering]: feature_functions.py uses pure Python types (not PySpark Columns) — runs inside foreachBatch on driver, not distributed executors
- [Phase 04-spark-feature-engineering]: foreachBatch Redis pipeline requires explicit pipe.execute() — omitting causes silent zero writes with no error
- [Phase 04]: PySpark column expressions (F.hour, F.dayofweek, F.log1p) used for streaming transforms — pure Python feature_functions.py used inside foreachBatch on driver only
- [Phase 04]: requires_java skipif guard added to JVM-dependent Spark tests — prevents ERROR on machines without Java, tests run in bitnami/spark Docker container
- [Phase 04]: bitnami/spark:3.5 as Dockerfile base with spark-submit --packages for Kafka JAR (runtime download avoids 100MB in image)
- [Phase 04]: E2E tests use static DataFrame (no live Kafka) for CI stability; validates write_features_to_redis directly against live Redis
- [Phase 04]: requires_java guard on all Spark E2E tests — skip on local dev, run inside bitnami/spark Docker container
- [Phase 05-ml-risk-scoring]: model.ubj committed to repo (D-A1) — not a Docker volume, not a registry; zero runtime dependency on training infra
- [Phase 05-ml-risk-scoring]: crash-and-exit (sys.exit(1)) for missing model at startup — distinguishes misconfiguration from runtime Redis-timeout fallback (D-A3)
- [Phase 05]: FastAPI lifespan over @app.on_event for ml_service model load — on_event deprecated in FastAPI 0.115+
- [Phase 05]: model.ubj lives at ml/models/model.ubj (not ml/model.ubj) — confirmed on filesystem during Phase 05-02 test run
- [Phase 05-ml-risk-scoring]: ML scoring pipeline containerized: scoring-consumer (8003) + ml-scoring-service (8001) via docker-compose with health checks; ML_MODEL_PATH=ml/models/model.ubj; health checks via Python urllib.request (no curl dep)

### Pending Todos

None.

### Blockers/Concerns

- Stripe test webhooks need Stripe CLI running locally to forward events (see CLAUDE.md Known Gotchas)
- PostgreSQL Alembic migrations must run in a transaction (CLAUDE.md)
- Pydantic v2: use `model_validator`, not `validator` (CLAUDE.md)
- Docker container name conflicts on re-run: always `docker rm -f` before `docker-compose up -d` if containers exist (CLAUDE.md)

## Session Continuity

Last session: 2026-03-26T18:05:19.738Z
Stopped at: Completed 05-03-PLAN.md
Resume file: None
