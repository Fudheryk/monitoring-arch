from __future__ import annotations
"""
webapp/app/main.py
ASGI de la WebApp (login).

Fonctions principales :
- Sert les templates + static
- Appelle l'API backend /api/v1/auth/login
- Propage les Set-Cookie de l'API (access/refresh)
- Expose /_health (healthcheck Docker) et /health
- Page / protégée via @login_required (voir app/auth_guard.py)

Gestion de version automatique :
- Version complète récupérée depuis app.version (git + build info)
- Cache busting via GIT_COMMIT pour les assets statiques
"""

from pathlib import Path
import httpx
import os
import asyncio
import logging
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Form, status, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.auth_guard import login_required  # décorateur qui vérifie l'auth (cookies) côté webapp
from app.config import get_settings         # config centralisée (API_BASE_URL, etc.)

# =============================================================================
# IMPORT DE LA VERSION AUTOMATIQUE
# =============================================================================
try:
    # Import relatif depuis le même package (app.version)
    from .version import APP_VERSION, GIT_COMMIT, BUILD_TIMESTAMP, VERSION_CACHE_BUST
    logger = logging.getLogger(__name__)
    logger.info(f"✓ Version chargée : {APP_VERSION} (commit: {GIT_COMMIT})")
except ImportError as e:
    # Fallback pour développement ou erreur d'import
    logger = logging.getLogger(__name__)
    logger.warning(f"⚠ Impossible d'importer app.version : {e}. Utilisation des valeurs par défaut.")
    
    APP_VERSION = "1.0.0+dev.local"
    GIT_COMMIT = os.getenv("GIT_COMMIT", "dev")
    BUILD_TIMESTAMP = datetime.utcnow().isoformat() + "Z"
    VERSION_CACHE_BUST = GIT_COMMIT

# Chargement de la configuration (env/.env)
settings = get_settings()

# =============================================================================
# FONCTIONS UTILITAIRES
# =============================================================================

def _parse_iso(ts: str | None) -> datetime | None:
    """
    Parse ISO8601 "backend-like" (avec ou sans Z).
    Retourne un datetime timezone-aware UTC si possible.
    
    Args:
        ts: Timestamp ISO8601 (ex: "2024-01-14T10:30:00Z")
    
    Returns:
        datetime en UTC ou None en cas d'erreur
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
        logger.warning(f"Erreur de parsing ISO pour: {ts}")
        return None


def _human_duration(seconds: int | None) -> str | None:
    """
    Convertit une durée en secondes en format humain lisible.
    
    Examples:
        65  → "1m 05s"
        3605 → "1h 00m"
        90061 → "1j 1h"
    
    Args:
        seconds: Durée en secondes
    
    Returns:
        Chaîne formatée ou None si négative
    """
    if seconds is None or seconds < 0:
        return None

    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)

    if days > 0:
        # Format compact : jours + heures
        return f"{days}j {hours}h"
    if hours > 0:
        return f"{hours}h {mins:02d}m"
    if mins > 0:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


def _get_dev_api_key() -> str:
    """Récupère la clé API pour le développement."""
    return getattr(settings, "DEV_API_KEY", None) or os.getenv("DEV_API_KEY") or "dev-apikey-123"


def _get_prod_api_key() -> str:
    """Récupère la clé API pour la production."""
    return getattr(settings, "API_KEY", None) or os.getenv("API_KEY") or "prod-apikey-xxxxxxxxxx"


# =============================================================================
# LIFECYCLE DE L'APPLICATION
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gestion du cycle de vie de l'application FastAPI.
    
    Startup:
        - Log des informations de version
        - Log de la configuration cookies
        - Initialisation des clients HTTP globaux (optionnel)
    
    Shutdown:
        - Nettoyage des ressources
    """
    # Startup
    logger.info(f"🚀 Démarrage de NeonMonitor Web {APP_VERSION}")
    logger.info(f"📦 Commit: {GIT_COMMIT}, Build: {BUILD_TIMESTAMP}")
    logger.info(f"🍪 Cookies config: ACCESS={settings.ACCESS_COOKIE} REFRESH={settings.REFRESH_COOKIE}")
    logger.info(f"🌐 API base URL: {settings.API_BASE_URL}")
    
    # (ici: ouvrir des connexions, clients http globaux, etc.)
    yield
    
    # Shutdown
    logger.info("👋 Arrêt de NeonMonitor Web")


# =============================================================================
# INITIALISATION DE L'APPLICATION FASTAPI
# =============================================================================

app = FastAPI(
    title="NeonMonitor Web",
    description="Interface web de monitoring avec gestion automatique de version",
    version=APP_VERSION,  # Version automatique via app.version
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("ENVIRONMENT") != "production" else None,
    redoc_url="/redoc" if os.getenv("ENVIRONMENT") != "production" else None,
)

API_BASE = settings.API_BASE_URL.rstrip("/")

# =============================================================================
# CONFIGURATION DES STATICS ET TEMPLATES
# =============================================================================

BASE = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# Injection des variables globales dans tous les templates
templates.env.globals.update({
    "app_version": APP_VERSION,
    "git_commit": GIT_COMMIT,
    "build_time": BUILD_TIMESTAMP,
    "cache_bust": VERSION_CACHE_BUST,
    "current_year": datetime.now().year,
})

# =============================================================================
# HEALTHCHECKS (pour Docker/compose)
# =============================================================================

@app.get("/_health")
@app.get("/health")
def health():
    """
    Endpoint de healthcheck utilisé par Docker/Orchestrateur.
    
    Returns:
        JSON avec statut et informations de version
    """
    return {
        "status": "ok",
        "service": "neonmonitor-web",
        "version": APP_VERSION,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


# =============================================================================
# PAGES D'AUTHENTIFICATION
# =============================================================================

@app.api_route("/login", methods=["GET", "HEAD"], response_class=HTMLResponse, name="login_page")
def login_page(request: Request, error: str | None = None):
    """
    Page de login (GET/HEAD).
    
    Args:
        request: Requête FastAPI
        error: Message d'erreur optionnel
    
    Returns:
        Template HTML de login
    """
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "title": "Connexion",
            "error": error,
        },
    )


@app.post("/login", name="login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """
    Soumission du formulaire de login.
    
    Processus:
        1. Appel du backend API (serveur→serveur)
        2. Récupération des cookies HttpOnly
        3. Propagation des cookies au navigateur
        4. Redirection vers la page protégée
    
    Args:
        request: Requête FastAPI
        email: Email de l'utilisateur
        password: Mot de passe
    
    Returns:
        Redirection avec cookies ou page d'erreur
    """
    # Appel backend API (serveur→serveur) : pas de CORS
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0, follow_redirects=False) as client:
        try:
            resp = await client.post(
                "/api/v1/auth/login", 
                json={"email": email, "password": password}
            )
        except httpx.RequestError:
            # API down / réseau KO → 503 côté webapp
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request, 
                    "error": "API indisponible, réessayez."
                },
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    if resp.status_code != 200:
        # 401 attendu si mauvais identifiants
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request, 
                "error": "Identifiants invalides."
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Succès → redirection vers la page protégée "/"
    redirect = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    # Propage TOUS les Set-Cookie renvoyés par l'API (access_token / refresh_token)
    for cookie in resp.headers.get_list("set-cookie"):
        # Starlette permet d'ajouter plusieurs "set-cookie" dans la réponse
        redirect.headers.append("set-cookie", cookie)

    return redirect


# =============================================================================
# PAGES PROTÉGÉES
# =============================================================================

@app.get("/", response_class=HTMLResponse, name="home")
@login_required
async def home(request: Request):
    """
    Page d'accueil protégée (dashboard).
    
    Features:
        - Charge la première machine pour pré-rendu
        - Permet au JS de charger directement sans fragment supplémentaire
    
    Args:
        request: Requête FastAPI avec utilisateur authentifié
    
    Returns:
        Template HTML du dashboard
    """
    user = getattr(request.state, "user", None)  # défini par le guard si présent

    # ✅ Permet au dashboard de charger directement la 1ère machine
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
        },
    )


@app.post("/logout", name="logout")
async def logout(request: Request):
    """
    Déconnexion de l'utilisateur.
    
    Processus:
        1. Appel API pour suppression des cookies côté backend
        2. Propagation des Set-Cookie de suppression
        3. Fallback local si API indisponible
    
    Args:
        request: Requête FastAPI
    
    Returns:
        Redirection vers la page de login
    """
    # Tente d'appeler l'API pour qu'elle émette les Set-Cookie de suppression
    api_resp = None
    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0, follow_redirects=False) as client:
            api_resp = await client.post("/api/v1/auth/logout")
    except httpx.RequestError:
        api_resp = None

    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    if api_resp is not None and api_resp.status_code == 200:
        # ✅ propage les en-têtes Set-Cookie renvoyés par l'API (delete_cookie)
        for cookie in api_resp.headers.get_list("set-cookie"):
            redirect.headers.append("set-cookie", cookie)
    else:
        # ✅ fallback local si l'API est indisponible
        redirect.delete_cookie(settings.ACCESS_COOKIE, path="/")
        redirect.delete_cookie(settings.REFRESH_COOKIE, path="/")

    return redirect


# =============================================================================
# FRAGMENTS (chargés dynamiquement via fetch() JS)
# =============================================================================

@app.get("/fragment/sites", response_class=HTMLResponse)
@login_required
async def fragment_sites(request: Request):
    """
    Fragment: Liste des sites monitorés.
    
    Appelle l'API backend: GET /api/v1/http-targets
    
    Args:
        request: Requête FastAPI
    
    Returns:
        Template HTML du fragment sites
    """
    ctx = {"request": request, "sites": []}

    try:
        async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
            r = await client.get(
                "/api/v1/http-targets",
                headers={"X-API-Key": _get_dev_api_key()},
            )
            if r.status_code == 200:
                ctx["sites"] = r.json()
    except httpx.RequestError:
        ctx["sites"] = []

    return templates.TemplateResponse("fragments/sites.html", ctx)


@app.get("/fragment/machines", response_class=HTMLResponse)
@login_required
async def fragment_machines(request: Request):
    """
    Fragment: Liste des machines avec détail de la première.
    
    Processus:
        1. Charge la liste des machines
        2. Charge le détail de la première machine
        3. Renvoie le template machines.html (split view)
    
    Args:
        request: Requête FastAPI
    
    Returns:
        Template HTML du fragment machines
    """
    ctx = {"request": request}

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
            detail = await client.get(
                f"/api/v1/machines/{first_id}/detail", 
                headers={"X-API-Key": _get_dev_api_key()}
            )
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
    Fragment: Détails d'une machine spécifique.
    
    Consomme /api/v1/machines/{id}/detail (protégé X-API-Key).
    
    Args:
        request: Requête FastAPI
        machine_id: ID de la machine
    
    Returns:
        Template HTML du détail machine
    """
    ctx = {"request": request}

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
            logger.info("GET /machines/%s/detail -> %s %s", 
                       machine_id, detail_resp.status_code, detail_resp.text[:300])
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
    Fragment: Paramètres client (notifications, etc.).
    
    API: GET /api/v1/settings → { ... }
    
    Args:
        request: Requête FastAPI
    
    Returns:
        Template HTML des paramètres
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
        },
    )


@app.get("/fragment/events", response_class=HTMLResponse)
@login_required
async def fragment_events(request: Request):
    """
    Fragment: Historique des événements (incidents + notifications).
    
    Processus:
        1. Appelle l'API backend:
            - GET /api/v1/incidents
            - GET /api/v1/notifications
            - GET /api/v1/machines
        2. Fusionne en une seule liste triée par date décroissante
    
    Args:
        request: Requête FastAPI
    
    Returns:
        Template HTML des événements
    """
    ctx = {
        "request": request,
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

        # Construction d'une liste d'événements unifiés
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
                    "description": inc.get("description"),
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


# =============================================================================
# PROXY HTTP (webapp → backend API)
# =============================================================================

@app.post("/webapi/http-targets")
@login_required
async def proxy_create_target(request: Request):
    """Proxy interne : relaye POST vers l'API /api/v1/http-targets avec X-API-Key."""
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
    return Response(
        content=r.content, 
        status_code=r.status_code, 
        media_type=r.headers.get("content-type")
    )


@app.delete("/webapi/http-targets/{target_id}")
@login_required
async def proxy_delete_target(request: Request, target_id: str):
    """Relaye DELETE vers le backend API /api/v1/http-targets/{id}"""
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        r = await client.delete(
            f"/api/v1/http-targets/{target_id}",
            headers={"X-API-Key": _get_dev_api_key()},
        )
    return Response(
        content=r.content, 
        status_code=r.status_code
    )


@app.patch("/webapi/http-targets/{target_id}")
@login_required
async def proxy_patch_target(request: Request, target_id: str):
    """Relaye PATCH/PUT vers l'API /api/v1/http-targets/{id}"""
    payload = await request.json()

    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        # 1) on tente PATCH côté API
        r = await client.patch(
            f"/api/v1/http-targets/{target_id}",
            json=payload,
            headers={"X-API-Key": _get_dev_api_key()},
        )
        # 2) fallback si l'API n'expose que PUT
        if r.status_code == 405:
            r = await client.put(
                f"/api/v1/http-targets/{target_id}",
                json=payload,
                headers={"X-API-Key": _get_dev_api_key()},
            )

    return Response(
        content=r.content, 
        status_code=r.status_code, 
        media_type=r.headers.get("content-type")
    )


@app.post("/webapi/auth/refresh", name="web_refresh")
async def proxy_refresh(request: Request):
    """
    Proxy WebApp → API : tente un refresh via /api/v1/auth/refresh-cookie
    et RELAY tous les Set-Cookie au navigateur.
    """
    async with httpx.AsyncClient(base_url=API_BASE, timeout=5.0, cookies=request.cookies) as client:
        r = await client.post("/api/v1/auth/refresh-cookie")
    
    if r.status_code != 200:
        return Response(
            content=r.content, 
            status_code=r.status_code, 
            media_type=r.headers.get("content-type")
        )

    resp = Response(
        content=r.content, 
        status_code=200, 
        media_type=r.headers.get("content-type")
    )
    
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
    Proxy webapp → API pour la définition de seuils.
    
    Args:
        request: Requête FastAPI
        metric_instance_id: ID de l'instance de métrique
    
    Returns:
        Réponse JSON de l'API backend
    """
    # ✅ CORRECTION : Lire le body brut et le transmettre tel quel
    body_bytes = await request.body()
    content_type = request.headers.get("content-type", "application/json")
    
    logger.debug("THRESHOLD proxy: Content-Type=%s, body=%s", content_type, body_bytes.decode('utf-8'))

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
    Proxy webapp → API pour activer/désactiver les alertes.
    
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
    Proxy webapp → API pour mettre en pause/reprendre une métrique.
    
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


# # =============================================================================
# # POINT D'ENTRÉE
# # =============================================================================
# if __name__ == "__main__":
#     import uvicorn
#     logger.info(f"🌐 Démarrage en standalone: http://localhost:3000")
#     uvicorn.run(
#         "app.main:app",
#         host="0.0.0.0",
#         port=3000,
#         reload=os.getenv("ENVIRONMENT") == "development",
#         log_level="info"
#     )