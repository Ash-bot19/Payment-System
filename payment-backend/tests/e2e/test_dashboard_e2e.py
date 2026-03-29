"""E2E tests for Phase 09 — Streamlit dashboard + monitoring stack.

Requires Docker stack running:
    cd payment-backend/infra && docker compose up -d

All tests skip gracefully if services are not reachable.
"""

import base64
import json
import urllib.error
import urllib.request

import pytest
import structlog

logger = structlog.get_logger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _fetch(url: str, timeout: int = 5, headers: dict | None = None) -> str:
    """Fetch URL content. Raises on network failure."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode()


def _grafana_auth_header() -> dict:
    """Basic auth header for Grafana (admin:admin)."""
    token = base64.b64encode(b"admin:admin").decode()
    return {"Authorization": f"Basic {token}"}


# ─── Skip fixture ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _skip_without_stack():
    """Skip all tests if the Docker stack is not running."""
    try:
        _fetch("http://localhost:8501/_stcore/health", timeout=3)
    except (urllib.error.URLError, ConnectionError, OSError):
        pytest.skip(
            "Docker stack not running (streamlit-dashboard not reachable at :8501)"
        )


# ─── Streamlit tests ──────────────────────────────────────────────────────────


def test_streamlit_health():
    """Streamlit health endpoint returns ok."""
    body = _fetch("http://localhost:8501/_stcore/health")
    assert "ok" in body.lower(), f"Unexpected health response: {body!r}"
    logger.info("test_streamlit_health_passed")


def test_streamlit_main_page_loads():
    """Streamlit root page is a valid HTML SPA shell."""
    body = _fetch("http://localhost:8501/")
    # Streamlit serves a React SPA — page title is injected by JS, not static HTML.
    # Assert the HTML shell and root div are present.
    assert "<html" in body, "Expected HTML page from Streamlit"
    assert 'id="root"' in body, "Expected Streamlit React root div"
    logger.info("test_streamlit_main_page_loads_passed")


# ─── Prometheus tests ─────────────────────────────────────────────────────────


def test_prometheus_targets_include_ml_service():
    """Prometheus active targets include both webhook-service and ml-scoring-service."""
    try:
        body = _fetch("http://localhost:9090/api/v1/targets")
    except (urllib.error.URLError, ConnectionError, OSError):
        pytest.skip("Prometheus not reachable at :9090")

    data = json.loads(body)
    active_targets = data.get("data", {}).get("activeTargets", [])
    jobs = {t["labels"].get("job") for t in active_targets}

    assert "webhook-service" in jobs, f"webhook-service not in Prometheus targets: {jobs}"
    assert "ml-scoring-service" in jobs, (
        f"ml-scoring-service not in Prometheus targets: {jobs}"
    )
    logger.info("test_prometheus_targets_passed", jobs=sorted(jobs))


def test_prometheus_ml_service_metrics_available():
    """Prometheus can query the ml-scoring-service 'up' metric."""
    try:
        body = _fetch(
            "http://localhost:9090/api/v1/query?query=up%7Bjob%3D%22ml-scoring-service%22%7D"
        )
    except (urllib.error.URLError, ConnectionError, OSError):
        pytest.skip("Prometheus not reachable at :9090")

    data = json.loads(body)
    results = data.get("data", {}).get("result", [])
    assert results, "No 'up' metric results for ml-scoring-service — service may not be scraped yet"
    logger.info("test_prometheus_ml_metrics_passed", result_count=len(results))


# ─── Grafana tests ────────────────────────────────────────────────────────────


def test_grafana_datasource_provisioned():
    """Grafana has a Prometheus datasource with uid='prometheus'."""
    try:
        body = _fetch(
            "http://localhost:3000/api/datasources",
            headers=_grafana_auth_header(),
        )
    except (urllib.error.URLError, ConnectionError, OSError):
        pytest.skip("Grafana not reachable at :3000")

    datasources = json.loads(body)
    prom_ds = [
        ds for ds in datasources
        if ds.get("type") == "prometheus" and ds.get("uid") == "prometheus"
    ]
    assert prom_ds, (
        f"No Prometheus datasource with uid='prometheus' found. Datasources: {datasources}"
    )
    logger.info("test_grafana_datasource_provisioned_passed")


def test_grafana_alert_rules_provisioned():
    """Grafana has WebhookErrorRate and MLScoringLatencyP99 alert rules provisioned."""
    try:
        body = _fetch(
            "http://localhost:3000/api/v1/provisioning/alert-rules",
            headers=_grafana_auth_header(),
        )
    except (urllib.error.URLError, ConnectionError, OSError):
        pytest.skip("Grafana not reachable at :3000")

    rules = json.loads(body)
    assert isinstance(rules, list), f"Expected list of alert rules, got: {type(rules)}"
    assert len(rules) >= 2, f"Expected >= 2 alert rules, got {len(rules)}"

    titles = {r.get("title") for r in rules}
    assert "WebhookErrorRate" in titles, (
        f"WebhookErrorRate alert rule missing. Rules found: {titles}"
    )
    assert "MLScoringLatencyP99" in titles, (
        f"MLScoringLatencyP99 alert rule missing. Rules found: {titles}"
    )
    logger.info("test_grafana_alert_rules_provisioned_passed", titles=sorted(titles))
