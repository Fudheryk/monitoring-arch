from __future__ import annotations
"""server/app/main.py
~~~~~~~~~~~~~~~~~~~~~~~~
Point d'entrée FastAPI.
"""
from typing import List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.middleware import install_global_middleware

from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.middleware import install_global_middleware

app = FastAPI(title="Monitoring Server", version="0.2.1")

allow_origins: List[str] = []
if origins := getattr(settings, "CORS_ALLOW_ORIGINS", None):
    allow_origins = [o.strip() for o in origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup() -> None:
    setup_logging()
    install_global_middleware(app)

app.include_router(api_router, prefix="/api/v1")
