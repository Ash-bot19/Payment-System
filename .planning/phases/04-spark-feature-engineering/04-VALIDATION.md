---
phase: 4
slug: spark-feature-engineering
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-24
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | payment-backend/tests/ |
| **Quick run command** | `pytest payment-backend/tests/unit/spark/ -x -q` |
| **Full suite command** | `pytest payment-backend/tests/unit/spark/ payment-backend/tests/integration/spark/ -v` |
| **Estimated runtime** | ~30 seconds (unit), ~120 seconds (integration with SparkSession) |

---

## Sampling Rate

- **After every task commit:** Run `pytest payment-backend/tests/unit/spark/ -x -q`
- **After every plan wave:** Run `pytest payment-backend/tests/unit/spark/ payment-backend/tests/integration/spark/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds (unit only)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 4-01-01 | 01 | 1 | feat-engineering | unit | `pytest payment-backend/tests/unit/spark/test_feature_engineering.py -x -q` | ❌ W0 | ⬜ pending |
| 4-01-02 | 01 | 1 | feat-engineering | unit | `pytest payment-backend/tests/unit/spark/test_feature_engineering.py -x -q` | ❌ W0 | ⬜ pending |
| 4-02-01 | 02 | 2 | kafka-consumer | integration | `pytest payment-backend/tests/integration/spark/ -x -q` | ❌ W0 | ⬜ pending |
| 4-02-02 | 02 | 2 | redis-writes | integration | `pytest payment-backend/tests/integration/spark/ -x -q` | ❌ W0 | ⬜ pending |
| 4-03-01 | 03 | 3 | e2e-pipeline | integration | `pytest payment-backend/tests/integration/spark/test_e2e.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `payment-backend/tests/unit/spark/__init__.py` — unit test package
- [ ] `payment-backend/tests/unit/spark/test_feature_engineering.py` — stubs for all 8 feature computations
- [ ] `payment-backend/tests/integration/spark/__init__.py` — integration test package
- [ ] `payment-backend/tests/integration/spark/conftest.py` — SparkSession fixture (local mode), Redis mock/live fixture
- [ ] `payment-backend/tests/integration/spark/test_spark_kafka_consumer.py` — stubs for Kafka consume → feature write path

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| GCS checkpoint persistence | checkpoint-dir | Requires GCP credentials and GCS bucket (Phase 11 concern) | Verify SPARK_CHECKPOINT_DIR env points to named Docker volume in local dev; GCS path deferred to Phase 11 |
| Spark UI (port 4040) accessible | observability | Browser-based; no programmatic assertion | `docker-compose up spark-feature-engine` → visit http://localhost:4040 → verify streaming query visible |
| Kafka consumer group lag = 0 after drain | throughput | Requires live Kafka admin client query | `kafka-consumer-groups.sh --describe --group spark-feature-engine` after test messages consumed |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
