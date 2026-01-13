from __future__ import annotations
"""
server/app/api/v1/router.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Router principal de l'API v1.

- Centralise l'inclusion de tous les sous-routers d'endpoints v1.
- Chaque endpoint définit son propre `prefix` (ex. "/health", "/http-targets", etc.).
- Ce router est ensuite monté dans l'app FastAPI principale sous le préfixe global "/api/v1".
"""

from fastapi import APIRouter

# ⚠️ IMPORTANT
# Les imports doivent pointer vers le même package que vos endpoints.
# Ici, on importe depuis `app.api.v1.endpoints` (et non `app.app.api...`).
# Vérifiez que votre arborescence correspond bien (c'est le cas des fichiers fournis).
from app.api.v1.endpoints import (
    health,
    ingest,
    machines,
    alerts,
    incidents,
    http_targets,  # ✅ notre router HTTP targets
    settings,
    metrics,
    dashboard,
    auth,
    notifications,
)

# Crée un router v1 qui agglomère tous les sous-routers.
api_router = APIRouter()

# Ordre non bloquant, mais on regroupe par thématique pour la lisibilité.
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])  # endpoints d'auth
api_router.include_router(settings.router, tags=["settings"])
api_router.include_router(dashboard.router, tags=["dashboard"])

# Monitoring / données
api_router.include_router(ingest.router, tags=["ingest"])
api_router.include_router(metrics.router, tags=["metrics"])
api_router.include_router(alerts.router, tags=["alerts"])
api_router.include_router(incidents.router, tags=["incidents"])
api_router.include_router(machines.router, tags=["machines"])
api_router.include_router(notifications.router, tags=["notifications"])
api_router.include_router(http_targets.router, tags=["http-targets"])

