from __future__ import annotations
"""server/app/api/v1/endpoints/health.py
~~~~~~~~~~~~~~~~~~~~~~~~
Health check.
"""
from fastapi import APIRouter

from fastapi import APIRouter

router = APIRouter()

@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
