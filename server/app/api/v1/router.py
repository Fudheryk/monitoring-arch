from __future__ import annotations
"""server/app/api/v1/router.py
~~~~~~~~~~~~~~~~~~~~~~~~
Router principal API v1.
"""
from fastapi import APIRouter
from app.api.v1.endpoints import health, ingest, machines, alerts, incidents, http_targets, settings, metrics, dashboard



api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(ingest.router, tags=["ingest"])
api_router.include_router(machines.router, tags=["machines"])
api_router.include_router(metrics.router, tags=["metrics"])
api_router.include_router(alerts.router, tags=["alerts"])
api_router.include_router(incidents.router, tags=["incidents"])
api_router.include_router(http_targets.router, tags=["http-targets"])
api_router.include_router(settings.router, tags=["settings"])
api_router.include_router(dashboard.router, tags=["dashboard"])
