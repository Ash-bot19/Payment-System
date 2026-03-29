---
phase: 9
slug: dashboard-monitoring
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-28
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | `payment-backend/pytest.ini` (existing) |
| **Quick run command** | `pytest tests/unit/test_dashboard_queries.py -x -q` |
| **Full suite command** | `pytest tests/unit/ tests/integration/ tests/e2e/ -x -q` |
| **Estimated runtime** | ~30 seconds (unit only) / ~90 seconds (full) |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/unit/test_dashboard_queries.py -x -q`
- **After every plan wave:** Run full suite
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 09-01-01 | 01 | 1 | seed data | integration | `pytest tests/integration/test_seed_demo_data.py -x -q` | ❌ W0 | ⬜ pending |
| 09-01-02 | 01 | 1 | prometheus scrape | unit | `grep ml-scoring-service payment-backend/monitoring/prometheus/prometheus.yml` | ✅ | ⬜ pending |
| 09-01-03 | 01 | 1 | ml_service histogram | unit | `grep Instrumentator payment-backend/services/ml_service.py` | ✅ | ⬜ pending |
| 09-02-01 | 02 | 1 | grafana datasource | unit | `test -f payment-backend/monitoring/grafana/provisioning/datasources/prometheus.json` | ❌ W0 | ⬜ pending |
| 09-02-02 | 02 | 1 | grafana alerts | unit | `test -f payment-backend/monitoring/grafana/provisioning/alerting/payment_alerts.json` | ❌ W0 | ⬜ pending |
| 09-03-01 | 03 | 2 | dashboard queries | unit | `pytest tests/unit/test_dashboard_queries.py -x -q` | ❌ W0 | ⬜ pending |
| 09-03-02 | 03 | 2 | streamlit pages | unit | `python -c "import dashboard.pages"` | ❌ W0 | ⬜ pending |
| 09-03-03 | 03 | 2 | docker-compose service | e2e | `pytest tests/e2e/test_dashboard_e2e.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_dashboard_queries.py` — stubs for query module tests
- [ ] `tests/integration/test_seed_demo_data.py` — seed script integration test stub
- [ ] `tests/e2e/test_dashboard_e2e.py` — Streamlit E2E stub (skip without live stack)
- [ ] `payment-backend/monitoring/grafana/provisioning/datasources/` — directory
- [ ] `payment-backend/monitoring/grafana/provisioning/alerting/` — directory

*Existing pytest infrastructure covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Streamlit pages render with data | Dashboard UX | Requires browser + running stack | `docker compose up -d`, open `http://localhost:8501`, verify all 4 pages show data after running seed script + `dbt run` |
| Grafana alert fires | Alert SLA | Requires Grafana + Prometheus live | Open Grafana at `:3000`, check Alerting → Alert Rules, verify `WebhookErrorRate` and `MLScoringLatencyP99` rules are Active |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
