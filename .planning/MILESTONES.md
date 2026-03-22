# Milestones

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
