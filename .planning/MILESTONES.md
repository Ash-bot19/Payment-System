# Milestones

## v1.1 Validation + State Machine (Shipped: 2026-03-24)

**Phases completed:** 2 phases, 5 plans, 11 tasks

**Key accomplishments:**

- Pydantic v2 validation models (ValidatedPaymentEvent, DLQMessage), pure validate_event function with schema + business-rule checks, DLQProducer with 3-retry exponential backoff, and 11 passing unit tests
- Synchronous ValidationConsumer poll loop wiring validate_event() and DLQProducer into a running Kafka service with manual offset commit, threaded /health on port 8002, and a Docker Compose service definition
- Alembic migration creating append-only payment_state_log with PL/pgSQL UPDATE/DELETE triggers, plus injectable PaymentStateMachine, MerchantRateLimiter (Redis INCR), and ValidatedEventProducer (mirrors DLQProducer) — all standalone testable classes
- ValidationConsumer fully wired following D-03 processing order — rate limiting, state machine writes, and payment.transaction.validated publish with merchant_id propagated to all downstream consumers
- 8 integration tests covering state machine transitions, append-only enforcement, Redis rate limiting, and Kafka downstream publish against real docker-compose infrastructure

---

## v1.0 — Foundation + Ingestion

**Shipped:** 2026-03-22
**Phases:** 1 | **Plans:** 1

### Delivered

End-to-end Stripe webhook ingestion pipeline: HMAC verification → Redis idempotency → Kafka publish, with full containerised local dev stack (Kafka, Redis, PostgreSQL, Prometheus, Grafana) and all 7 Kafka topics provisioned.

### Key Accomplishments

1. Docker Compose stack with 8 services, health checks, and correct startup ordering (Zookeeper → Kafka → kafka-init → Redis → PostgreSQL → webhook-service)
2. All 7 Kafka topics provisioned with locked names (3 partitions, auto-create disabled) — cross-service contract established for all future milestones
3. POST /webhook: Stripe HMAC verification + Redis idempotency (Lua SET NX EX 86400) + Kafka publish with stripe_event_id partition key
4. Prometheus instrumentation from day one — every future route gets metrics automatically
5. 5 unit tests passing (happy path, duplicate skip, invalid signature) using AsyncClient + ASGITransport with mocked Redis/Kafka
6. Discovered and fixed 5 bugs: Zookeeper health check (nc missing), Stripe SDK v9 namespace, requirements.txt overwrite, Docker name conflicts, missing git init

### Stats

- Python LOC: 376 | YAML LOC: 158 | Total: ~534
- Timeline: 1 session (2026-03-22)
- Git: `0cef50a` — M1: Foundation + Ingestion

### Archive

- `.planning/milestones/v1.0-ROADMAP.md`
- `.planning/milestones/v1.0-REQUIREMENTS.md`

---
