# Phase 9: Dashboard + Monitoring - Research

**Researched:** 2026-03-28
**Domain:** Streamlit multi-page apps, Grafana JSON provisioning, Prometheus PromQL, psycopg2 seed scripting
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**A. Data Source — PostgreSQL now, BigQuery Phase 11**
- Streamlit connects to PostgreSQL (`postgres:5432` inside compose, `localhost:5432` from host)
- Env var `DATABASE_URL` — same `postgresql://payment:payment@postgres:5432/payment_db` pattern as all other services
- No `DATA_SOURCE` toggle — Phase 11 swaps the URL, that's it
- No abstraction layer

**B. Dashboard Scope — 4 pages, one per mart**
- `Fraud Metrics` → `fraud_metrics` mart
- `Payment Volume` → `hourly_payment_volume` mart
- `Merchant Performance` → `merchant_performance` mart
- `Reconciliation` → `reconciliation_summary` mart
- All SQL in `dashboard/queries/` module — not inline in page files
- Seed script: `scripts/seed_demo_data.py` — inserts into `payment_state_log`, `ledger_entries`, `reconciliation_discrepancies`

**C. Prometheus Coverage + Grafana Alert Rules**
- Add `ml-scoring-service:8001` to `prometheus.yml` (already exposes `/metrics` via `prometheus_client.make_asgi_app`)
- `webhook-service:8000` already registered — keep as-is
- Kafka consumers skipped — no HTTP server
- 2 alert rules provisioned as JSON in `monitoring/grafana/provisioning/alerting/payment_alerts.json`
  - `WebhookErrorRate`: rate of 5xx on webhook-service > 1%
  - `MLScoringLatencyP99`: histogram_quantile(0.99) on ml-scoring-service > 200ms
- Grafana datasource: `monitoring/grafana/provisioning/datasources/prometheus.json`

**D. Streamlit Deployment**
- Single docker-compose service, port 8501, no auth, `--server.runOnSave true`
- `streamlit run dashboard/app.py --server.port 8501 --server.address 0.0.0.0 --server.runOnSave true`
- Dockerfile at `dashboard/Dockerfile`, build context `..` (payment-backend root)
- `depends_on: postgres: condition: service_healthy`

### Claude's Discretion

None explicitly listed. All major decisions are locked.

### Deferred Ideas (OUT OF SCOPE)

- BigQuery connection → Phase 11
- Auth on Streamlit dashboard
- Kafka consumer lag alerts (no HTTP server, requires Kafka exporter/JMX)
- Scraping validation/scoring/ledger consumers
- Drill-down or cross-mart joins in Streamlit
- Expanding `dim_merchants` with name/tier attributes
</user_constraints>

---

## Summary

Phase 9 delivers three distinct deliverables: (1) a Streamlit multi-page dashboard querying dbt mart tables in PostgreSQL, (2) a demo seed script populating all required tables with realistic data, and (3) Grafana + Prometheus monitoring wired to both FastAPI services.

The Streamlit work is straightforward: Streamlit 1.35+ introduced `st.Page` / `st.navigation` as the canonical multi-page API (superseding the older `pages/` directory auto-discovery). The pattern is one `app.py` entrypoint calling `st.navigation([...])`, with each page as a separate `.py` file registered via `st.Page("pages/filename.py")`. Database connection management follows a two-decorator pattern: `@st.cache_resource` for the psycopg2 connection (shared across reruns, never pickled) and `@st.cache_data(ttl=300)` for query results (serializable DataFrames, refreshed every 5 minutes).

The seed script is the highest-risk task. The `ledger_entries` DEFERRABLE INITIALLY DEFERRED balance trigger requires all DEBIT+CREDIT inserts for a transaction_id to commit in a single `BEGIN/COMMIT` block. The `payment_state_log` append-only trigger blocks any UPDATE/DELETE, so the seed must be idempotent via existence checks before inserting. The `reconciliation_discrepancies` table is append-only with a simpler constraint.

Grafana provisioning uses two separate JSON file types: datasource provisioning (under `provisioning/datasources/`) and alerting provisioning (under `provisioning/alerting/`). Grafana 10.4.0 (already in docker-compose) supports both. Alert rules use a two-query pattern: refId A is the PromQL expression against Prometheus datasource; refId B is a `__expr__` threshold expression that evaluates the result of A.

**Primary recommendation:** Build in the order seed script → queries module → Streamlit pages → Prometheus config → Grafana provisioning. The seed script must exist before any dashboard page can show real data; getting it correct first avoids repeated dbt re-runs during development.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| streamlit | 1.55.0 (latest) | Multi-page dashboard framework | Already in CLAUDE.md, no alternative considered |
| psycopg2-binary | 2.9.9 | PostgreSQL driver for Streamlit queries | Already in requirements.txt |
| pandas | 2.2.2 | DataFrame display and data manipulation | Already in requirements.txt |
| prometheus_client | (via requirements.txt) | ml_service metrics exposure | Already integrated in ml_service.py |
| prometheus-fastapi-instrumentator | 6.1.0 | webhook-service metrics | Already in requirements.txt |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| altair / plotly | built into streamlit | Chart rendering | st.line_chart / st.bar_chart use Altair by default; Plotly only needed for custom chart types |
| structlog | 24.1.0 | Server-side logging in Streamlit | Already in requirements.txt; use for dashboard startup/error logging |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| psycopg2 direct | SQLAlchemy + st.connection("sql") | st.connection is cleaner but requires `pip install sqlalchemy` in dashboard Dockerfile; psycopg2 is already present |
| st.Page/st.navigation | `pages/` directory auto-discovery | `pages/` is the old pattern (still works); st.Page/st.navigation is the current recommended API in Streamlit 1.35+ |

**Installation (dashboard/Dockerfile additions):**
```bash
pip install streamlit==1.35.0 psycopg2-binary pandas
```
Note: CLAUDE.md specifies Streamlit 1.35+; current PyPI latest is 1.55.0. Pin to `streamlit>=1.35.0,<2.0` or exactly `1.35.0` for reproducibility.

---

## Architecture Patterns

### Recommended Project Structure

```
payment-backend/
  dashboard/
    app.py                     # Entry point: st.navigation([...]) + pg.run()
    pages/
      1_Fraud_Metrics.py       # Calls queries.fraud_metrics.get_fraud_metrics()
      2_Payment_Volume.py      # Calls queries.payment_volume.get_payment_volume()
      3_Merchant_Performance.py
      4_Reconciliation.py
    queries/
      __init__.py
      fraud_metrics.py         # get_fraud_metrics(conn) -> pd.DataFrame
      payment_volume.py
      merchant_performance.py
      reconciliation.py
    Dockerfile
  scripts/
    seed_demo_data.py
  monitoring/
    grafana/
      provisioning/
        datasources/
          prometheus.json
        alerting/
          payment_alerts.json
    prometheus/
      prometheus.yml           # Extended with ml-scoring-service target
  tests/
    unit/
      test_dashboard_queries.py
    e2e/
      test_dashboard_e2e.py
```

### Pattern 1: Streamlit Multi-Page with st.Page / st.navigation

**What:** `app.py` is the sole entrypoint. It calls `st.navigation(pages)` once per app run and then calls `.run()` on the returned page. Each page is a plain Python script; Streamlit executes it in the app's namespace.

**When to use:** Always — this is the current Streamlit API for multi-page apps as of v1.35+.

**Example:**
```python
# dashboard/app.py
# Source: https://docs.streamlit.io/develop/concepts/multipage-apps/page-and-navigation
import streamlit as st

st.set_page_config(page_title="Payment System Dashboard", layout="wide")

pg = st.navigation({
    "Analytics": [
        st.Page("pages/1_Fraud_Metrics.py", title="Fraud Metrics", icon=":material/security:"),
        st.Page("pages/2_Payment_Volume.py", title="Payment Volume", icon=":material/bar_chart:"),
        st.Page("pages/3_Merchant_Performance.py", title="Merchant Performance", icon=":material/store:"),
    ],
    "Operations": [
        st.Page("pages/4_Reconciliation.py", title="Reconciliation", icon=":material/fact_check:"),
    ],
})
pg.run()
```

**Key constraint:** `st.navigation` can only be called once per app run, and only from the entrypoint file (`app.py`). Page paths are relative to the entrypoint.

### Pattern 2: Two-Decorator DB Connection Pattern

**What:** `@st.cache_resource` for the connection object (one per app instance, not pickled, survives reruns), `@st.cache_data(ttl=N)` for query results (serializable DataFrames, auto-refreshed).

**When to use:** All database access in Streamlit pages.

**Example:**
```python
# dashboard/queries/__init__.py or each queries/*.py file
# Source: https://docs.streamlit.io/develop/concepts/architecture/caching
import os
import psycopg2
import pandas as pd
import streamlit as st

@st.cache_resource
def get_connection():
    """Single psycopg2 connection, shared across all reruns and users.

    st.cache_resource: non-serializable objects, no copy on access.
    Never use @st.cache_data for connections (psycopg2 is not picklable).
    """
    return psycopg2.connect(os.environ["DATABASE_URL"])


@st.cache_data(ttl=300)
def get_fraud_metrics() -> pd.DataFrame:
    """Query fraud_metrics mart. Cached 5 minutes.

    st.cache_data: serializable DataFrames, creates a copy each call.
    ttl=300 ensures stale data is refreshed without manual intervention.
    """
    conn = get_connection()
    return pd.read_sql_query(
        "SELECT * FROM dbt_dev.fraud_metrics ORDER BY metric_date DESC",
        conn
    )
```

**Critical:** psycopg2 connections can go stale (idle timeout, container restart). Add a reconnect guard:
```python
@st.cache_resource
def get_connection():
    url = os.environ["DATABASE_URL"]
    conn = psycopg2.connect(url)
    conn.autocommit = True  # dashboard is read-only; avoids open transactions
    return conn
```
If `OperationalError: connection already closed` is thrown in a query function, call `get_connection.clear()` then retry. For a portfolio demo with a single developer, this is acceptable. Production would use a connection pool.

### Pattern 3: dbt Schema Prefix in Queries

**What:** dbt dev profile materializes tables into schema `dbt_dev` (set in `dbt/profiles.yml`). Streamlit queries must use the fully-qualified schema prefix.

**When to use:** Every SQL query in `dashboard/queries/`.

**Verified from existing dbt test:**
```python
# From tests/e2e/test_phase8_e2e.py — confirms dbt_dev schema:
cur.execute("SELECT ... FROM dbt_dev.imbalanced_transaction ...")
```

**All mart queries must use `dbt_dev.` prefix:**
```sql
SELECT * FROM dbt_dev.fraud_metrics ORDER BY metric_date DESC
SELECT * FROM dbt_dev.hourly_payment_volume ORDER BY payment_hour DESC
SELECT * FROM dbt_dev.merchant_performance ORDER BY total_settled_amount_cents DESC
SELECT * FROM dbt_dev.reconciliation_summary ORDER BY run_date DESC
```

### Pattern 4: Grafana Alert Rule Provisioning (Two-Query Pattern)

**What:** Grafana-managed alert rules require two `data` entries: refId A is the PromQL query against Prometheus; refId B is a `__expr__` threshold condition referencing A. This is the standard "Classic condition" pattern for Grafana file provisioning.

**When to use:** All alert rules in `payment_alerts.json`.

**Example — MLScoringLatencyP99:**
```json
{
  "apiVersion": 1,
  "groups": [
    {
      "orgId": 1,
      "name": "payment_system_alerts",
      "folder": "Payment System",
      "interval": "60s",
      "rules": [
        {
          "uid": "ml_p99_latency_alert",
          "title": "MLScoringLatencyP99",
          "condition": "B",
          "data": [
            {
              "refId": "A",
              "datasourceUid": "prometheus",
              "relativeTimeRange": { "from": 600, "to": 0 },
              "model": {
                "expr": "histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{job=\"ml-scoring-service\"}[5m]))",
                "refId": "A",
                "interval": ""
              }
            },
            {
              "refId": "B",
              "datasourceUid": "__expr__",
              "model": {
                "conditions": [
                  {
                    "evaluator": { "params": [0.2], "type": "gt" },
                    "operator": { "type": "and" },
                    "query": { "params": ["A"] },
                    "reducer": { "type": "last" },
                    "type": "query"
                  }
                ],
                "datasource": { "type": "__expr__", "uid": "__expr__" },
                "expression": "",
                "intervalMs": 1000,
                "maxDataPoints": 43200,
                "refId": "B",
                "type": "classic_conditions"
              }
            }
          ],
          "noDataState": "NoData",
          "execErrState": "Alerting",
          "for": "5m",
          "annotations": {
            "description": "ML scoring service p99 latency exceeds 200ms (2x SLA of 100ms)"
          },
          "labels": { "severity": "warning", "team": "platform" }
        }
      ]
    }
  ]
}
```

**Critical detail:** The `datasourceUid` in the Prometheus query must match the `uid` field in the datasource provisioning JSON. Use a fixed string like `"prometheus"` in both files.

### Pattern 5: Grafana Datasource Provisioning JSON

```json
{
  "apiVersion": 1,
  "datasources": [
    {
      "name": "Prometheus",
      "type": "prometheus",
      "access": "proxy",
      "uid": "prometheus",
      "url": "http://prometheus:9090",
      "isDefault": true,
      "version": 1,
      "editable": false,
      "jsonData": {
        "prometheusType": "Prometheus",
        "prometheusVersion": "2.51.0"
      }
    }
  ]
}
```

**File location:** `monitoring/grafana/provisioning/datasources/prometheus.json`
The existing docker-compose already mounts `../monitoring/grafana` into `/etc/grafana/provisioning:ro` — Grafana auto-loads all files in `provisioning/datasources/` and `provisioning/alerting/` on startup.

### Pattern 6: Seed Script — Ledger Balance Constraint Handling

**What:** The `ledger_entries` DEFERRABLE INITIALLY DEFERRED balance trigger requires DEBIT+CREDIT inserts to commit in the same transaction. Standard `autocommit=True` mode will fire the trigger after each INSERT, rejecting the DEBIT-only row.

**Correct pattern (psycopg2 explicit transaction):**
```python
# Source: Verified from tests/e2e/test_phase8_e2e.py + migration 003
import psycopg2

conn = psycopg2.connect(os.environ.get("DATABASE_URL", "postgresql://payment:payment@localhost:5432/payment_db"))
conn.autocommit = False  # CRITICAL: must be False for DEFERRABLE trigger to work

with conn:  # conn as context manager: commits on success, rollbacks on exception
    with conn.cursor() as cur:
        for entry_type in ("DEBIT", "CREDIT"):
            cur.execute(
                """INSERT INTO ledger_entries
                   (transaction_id, amount_cents, entry_type, merchant_id, currency, source_event_id)
                   VALUES (%s, %s, %s, %s, 'USD', %s)""",
                (tx_id, amount_cents, entry_type, merchant_id, f"seed_evt_{tx_id}_{entry_type.lower()}")
            )
# Both DEBIT and CREDIT visible at COMMIT — trigger fires once, sees balance = 0
```

**Why `autocommit=False`:** When `autocommit=True`, each statement is its own transaction. The DEFERRABLE trigger fires at statement-end, sees only 1 row (DEBIT), balance != 0, raises exception. With `autocommit=False` (psycopg2 default), both inserts are in the same transaction and the deferred trigger fires at `COMMIT`.

**Idempotency for append-only tables:**
```python
# payment_state_log has no unique constraint on (transaction_id, to_state) combo
# Use SELECT before INSERT to avoid duplicate seeding:
cur.execute("SELECT COUNT(*) FROM payment_state_log WHERE event_id = %s", (event_id,))
if cur.fetchone()[0] == 0:
    cur.execute("INSERT INTO payment_state_log ...")
```

### Anti-Patterns to Avoid

- **Inline SQL in page files:** SQL must live in `dashboard/queries/` — enforced by CONTEXT.md. Inline SQL breaks the unit-test-with-mock-DB strategy.
- **`@st.cache_data` for DB connections:** psycopg2 connections are not picklable — use `@st.cache_resource` for connections, `@st.cache_data` for DataFrames.
- **`autocommit=True` in seed script for ledger inserts:** Will cause `Ledger imbalance` exception on the DEBIT insert. Must use explicit transactions.
- **`pages/` directory without st.navigation:** The old auto-discovery pattern (Streamlit scans `pages/` automatically) still works but bypasses `st.navigation` grouping and icons. Use `app.py` + `st.Page` pattern.
- **Hardcoding datasource UID in alert JSON:** The `datasourceUid` in alert rules must exactly match the `uid` in datasource provisioning. Mismatch = "datasource not found" error at alert evaluation time.
- **UPDATE/DELETE in seed script:** All three tables (`payment_state_log`, `ledger_entries`, `reconciliation_discrepancies`) have PL/pgSQL triggers that reject UPDATE and DELETE with an exception. Seed script must be INSERT-only with pre-flight existence checks.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Query result caching | TTL logic with time.time() checks | `@st.cache_data(ttl=300)` | Streamlit handles invalidation, cache busting, and per-user copies |
| DB connection singleton | Module-level global connection | `@st.cache_resource` | Streamlit restarts Python kernel on save; globals don't survive; cache_resource does |
| Grafana datasource setup | Clicking through Grafana UI | `provisioning/datasources/prometheus.json` | Provisioned sources survive container recreation; manual setup is lost on `docker rm` |
| PromQL alert logic | Custom polling scripts | Grafana alert rules with `histogram_quantile` | Grafana evaluates on its own schedule, sends to alertmanager/UI, survives restarts |
| Chart rendering | Custom HTML/JS in Streamlit | `st.line_chart`, `st.bar_chart`, `st.dataframe` | Built-in components are Altair-backed and auto-responsive |
| Ledger balance enforcement | Application-layer balance check in seed | Write DEBIT+CREDIT in same DB transaction | DB trigger is the law — application checks are redundant and inconsistent |

**Key insight:** Streamlit's caching decorators and Grafana's file provisioning are both designed to handle exactly the patterns this phase needs — don't work around them.

---

## Common Pitfalls

### Pitfall 1: dbt Schema Prefix Missing in Dashboard Queries

**What goes wrong:** `pd.read_sql_query("SELECT * FROM fraud_metrics", conn)` returns `ProgrammingError: relation "fraud_metrics" does not exist`.

**Why it happens:** dbt dev profile materializes tables into the `dbt_dev` schema (verified from `dbt/profiles.yml` and `tests/e2e/test_phase8_e2e.py`). The default PostgreSQL search path is `public`, not `dbt_dev`.

**How to avoid:** Always use fully-qualified `dbt_dev.<mart_table>` in all `dashboard/queries/` SQL. Alternatively, set `options=-c search_path=dbt_dev` in the DATABASE_URL for the Streamlit service. Recommend explicit schema prefix — more transparent.

**Warning signs:** `ProgrammingError: relation "X" does not exist` in Streamlit — immediately check schema prefix.

### Pitfall 2: Stale psycopg2 Connection After Container Restart

**What goes wrong:** Dashboard shows `OperationalError: connection already closed` after postgres container restarts or after idle timeout.

**Why it happens:** `@st.cache_resource` caches the connection object. If the PostgreSQL connection drops (container restart, idle timeout), Streamlit returns the dead cached connection object without rechecking liveness.

**How to avoid:** Wrap query calls in a try/except that calls `get_connection.clear()` on `OperationalError` and retries once. For the portfolio demo context (single developer, local docker), an `--server.runOnSave true` restart will also clear the cache.

**Warning signs:** First page load after a compose restart returns a connection error.

### Pitfall 3: Grafana Alert UID Collision or Mismatch

**What goes wrong:** Grafana logs `"Error loading rules: failed to load rule ... uid already exists"` or alert rule never fires because datasource not found.

**Why it happens:** (1) If provisioning file is changed and Grafana is reloaded without `docker rm grafana`, the old UID may conflict with the provisioned one. (2) `datasourceUid` in the alert JSON doesn't match `uid` in the datasource JSON.

**How to avoid:** Use a consistent short `uid` like `"prometheus"` in the datasource JSON and the same string in alert rule `datasourceUid`. Keep UIDs stable — don't regenerate. If Grafana state is corrupt, `docker rm -f grafana && docker-compose up -d grafana`.

**Warning signs:** Alert page in Grafana UI shows "Error loading" or datasource dropdown shows "Not found".

### Pitfall 4: Ledger Balance Trigger Fires on DEBIT-only Insert

**What goes wrong:** `seed_demo_data.py` raises `InternalError: Ledger imbalance for transaction_id X: balance = -5000 (must be 0)`.

**Why it happens:** Using `autocommit=True` (or `conn.commit()` after each INSERT) commits the DEBIT row alone. The DEFERRABLE INITIALLY DEFERRED trigger fires at commit time — it sees only 1 row, balance is -amount_cents, raises exception.

**How to avoid:** Both DEBIT and CREDIT for a transaction_id must be inserted within the same `BEGIN/COMMIT` block. Use psycopg2 context manager `with conn:` after ensuring `conn.autocommit = False`.

**Warning signs:** Seed script fails on `ledger_entries` insert with `InternalError`.

### Pitfall 5: prometheus_fastapi_instrumentator Metric Name vs prometheus_client Metric Name

**What goes wrong:** PromQL alert for ml-scoring-service uses `http_request_duration_seconds_bucket` but the ml_service uses raw `prometheus_client` (not prometheus-fastapi-instrumentator) — so the metric doesn't exist.

**Why it happens:** `webhook-service` uses `prometheus_fastapi_instrumentator` (which auto-creates `http_request_duration_seconds_bucket` with handler/method labels). `ml-scoring-service` uses `prometheus_client.make_asgi_app()` + manual `Counter` (`score_requests_total`) — it does NOT auto-create `http_request_duration_seconds`.

**How to avoid:** For the MLScoringLatencyP99 alert, the PromQL must reference a metric that actually exists on `ml-scoring-service`. Option A: add `prometheus_fastapi_instrumentator` to ml_service (matches webhook-service pattern, gives `http_request_duration_seconds` automatically). Option B: keep manual Counter and alert on `score_requests_total` rate instead (no latency data). **Recommendation: Option A** — adding `Instrumentator().instrument(app).expose(app)` to ml_service matches the existing webhook-service pattern and produces identical metric names, making the alert PromQL symmetric.

**Warning signs:** Alert rule shows "No data" in Grafana despite ml-scoring-service being up.

### Pitfall 6: Grafana Provisioning Directory Not Mounted

**What goes wrong:** Alert rules or datasources created in JSON files don't appear in Grafana.

**Why it happens:** Existing docker-compose mounts `../monitoring/grafana:/etc/grafana/provisioning:ro`. The subdirectories `provisioning/datasources/` and `provisioning/alerting/` must exist. Currently only `monitoring/grafana/.gitkeep` exists — the subdirectories need to be created.

**How to avoid:** Create the full directory tree: `monitoring/grafana/provisioning/datasources/` and `monitoring/grafana/provisioning/alerting/`. The `.gitkeep` can be removed.

**Warning signs:** Grafana starts but shows no provisioned datasources or alert rules.

---

## Code Examples

Verified patterns from official sources and project codebase:

### Streamlit Entry Point (app.py)
```python
# Source: https://docs.streamlit.io/develop/concepts/multipage-apps/page-and-navigation
import streamlit as st

st.set_page_config(
    page_title="Payment System",
    page_icon=":material/payments:",
    layout="wide",
)

pg = st.navigation({
    "Analytics": [
        st.Page("pages/1_Fraud_Metrics.py", title="Fraud Metrics"),
        st.Page("pages/2_Payment_Volume.py", title="Payment Volume"),
        st.Page("pages/3_Merchant_Performance.py", title="Merchant Performance"),
    ],
    "Operations": [
        st.Page("pages/4_Reconciliation.py", title="Reconciliation"),
    ],
})
pg.run()
```

### Query Module Pattern
```python
# dashboard/queries/fraud_metrics.py
import os
import pandas as pd
import psycopg2
import streamlit as st

@st.cache_resource
def _get_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"])

@st.cache_data(ttl=300)
def get_fraud_metrics() -> pd.DataFrame:
    conn = _get_connection()
    return pd.read_sql_query(
        "SELECT metric_date, current_state, transaction_count, "
        "unique_merchants, total_amount_cents, avg_amount_cents "
        "FROM dbt_dev.fraud_metrics ORDER BY metric_date DESC LIMIT 90",
        conn
    )
```

### Page File Pattern
```python
# dashboard/pages/1_Fraud_Metrics.py
import streamlit as st
from dashboard.queries.fraud_metrics import get_fraud_metrics

st.title("Fraud Metrics")

df = get_fraud_metrics()
if df.empty:
    st.warning("No data. Run seed script: python scripts/seed_demo_data.py")
    st.stop()

# KPI cards
col1, col2, col3 = st.columns(3)
col1.metric("Total Transactions", int(df["transaction_count"].sum()))
# ... more metrics

# Chart
st.subheader("Transaction Count by State (last 90 days)")
st.bar_chart(df.set_index("metric_date")["transaction_count"])

# Filterable table
st.subheader("Raw Data")
states = st.multiselect("Filter by state", df["current_state"].unique().tolist(), default=df["current_state"].unique().tolist())
st.dataframe(df[df["current_state"].isin(states)], use_container_width=True)
```

### Seed Script — Ledger Pair Insert
```python
# scripts/seed_demo_data.py — ledger pair insert (key pattern)
# Source: Verified against migration 003 DEFERRABLE INITIALLY DEFERRED trigger
import psycopg2, os

def insert_ledger_pair(conn, tx_id: str, amount_cents: int, merchant_id: str):
    """Insert DEBIT + CREDIT in same transaction to satisfy balance trigger."""
    # autocommit must be False (psycopg2 default) — both rows must commit together
    with conn.cursor() as cur:
        for entry_type in ("DEBIT", "CREDIT"):
            cur.execute(
                """INSERT INTO ledger_entries
                   (transaction_id, amount_cents, entry_type, merchant_id, currency, source_event_id)
                   VALUES (%s, %s, %s, %s, 'USD', %s)""",
                (tx_id, amount_cents, entry_type, merchant_id,
                 f"seed_{tx_id}_{entry_type.lower()}")
            )
    conn.commit()  # trigger fires HERE — sees both rows, balance = 0, passes
```

### Prometheus Config Addition
```yaml
# monitoring/prometheus/prometheus.yml — add ml-scoring-service target
scrape_configs:
  - job_name: "webhook-service"
    static_configs:
      - targets: ["webhook-service:8000"]
    metrics_path: /metrics

  - job_name: "ml-scoring-service"
    static_configs:
      - targets: ["ml-scoring-service:8001"]
    metrics_path: /metrics
```

### Streamlit Dockerfile Pattern
```dockerfile
# dashboard/Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir streamlit==1.35.0 psycopg2-binary pandas

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "dashboard/app.py", \
     "--server.port", "8501", \
     "--server.address", "0.0.0.0", \
     "--server.runOnSave", "true"]
```

### Docker Compose Service Addition
```yaml
# To add in infra/docker-compose.yml
streamlit-dashboard:
  build:
    context: ..
    dockerfile: dashboard/Dockerfile
  container_name: streamlit-dashboard
  ports:
    - "8501:8501"
  environment:
    - DATABASE_URL=postgresql://payment:payment@postgres:5432/payment_db
  depends_on:
    postgres:
      condition: service_healthy
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"]
    interval: 15s
    timeout: 5s
    retries: 3
    start_period: 20s
  restart: unless-stopped
```

Note: Streamlit's built-in health endpoint is `/_stcore/health` (returns `"ok"`), which is preferable to checking the main page.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `pages/` directory auto-scan | `st.Page` + `st.navigation` in `app.py` | Streamlit 1.28 (Oct 2023) | More control over page titles, icons, grouping, and dynamic navigation |
| `@st.cache` (deprecated) | `@st.cache_data` / `@st.cache_resource` | Streamlit 1.18 (Dec 2022) | Old decorator still works but shows deprecation warning |
| Grafana legacy alerting | Grafana unified alerting + file provisioning | Grafana 9+ | `provisioning/alerting/` directory; old `alerts` key in dashboard JSON is deprecated |
| Grafana datasource YAML only | JSON and YAML both supported | Grafana 8+ | Either format works; CONTEXT.md specifies `.json` — use JSON |

**Deprecated/outdated:**
- `@st.experimental_rerun()` → `st.rerun()` (Streamlit 1.27+)
- `@st.cache` → split into `@st.cache_data` / `@st.cache_resource`
- Grafana `alerting: true` in `prometheus.yml` → use Grafana's native alerting

---

## Open Questions

1. **prometheus_fastapi_instrumentator vs prometheus_client on ml_service for latency metric**
   - What we know: `ml_service.py` uses `prometheus_client.make_asgi_app()` and a manual `Counter` (`score_requests_total`). It does NOT use `prometheus_fastapi_instrumentator`. The `http_request_duration_seconds_bucket` metric referenced in the CONTEXT.md alert PromQL only exists when `prometheus_fastapi_instrumentator` is used.
   - What's unclear: Whether the plan should add `Instrumentator().instrument(app).expose(app)` to `ml_service.py` (adds the histogram automatically) or change the alert PromQL to use a different metric.
   - Recommendation: Add `prometheus_fastapi_instrumentator` instrumentation to `ml_service.py` in Plan 01. This is a 2-line addition matching the webhook-service pattern and makes the alert PromQL work without any metric name gymnastics. The existing `make_asgi_app()` mount can be removed as `Instrumentator.expose(app)` replaces it.

2. **dbt run before dashboard starts**
   - What we know: Streamlit queries `dbt_dev.*` views/tables. If `dbt run` has not been executed, those tables don't exist.
   - What's unclear: Whether the docker-compose startup sequence should include a `dbt-run` service or rely on documentation.
   - Recommendation: Document in seed_demo_data.py that `dbt run` must be executed before seeding. Do not add a docker-compose dbt service — that requires dbt-postgres in the container and adds complexity. Instructions: run `dbt run` from host with `.venv` activated, then run `python scripts/seed_demo_data.py`.

3. **Grafana `folder` field in alert JSON must exist**
   - What we know: The `folder` field in the alert provisioning JSON refers to a Grafana folder (not a filesystem path). If the folder doesn't exist, Grafana may fail to provision the rules.
   - What's unclear: Whether Grafana 10.4.0 auto-creates the folder on provisioning startup.
   - Recommendation: Use `"folder": "alerting"` (a generic name) or test `"folder": "General"` (Grafana's default folder). Alternatively, provision a folder explicitly via a `provisioning/folders/` file if Grafana rejects an unknown folder name.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.2.1 |
| Config file | None (pytest discovers via standard convention) |
| Quick run command | `PYTHONPATH=payment-backend pytest payment-backend/tests/unit/test_dashboard_queries.py -x` |
| Full suite command | `PYTHONPATH=payment-backend pytest payment-backend/tests/ -x --ignore=payment-backend/tests/integration --ignore=payment-backend/tests/e2e` |

### Phase Requirements → Test Map

| Behavior | Test Type | Automated Command | File Exists? |
|----------|-----------|-------------------|-------------|
| Query functions return DataFrames from mocked connection | unit | `pytest tests/unit/test_dashboard_queries.py -x` | Wave 0 |
| Query functions handle empty result gracefully | unit | same file | Wave 0 |
| Seed script inserts state_log rows (idempotent) | integration | `INTEGRATION_TEST=1 pytest tests/integration/test_phase9_integration.py -x` | Wave 0 |
| Seed script inserts ledger DEBIT+CREDIT pairs without balance error | integration | same | Wave 0 |
| Seed script inserts reconciliation_discrepancies rows | integration | same | Wave 0 |
| Streamlit dashboard responds at :8501 | e2e | `INTEGRATION_TEST=1 pytest tests/e2e/test_dashboard_e2e.py -x` | Wave 0 |
| Prometheus scrapes ml-scoring-service:8001/metrics | e2e | same | Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/unit/test_dashboard_queries.py -x`
- **Per wave merge:** `pytest tests/unit/ tests/integration/test_phase9_integration.py -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/unit/test_dashboard_queries.py` — unit tests for all 4 query functions with mocked psycopg2 connection
- [ ] `tests/integration/test_phase9_integration.py` — seed script + query verification against live postgres
- [ ] `tests/e2e/test_dashboard_e2e.py` — Streamlit :8501 health check + Prometheus scrape verification
- [ ] `dashboard/Dockerfile` — needs `streamlit` in dependencies list
- [ ] `dashboard/queries/__init__.py` — package init

---

## Sources

### Primary (HIGH confidence)

- Official Streamlit docs: `https://docs.streamlit.io/develop/concepts/multipage-apps/page-and-navigation` — st.Page / st.navigation API, verified current
- Official Streamlit docs: `https://docs.streamlit.io/develop/concepts/architecture/caching` — st.cache_data vs st.cache_resource, TTL pattern
- Official Grafana docs: `https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/file-provisioning/` — alert rule JSON schema
- Official Grafana docs: `https://grafana.com/docs/grafana/latest/administration/provisioning/` — datasource provisioning format
- Project codebase: `tests/e2e/test_phase8_e2e.py` — confirmed `dbt_dev` schema prefix, confirmed ledger DEBIT+CREDIT pattern
- Project codebase: `db/migrations/versions/003_create_ledger_entries.py` — confirmed DEFERRABLE INITIALLY DEFERRED trigger mechanics
- Project codebase: `services/ml_service.py` — confirmed prometheus_client.make_asgi_app() + Counter only (no histogram)
- Project codebase: `infra/docker-compose.yml` — confirmed Grafana 10.4.0, provisioning mount path

### Secondary (MEDIUM confidence)

- GitHub: `https://github.com/trallnag/prometheus-fastapi-instrumentator` — metric names `http_requests_total` (handler, status, method labels) and `http_request_duration_seconds` (handler, method labels). Verified against `services/main.py` usage pattern.
- PyPI: `https://pypi.org/project/streamlit/` — confirmed latest version 1.55.0 released 2026-03-03

### Tertiary (LOW confidence)

- WebSearch result re: Grafana auto-creating provisioned alert folders — not verified against official docs for Grafana 10.4.0 specifically

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in requirements.txt or official project stack
- Architecture: HIGH — Streamlit docs verified, Grafana provisioning verified, db schemas verified from codebase
- Pitfalls: HIGH for ledger constraint and dbt schema prefix (verified from codebase); MEDIUM for Grafana folder auto-creation (unverified)
- Seed script patterns: HIGH — verified from migration 003 trigger code + Phase 8 E2E test

**Research date:** 2026-03-28
**Valid until:** 2026-04-28 (Streamlit stable; Grafana provisioning format stable since v9)
