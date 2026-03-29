"""FastAPI ML scoring service — Phase 05.

Serves POST /score for synchronous ML inference against the pre-loaded
XGBoost model. Used as a debug/direct endpoint; production scoring runs
via ScoringConsumer (Kafka-based).

Endpoints:
    GET  /health — liveness probe, reports model_loaded status
    POST /score  — accepts FeatureVector, returns RiskScore
    GET  /metrics — Prometheus metrics (ASGI mounted)

Model is loaded once at startup via XGBoostScorer (per D-A3: missing model
→ crash-and-exit at startup). SLA: p99 < 100ms local, < 50ms on GCP.
"""

import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_client import Counter, make_asgi_app
from prometheus_fastapi_instrumentator import Instrumentator

from ml.scorer import XGBoostScorer
from models.ml_scoring import FeatureVector, RiskScore

logger = structlog.get_logger(__name__)

# Prometheus metrics
score_requests_total = Counter(
    "score_requests_total",
    "Total POST /score requests",
)

# Module-level scorer reference — populated at startup
_scorer: XGBoostScorer | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load XGBoost model once at startup (per D-A3).

    XGBoostScorer.sys.exit(1) if model file missing — distinguishes
    misconfiguration from recoverable runtime errors.
    """
    global _scorer
    model_path = os.getenv("ML_MODEL_PATH", "ml/models/model.ubj")
    _scorer = XGBoostScorer(model_path)
    logger.info("ml_service_startup", model_path=model_path)
    yield
    logger.info("ml_service_shutdown")


app = FastAPI(
    title="ML Scoring Service",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount Prometheus metrics at /metrics
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Instrument app for HTTP request duration histograms (histogram_quantile Grafana alerts)
Instrumentator().instrument(app)


@app.get("/health")
def health() -> dict:
    """Liveness probe. Reports model load status."""
    return {"status": "ok", "model_loaded": _scorer is not None}


@app.post("/score", response_model=RiskScore)
def score(features: FeatureVector) -> RiskScore:
    """Run XGBoost inference on 8 ML features.

    Accepts a FeatureVector (8 float fields per CLAUDE.md ML Risk Score Contract)
    and returns a RiskScore with risk_score in [0,1], is_high_risk, manual_review.

    Args:
        features: FeatureVector with all 8 required fields.

    Returns:
        RiskScore with risk_score float[0,1], is_high_risk bool, manual_review bool.
    """
    score_requests_total.inc()
    result = _scorer.score(features.model_dump())
    logger.info(
        "score_request",
        risk_score=result.risk_score,
        is_high_risk=result.is_high_risk,
        manual_review=result.manual_review,
    )
    return result
