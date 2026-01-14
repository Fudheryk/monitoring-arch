from __future__ import annotations
"""
webapp/app/main.py
ASGI de la WebApp (login).
- Sert les templates + static
- Appelle l'API backend /api/v1/auth/login
- Propage les Set-Cookie de l'API (access/refresh)
- Expose /_health (healthcheck Docker) et /health
- Page / protégée via @login_required (voir app/auth_guard.py)
"""

from pathlib import Path

import httpx
import os
import asyncio
import subprocess

from datetime import datetime, timezone
from fastapi import FastAPI, Request, Form, status, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.auth_guard import login_required  # décorateur qui vérifie l'auth (cookies) côté webapp
from app.config import get_settings         # config centralisée (API_BASE_URL, etc.)

# Chargement config (env/.env)
settings = get_settings()

import logging
logger = logging.getLogger(__name__)


VERSION_CACHE_BUST = os.getenv("GIT_COMMIT", "dev")


def _parse_iso(ts: str | None) -> datetime | None:
    """
    Parse ISO8601 "backend-like" (avec ou sans Z).
    Retourne un datetime timezone-aware UTC si possible.
    """
    if not ts:
        return None
    try:
        # Support "...Z"
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _human_duration(seconds: int | None) -> str | None:
    """
    65  -> "1m 05s"
    3605 -> "1h 00m"
    90061 -> "1j 1h"
    """
    if seconds is None or seconds < 0:
        return None

    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)

    if days > 0:
        # on garde compact : jours + heures
        return f"{days}j {hours}h"
    if hours > 0:
        return f"{hours}h {mins:02d}m"
    if mins > 0:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print(f"[WEB] cookies: ACCESS={settings.ACCESS_COOKIE} REFRESH={settings.REFRESH_COOKIE}")
    # (ici: ouvrir des connexions, clients http globaux, etc.)
    yield
    # Shutdown
    # (ici: fermer proprement ce qui doit l’être)
    print("[WEB] shutdown complete")

app = FastAPI(title="NeonMonitor Web", version="0.1.0", lifespan=lifespan)

API_BASE = settings.API_BASE_URL.rstrip("/")

# ── Static & Templates ────────────────────────────────────────────────────────
# Assure-toi que les dossiers existent : webapp/app/static et webapp/app/templates
BASE = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# ── Helpers ──────────────────────────────────────────────────────────────────
def _get_dev_api_key() -> str:
    return getattr(settings, "DEV_API_KEY", None) or os.getenv("DEV_API_KEY") or "dev-apikey-123"

def _get_prod_api_key() -> str:
    return getattr(settings, "API_KEY", None) or os.getenv("API_KEY") or "prod-apikey-xxxxxxxxxx"

# ── Startup Event ────────────────────────────────────────────────────────────
# Log des cookies utilisés côté webapp (doivent être alignés avec l'API)
@app.on_event("startup")
async def _on_startup():
    print(f"[WEB] cookies: ACCESS={settings.ACCESS_COOKIE} REFRESH={settings.REFRESH_COOKIE}")

# ── Healthchecks ─────────────────────────────────────────────────────────────
# Utilisé par Docker/compose pour valider le conteneur web.
@app.get("/_health")
@app.get("/health")
def health():
    return {"status": "ok"}

# ── Pages ────────────────────────────────────────────────────────────────────

# Page de login (GET/HEAD)
# Note: Starlette gère HEAD en supprimant le corps de la réponse automatiquement.
@app.api_route("/login", methods=["GET", "HEAD"], response_class=HTMLResponse, name="login_page")
def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "title": "Connexion",
            "version_cache_bust": VERSION_CACHE_BUST,
            "error": error,
        },
    )

# Soumission du formulaire de login
# IMPORTANT : les champs du formulaire doivent s'appeler "email" et "password"
@app.post("/login", name="login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    # Appel backend API (serveur→serveur) : pas de CORS, on récupère des cookies HttpOnly
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0, follow_redirects=False) as client:
        try:
            resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        except httpx.RequestError:
            # API down / réseau KO → 503 côté webapp
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "API indisponible, réessayez.", "version_cache_bust": VERSION_CACHE_BUST},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    if resp.status_code != 200:
        # 401 attendu si mauvais identifiants
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Identifiants invalides.", "version_cache_bust": VERSION_CACHE_BUST},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Succès → redirection vers la page protégée "/"
    redirect = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    # Propage TOUS les Set-Cookie renvoyés par l’API (access_token / refresh_token)
    # httpx.Headers.get_list() retourne l’ensemble des en-têtes multi-occurrences.
    for cookie in resp.headers.get_list("set-cookie"):
        # Starlette permet d'ajouter plusieurs "set-cookie" dans la réponse
        redirect.headers.append("set-cookie", cookie)

    return redirect


# Page d'accueil protégée (dashboard)
# Le décorateur @login_required doit soit :
#   - s'appuyer sur un middleware qui a déjà validé le token et posé request.state.user,
#   - soit appeler l'API /auth/me lui-même (selon ton implémentation dans app/auth_guard.py).
@app.get("/", response_class=HTMLResponse, name="home")
@login_required
async def home(request: Request):
    user = getattr(request.state, "user", None)  # défini par le guard si présent

    # ✅ Permet au dashboard de charger directement la 1ère machine (sans passer par /fragment/machines)
    first_machine_id: str | None = None
    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
            r = await client.get(
                "/api/v1/machines",
                headers={"X-API-Key": _get_dev_api_key()},
            )
        if r.status_code == 200:
            machines = r.json() or []
            if machines and isinstance(machines, list):
                mid = (machines[0] or {}).get("id")
                if mid:
                    first_machine_id = str(mid)
    except httpx.RequestError:
        # Pas bloquant : on fallback sur la vue "sites" côté JS
        first_machine_id = None

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "first_machine_id": first_machine_id,
            "title": "NeonMonitor",
            "version_cache_bust": VERSION_CACHE_BUST,
        },
    )


@app.post("/logout", name="logout")
async def logout(request: Request):
    # On tente d’appeler l’API pour qu’elle émette les Set-Cookie de suppression
    api_resp = None
    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0, follow_redirects=False) as client:
            api_resp = await client.post("/api/v1/auth/logout")
    except httpx.RequestError:
        api_resp = None

    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    if api_resp is not None and api_resp.status_code == 200:
        # ✅ propage les en-têtes Set-Cookie renvoyés par l’API (delete_cookie)
        for cookie in api_resp.headers.get_list("set-cookie"):
            redirect.headers.append("set-cookie", cookie)
    else:
        # ✅ fallback local si l’API est indisponible
        redirect.delete_cookie(settings.ACCESS_COOKIE, path="/")
        redirect.delete_cookie(settings.REFRESH_COOKIE, path="/")

    return redirect


# ── Fragments ────────────────────────────────────────────────────────────────

@app.get("/fragment/sites", response_class=HTMLResponse)
@login_required
async def fragment_sites(request: Request):
    """
    Liste les sites monitorés.
    Appelle l’API backend : GET /api/v1/http-targets
    """
    ctx = {"request": request, "version_cache_bust": VERSION_CACHE_BUST, "sites": []}

    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
            r = await client.get(
                "/api/v1/http-targets",
                headers={"X-API-Key": _get_dev_api_key()},
            )
            # print("LIST STATUS =", r.status_code)
            # print("LIST BODY =", r.text)
            if r.status_code == 200:
                ctx["sites"] = r.json()
    except httpx.RequestError:
        ctx["sites"] = []

    return templates.TemplateResponse("fragments/sites.html", ctx)


@app.get("/fragment/machines", response_class=HTMLResponse)
@login_required
async def fragment_machines(request: Request):
    ctx = {"request": request, "version_cache_bust": VERSION_CACHE_BUST}

    # 1) Charger la liste
    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
            r = await client.get("/api/v1/machines", headers={"X-API-Key": _get_dev_api_key()})
        machines = r.json() if r.status_code == 200 else []
    except httpx.RequestError as exc:
        logger.error("Erreur httpx vers API /api/v1/machines : %s", exc)
        machines = []

    if not machines:
        return templates.TemplateResponse("fragments/no_machine.html", ctx)

    # 2) Charger le détail de la 1ère machine
    first_id = (machines[0] or {}).get("id")
    if not first_id:
        return templates.TemplateResponse("fragments/no_machine.html", ctx)

    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
            detail = await client.get(f"/api/v1/machines/{first_id}/detail", headers={"X-API-Key": _get_dev_api_key()})
        if detail.status_code != 200:
            return templates.TemplateResponse("fragments/no_machine.html", ctx)

        payload = detail.json() or {}
    except httpx.RequestError as exc:
        logger.error("Erreur httpx vers machine detail : %s", exc)
        return templates.TemplateResponse("fragments/no_machine.html", ctx)

    # 3) Contexte complet pour une page "split"
    ctx["all_machines"] = machines
    ctx["current_machine"] = payload.get("machine")
    ctx["metrics"] = payload.get("metrics") or []
    ctx["services"] = payload.get("services") or []

    # ✅ important : on rend machines.html (et plus machine_detail.html)
    return templates.TemplateResponse("fragments/machines.html", ctx)

@app.get("/fragment/machine/{machine_id}", response_class=HTMLResponse)
@login_required
async def fragment_machine_detail(request: Request, machine_id: str):
    """
    Détails machine (host + métriques).
    Consomme /api/v1/machines/{id}/detail (protégé X-API-Key).
    """
    ctx = {"request": request, "version_cache_bust": VERSION_CACHE_BUST}

    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
            machines_resp = await client.get(
                "/api/v1/machines",
                headers={"X-API-Key": _get_dev_api_key()},
            )

            machines = machines_resp.json() if machines_resp.status_code == 200 else []

            detail_resp = await client.get(
                f"/api/v1/machines/{machine_id}/detail",
                headers={"X-API-Key": _get_dev_api_key()},
            )

        if detail_resp.status_code != 200:
            logger.info("GET /machines/%s/detail -> %s %s", machine_id, detail_resp.status_code, detail_resp.text[:300])
            return templates.TemplateResponse("fragments/no_machine.html", ctx)

        detail = detail_resp.json()

    except httpx.RequestError as exc:
        logger.error("Erreur httpx vers API machine detail : %s", exc)
        return templates.TemplateResponse("fragments/no_machine.html", ctx)

    ctx["all_machines"] = machines
    ctx["current_machine"] = detail.get("machine")
    ctx["metrics"] = detail.get("metrics", [])
    ctx["services"] = detail.get("services") or []

    return templates.TemplateResponse("fragments/machine_detail_inner.html", ctx)


@app.get("/fragment/settings", response_class=HTMLResponse)
@login_required
async def fragment_settings(request: Request):
    """
    Paramètres client (notifications, etc.).
    API : GET /api/v1/settings → { ... }
    """

    default_cfg = {
        "email": "",
        "slack": "",
        "slack_channel_name": "",
        "grace_minutes": 0,
        "reminder_interval": 10,
        "group_alerts": False,
        "suppress_resolution_alert": False,
    }

    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
            r = await client.get(
                "/api/v1/settings",
                headers={"X-API-Key": _get_dev_api_key()},
            )
            if r.status_code == 200:
                s = r.json()
                cfg = {
                    "email": s.get("notification_email") or "",
                    "slack": s.get("slack_webhook_url") or "",
                    "slack_channel_name": s.get("slack_channel_name") or "",
                    # secondes → minutes (arrondi vers le bas)
                    "grace_minutes": int((s.get("grace_period_seconds") or 0) / 60),
                    "reminder_interval": int((s.get("reminder_notification_seconds") or 0) / 60),
                    "group_alerts": bool(s.get("alert_grouping_enabled", False)),
                    # DB = notify_on_resolve → case à cocher "ne PAS recevoir"
                    "suppress_resolution_alert": not bool(s.get("notify_on_resolve", True)),
                }
            else:
                cfg = default_cfg
    except httpx.RequestError:
        cfg = default_cfg

    return templates.TemplateResponse(
        "fragments/settings.html",
        {
            "request": request,
            "alert_config": cfg,
            "version_cache_bust": VERSION_CACHE_BUST
        },
    )


@app.get("/fragment/events", response_class=HTMLResponse)
@login_required
async def fragment_events(request: Request):
    """
    Historique des événements (incidents + notifications) pour le client courant.
    - Appelle l'API backend :
      - GET /api/v1/incidents
      - GET /api/v1/notifications
    - Fusionne en une seule liste triée par date décroissante.
    """
    ctx = {
        "request": request,
        "version_cache_bust": VERSION_CACHE_BUST,
        "events": [],
    }

    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
            incidents_resp, notif_resp, machines_resp = await asyncio.gather(
                client.get("/api/v1/incidents", headers={"X-API-Key": _get_dev_api_key()}),
                client.get("/api/v1/notifications", headers={"X-API-Key": _get_dev_api_key()}),
                client.get("/api/v1/machines", headers={"X-API-Key": _get_dev_api_key()}),
            )

        incidents = incidents_resp.json() if incidents_resp.status_code == 200 else []
        notifs = notif_resp.json() if notif_resp.status_code == 200 else []
        machines = machines_resp.json() if machines_resp.status_code == 200 else []

        machine_name_by_id = {
            m.get("id"): (m.get("hostname") or m.get("id"))
            for m in machines
            if m.get("id")
        }

        # On construit une liste d'évènements unifiés
        events: list[dict] = []

        for inc in incidents:

            created_ts = inc.get("created_at")
            resolved_ts = inc.get("resolved_at")
            created_dt = _parse_iso(created_ts)
            resolved_dt = _parse_iso(resolved_ts)

            duration_sec = None
            if created_dt and resolved_dt:
                duration_sec = int((resolved_dt - created_dt).total_seconds())

            events.append(
                {
                    "kind": "incident",
                    "id": inc["id"],
                    "timestamp": inc["created_at"],   # string ISO → facile à trier
                    "title": inc["title"],
                    "status": "ouvert" if inc["status"] == "OPEN" else "resolu",
                    "severity": inc["severity"],
                    "machine_id": inc.get("machine_id"),
                    "machine_name": machine_name_by_id.get(inc.get("machine_id")),
                    "resolved_at": inc.get("resolved_at"),
                    "duration": _human_duration(duration_sec),
                    "description": inc.get("description"),  # si tu l’ajoutes dans l’endpoint
                }
            )

        for n in notifs:

            delivery_status = (n.get("status") or "pending").lower()
            severity = (n.get("severity") or "").lower() or None

            ts = n.get("sent_at") or n.get("created_at")
            events.append(
                {
                    "kind": "notification",
                    "id": n["id"],
                    "timestamp": ts,
                    "provider": n["provider"],
                    "recipient": n["recipient"],
                    "status": delivery_status,   # success/failed/skipped_*/pending
                    "severity": severity,        # info/warning/error/critical ou None
                    "message": n.get("message"),
                    "error_message": n.get("error_message"),
                    "incident_id": n.get("incident_id"),
                }
            )
        
        for m in machines:
            mid = m.get("id")
            if not mid:
                continue

            hostname = m.get("hostname") or mid
            reg = m.get("registered_at")
            unreg = m.get("unregistered_at")
            is_active = bool(m.get("is_active", True))

            if reg:
                events.append({
                    "kind": "machine",
                    "subkind": "registered",
                    "id": f"{mid}:registered",
                    "timestamp": reg,
                    "title": f"Machine enregistrée : {hostname}",
                    "machine_id": mid,
                    "machine_name": hostname,
                    "status": "info",
                    "severity": "info",
                })

            # Désenregistrement : montre-le seulement si la machine est inactive (ou si tu veux toujours l’afficher)
            if unreg and (not is_active):
                events.append({
                    "kind": "machine",
                    "subkind": "unregistered",
                    "id": f"{mid}:unregistered",
                    "timestamp": unreg,
                    "title": f"Machine désenregistrée : {hostname}",
                    "machine_id": mid,
                    "machine_name": hostname,
                    "status": "info",
                    "severity": "warning",
                })


        # Tri déchronologique (timestamp ISO → OK pour trier en string)
        events.sort(key=lambda e: (e["timestamp"] or ""), reverse=True)

        ctx["events"] = events

    except httpx.RequestError:
        ctx["events"] = []

    return templates.TemplateResponse("fragments/events.html", ctx)


# ── Proxy HTTP (webapp → backend API) ─────────────────────────────────────────

@app.post("/webapi/http-targets")
@login_required
async def proxy_create_target(request: Request):
    """Proxy interne : relaye POST vers l’API /api/v1/http-targets avec X-API-Key."""
    payload = await request.json()
    payload.setdefault("name", payload.get("url"))
    payload.setdefault("method", "GET")
    payload.setdefault("timeout_seconds", 5)
    payload.setdefault("check_interval_seconds", 60)
    payload.setdefault("is_active", True)

    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        r = await client.post(
            "/api/v1/http-targets",
            json=payload,
            headers={"X-API-Key": _get_dev_api_key()},
        )
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


@app.delete("/webapi/http-targets/{target_id}")
@login_required
async def proxy_delete_target(request: Request, target_id: str):
    """Relaye DELETE vers le backend API /api/v1/http-targets/{id}"""
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        r = await client.delete(
            f"/api/v1/http-targets/{target_id}",
            headers={"X-API-Key": _get_dev_api_key()},
        )
    return Response(content=r.content, status_code=r.status_code)


@app.patch("/webapi/http-targets/{target_id}")
@login_required
async def proxy_patch_target(request: Request, target_id: str):
    payload = await request.json()  # ex: {"is_active": false}

    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        # 1) on tente PATCH côté API
        r = await client.patch(
            f"/api/v1/http-targets/{target_id}",
            json=payload,
            headers={"X-API-Key": _get_dev_api_key()},
        )
        # 2) fallback si l’API n’expose que PUT
        if r.status_code == 405:
            r = await client.put(
                f"/api/v1/http-targets/{target_id}",
                json=payload,
                headers={"X-API-Key": _get_dev_api_key()},
            )

    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


@app.post("/webapi/auth/refresh", name="web_refresh")
async def proxy_refresh(request: Request):
    """
    Proxy WebApp → API : tente un refresh via /api/v1/auth/refresh-cookie
    et RELAY tous les Set-Cookie au navigateur.
    """
    async with httpx.AsyncClient(base_url=API_BASE, timeout=5.0, cookies=request.cookies) as client:
        r = await client.post("/api/v1/auth/refresh-cookie")
    if r.status_code != 200:
        return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))

    resp = Response(content=r.content, status_code=200, media_type=r.headers.get("content-type"))
    for cookie in r.headers.get_list("set-cookie"):
        resp.headers.append("set-cookie", cookie)
    return resp


@app.put("/webapi/settings")
@login_required
async def proxy_update_settings(request: Request):
    """
    Proxy WebApp → API pour la mise à jour des paramètres client.
    Relaye le PUT vers /api/v1/settings avec l'API key dev.
    """
    payload = await request.json()

    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        r = await client.put(
            "/api/v1/settings",
            json=payload,
            headers={"X-API-Key": _get_dev_api_key()},
        )

    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type"),
    )


@app.post("/webapi/metrics/{metric_instance_id}/thresholds/default")
@login_required
async def web_upsert_default_threshold(request: Request, metric_instance_id: str):
    """
    Proxy webapp → API
    Transmet le body JSON tel quel vers l'API backend.
    """
    # ✅ CORRECTION : Lire le body brut et le transmettre tel quel
    body_bytes = await request.body()
    content_type = request.headers.get("content-type", "application/json")
    
    logger.info("THRESHOLD proxy: Content-Type=%s, body=%s", content_type, body_bytes.decode('utf-8'))

    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        r = await client.post(
            f"/api/v1/metrics/{metric_instance_id}/thresholds/default",
            content=body_bytes,  # ✅ Transmet le body brut
            headers={
                "X-API-Key": _get_dev_api_key(),
                "Content-Type": content_type,  # ✅ Transmet le Content-Type
            },
        )

    # Renvoie la réponse de l'API
    try:
        payload = r.json()
    except Exception:
        payload = {"success": False, "detail": r.text}

    return JSONResponse(payload, status_code=r.status_code)


@app.post("/webapi/metrics/{metric_instance_id}/alerting")
@login_required
async def web_toggle_alerting(request: Request, metric_instance_id: str):
    """
    Proxy webapp → API
    Form POST → API PATCH /alerting (car un <form> ne sait pas PATCH).
    """
    form = await request.form()
    raw = form.get("alert_enabled")

    alert_enabled = str(raw).strip().lower() in {"1", "true", "on", "yes", "y", "t"}

    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        r = await client.patch(
            f"/api/v1/metrics/{metric_instance_id}/alerting",
            json={"alert_enabled": alert_enabled},
            headers={"X-API-Key": _get_dev_api_key()},
        )

    try:
        payload = r.json()
    except Exception:
        payload = {"success": False, "detail": r.text}

    return JSONResponse(payload, status_code=r.status_code)


@app.post("/webapi/metrics/{metric_instance_id}/pause")
@login_required
async def web_toggle_pause(request: Request, metric_instance_id: str):
    """
    Proxy webapp → API
    Form POST → API PATCH /pause
    """
    form = await request.form()
    raw = form.get("paused")

    paused = str(raw).strip().lower() in {"1", "true", "on", "yes", "y", "t"}

    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        r = await client.patch(
            f"/api/v1/metrics/{metric_instance_id}/pause",
            json={"paused": paused},
            headers={"X-API-Key": _get_dev_api_key()},
        )

    try:
        payload = r.json()
    except Exception:
        payload = {"success": False, "detail": r.text}

    return JSONResponse(payload, status_code=r.status_code)