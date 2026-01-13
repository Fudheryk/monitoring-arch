from __future__ import annotations

"""
server/app/api/v1/endpoints/health.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Health check.

Objectif :
- Répondre "ok" pour vérifier que l'API est joignable.
- Supporter GET *et* HEAD :
    - GET : renvoie un JSON {"status": "ok"}
    - HEAD : renvoie uniquement les headers (sans body) → utile pour curl -I,
      load balancers, probes, etc.
"""

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()

@router.get("/health", summary="Health check (GET)")
async def health_get() -> dict[str, str]:
    """
    Health check classique.

    Exemple :
        curl -sk https://monitoring.local/api/v1/health
    Attendu :
        {"status":"ok"}
    """
    return {"status": "ok"}


@router.head("/health", summary="Health check (HEAD)")
async def health_head() -> Response:
    """
    Health check HEAD.

    HEAD doit renvoyer les mêmes headers que GET, mais sans body.
    Exemple :
        curl -skI https://monitoring.local/api/v1/health
    Attendu :
        HTTP/1.1 200 OK
        ...

    Note :
    - On renvoie une Response vide avec status_code=200.
    - FastAPI ne renverra pas de body (conforme HTTP).
    """
    return Response(status_code=200)
