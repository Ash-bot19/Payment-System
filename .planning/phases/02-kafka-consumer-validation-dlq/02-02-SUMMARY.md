---
phase: 02-kafka-consumer-validation-dlq
plan: 02
subsystem: payments

tags: [kafka, consumer, validation, dlq, docker, health-endpoint]

# Dependency graph
requires:
  - phase: 02-kafka-consumer-validation-dlq
    plan: 01
    provides: validate_event(), ValidationError, DLQProducer, ValidatedPaymentEvent, DLQMessage

provides:
  - ValidationConsumer class — synchronous Kafka poll loop, manual offset commit, DLQ routing
  - /health endpoint on port 8002 (threaded http.server) — Prometheus scrapable
  - __main__.py entrypoint — python -m kafka.consumers starts the consumer
  - Dockerfile.validation — Docker image for validation-consumer service
  - docker-compose validation-consumer service — depends on kafka+redis healthy

affects:
  - 03-state-machine (consumes validated events, publishes to payment.transaction.validated)
  - monitoring/prometheus (scrapes /health on port 8002)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Synchronous Kafka consumer (no asyncio) with manual store_offsets + commit after each message"
    - "Threaded http.server.HTTPServer in daemon thread — zero-dependency health endpoint"
    - "DLQ write before offset commit (D-15) — at-least-once delivery guarantee for failures"
    - "Crash on unhandled exceptions (logger.critical + re-raise) — Docker restart replays message"

key-files:
  created:
    - payment-backend/kafka/consumers/validation_consumer.py
    - payment-backend/kafka/consumers/__main__.py
    - payment-backend/infra/Dockerfile.validation
  modified:
    - payment-backend/infra/docker-compose.yml

key-decisions:
  - "Threaded http.server over FastAPI for health endpoint — zero external dependencies, avoids uvicorn process overhead for a pure consumer service (D-03)"
  - "store_offsets(msg) + commit() after each message — finer granularity than batch commit, simpler reasoning about at-least-once semantics"
  - "DLQ written before offset commit — ensures no silent drops on DLQ publish failure"
  - "restart: unless-stopped in docker-compose — crash-loop recovery without manual intervention"

# Metrics
duration: 4 minutes
completed: 2026-03-23
---

# Phase 02 Plan 02: Kafka Consumer + Docker Service Summary

**Synchronous ValidationConsumer poll loop wiring validate_event() and DLQProducer into a running Kafka service with manual offset commit, threaded /health on port 8002, and a Docker Compose service definition**

## Performance

- **Duration:** ~4 minutes
- **Started:** 2026-03-23T12:46:34Z
- **Completed:** 2026-03-23
- **Tasks:** 2
- **Files modified:** 4 (2 created new Python, 1 created Dockerfile, 1 modified docker-compose)

## Accomplishments

- ValidationConsumer reads from `payment.webhook.received` with consumer group `validation-service`
- `enable.auto.commit: False` enforced — `store_offsets(msg)` + `commit()` called after every message
- Valid events: `validation_passed_awaiting_downstream` logged with `phase=2` per D-16
- Invalid events: DLQMessage built with all 6 locked contract fields, published via DLQProducer before offset commit per D-15
- Threaded `_HealthHandler` on port 8002 returns `{"status": "ok"}` for Prometheus scraping per D-03
- Signal handlers (SIGTERM/SIGINT) trigger graceful shutdown with consumer + producer close
- `__main__.py` reads `KAFKA_BOOTSTRAP_SERVERS` env var, starts consumer
- `Dockerfile.validation` mirrors `Dockerfile.webhook`, `CMD ["python", "-m", "kafka.consumers"]`, `EXPOSE 8002`
- `docker-compose.yml` validation-consumer service: depends on kafka+redis healthy, healthcheck hits `/health`, `restart: unless-stopped`

## Task Commits

Each task was committed atomically:

1. **Task 1: ValidationConsumer + __main__.py** - `546c033` (feat)
2. **Task 2: Dockerfile.validation + docker-compose** - `454b078` (feat)

## Files Created/Modified

- `payment-backend/kafka/consumers/validation_consumer.py` — ValidationConsumer class, _HealthHandler, poll loop
- `payment-backend/kafka/consumers/__main__.py` — entrypoint, reads KAFKA_BOOTSTRAP_SERVERS
- `payment-backend/infra/Dockerfile.validation` — python:3.11-slim, EXPOSE 8002, CMD python -m kafka.consumers
- `payment-backend/infra/docker-compose.yml` — validation-consumer service block added (existing services unchanged)

## Decisions Made

- Threaded `http.server.HTTPServer` for /health rather than FastAPI — avoids uvicorn process for a pure consumer; satisfies D-03 with zero added dependencies
- `store_offsets(msg)` + `commit()` per-message rather than batch — simpler at-least-once reasoning at low throughput; easy to batch later if needed
- DLQ publish before offset commit — silent drop is impossible; DLQ failure triggers crash-and-restart

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None. To start the full stack including validation-consumer:

```
docker-compose -f payment-backend/infra/docker-compose.yml up -d
```

## Known Stubs

None — validation consumer logs valid events as `validation_passed_awaiting_downstream` with `phase=2`. Publishing to `payment.transaction.validated` is intentionally deferred to Phase 3 (state machine). This is documented per D-16 and is not a stub — the consumer is functionally complete for Phase 2.

## Next Phase Readiness

- Phase 3 (state machine) takes `ValidatedPaymentEvent` as input and publishes to `payment.transaction.validated`
- ValidationConsumer is ready to be extended in Phase 3 to publish validated events downstream
- No blockers

---
*Phase: 02-kafka-consumer-validation-dlq*
*Completed: 2026-03-23*
