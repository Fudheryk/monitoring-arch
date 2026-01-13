from __future__ import annotations
"""
Routes web (login) — frontend séparé du backend.
- GET/HEAD /login : affiche le formulaire de connexion.
- POST     /login : appelle l'API `/api/v1/auth/login` via httpx,
                    puis RELAY tous les Set-Cookie vers le navigateur.
"""

from pathlib import Path
import os

import httpx
from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["web"])

# ---------------------------------------------------------------------
# Templates Jinja2 (ex: login.html + fragments/_head.html)
# ---------------------------------------------------------------------
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Cache-busting simple utilisé par ton fragment _head.html
templates.env.globals["version_cache_bust"] = "dev"

# ---------------------------------------------------------------------
# URL interne du backend API (réseau Docker).
# En prod, on peut passer par un reverse-proxy (même domaine).
# ---------------------------------------------------------------------
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")

# ---------------------------------------------------------------------
# Pages : /login (GET + HEAD)
# ---------------------------------------------------------------------
@router.api_route("/login", methods=["GET", "HEAD"], name="login_page", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None) -> HTMLResponse:
    """Affiche la page de connexion. HEAD accepté pour certains healthchecks."""
    return templates.TemplateResponse("login.html", {"request": request, "error": error})

# ---------------------------------------------------------------------
# Form submit : /login (POST)
# ---------------------------------------------------------------------
# Route POST /login utilisée par le formulaire (name="login" pour url_for('login'))
@router.post("/login", name="login")
async def login_submit(
    request: Request,
    email: str = Form(...),     # ⚠️ on unifie sur "email" (pas "username")
    password: str = Form(...),
):
    # Appel serveur→serveur à l’API (pas de CORS ; cookies HttpOnly posés par l’API)
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0, follow_redirects=False) as client:
        try:
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": password},  # payload attendu par l’API
            )
        except httpx.RequestError:
            # API KO → on reste sur la page login avec un message
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "API indisponible, réessayez.",
                    "version_cache_bust": "dev",
                },
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    if resp.status_code != 200:
        # 401 attendu si mauvais identifiants → on ré-affiche le formulaire avec l’erreur
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Identifiants invalides.",
                "version_cache_bust": "dev",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Succès → redirige vers "/" (dashboard) ET propage tous les Set-Cookie renvoyés par l’API
    redirect = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    # httpx.Headers.get_list retourne toutes les occurrences Set-Cookie (access / refresh)
    for cookie in resp.headers.get_list("set-cookie"):
        # Starlette accepte les en-têtes répétés "set-cookie"
        redirect.headers.append("set-cookie", cookie)

    return redirect


@router.get("/machines", name="machines")
async def machines_list(
    request: Request
):
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as client:
        r = await client.get(
            "/api/v1/machines",
            cookies=request.cookies,
            headers={"authorization": request.headers.get("authorization", "")}
            if request.headers.get("authorization")
            else {},
        )
    return HTMLResponse(r.text, status_code=r.status_code)