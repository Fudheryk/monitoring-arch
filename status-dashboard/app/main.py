# status-dashboard/app/main.py
"""
Status Dashboard (FastAPI)
=========================

Objectif:
- Afficher un dashboard léger (host + conteneurs Docker)
- Éviter les 502 / timeouts nginx en PROD

Optimisations clés:
- Cache TTL (ex: 2s) : évite de recalculer les stats à chaque refresh
- Parallélisation (ThreadPool) des appels Docker `stats()` (très lents en série)
- Timeouts / fallbacks : si Docker ne répond pas, la page s'affiche quand même
"""

import os
import time
import shutil
from pathlib import Path
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed

import docker
import psutil

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


# =============================================================================
# App setup
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent.parent  # /app dans le container

app = FastAPI(title="Status Dashboard")

# NOTE:
# - Le dashboard est servi derrière nginx sous /status/
# - Le CSS est référencé dans le template via /status/static/style.css
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# =============================================================================
# Tuning / config (via env)
# =============================================================================

# Cache TTL: 2s par défaut (suffisant pour un dashboard)
CACHE_TTL_SECONDS = float(os.getenv("STATUS_CACHE_TTL", "2.0"))

# Nombre de threads pour paralléliser les appels Docker stats()
# (8 est un bon compromis sur un petit VPS)
DOCKER_MAX_WORKERS = int(os.getenv("STATUS_DOCKER_WORKERS", "8"))

# CPU percent:
# - interval=0.3 bloque 300ms minimum -> OK mais coûteux
# - interval=None est instantané mais moins "réel"
CPU_PERCENT_INTERVAL = float(os.getenv("STATUS_CPU_INTERVAL", "0.0"))  # 0.0 = non bloquant


# =============================================================================
# Cache in-memory (simple et efficace)
# =============================================================================

_CACHE_LOCK = Lock()
_CACHE = {
    "ts": 0.0,
    "response": None,  # TemplateResponse
}


# =============================================================================
# Utils
# =============================================================================

def bytes_to_human(n: int | float | None) -> str:
    if n is None:
        return "-"
    n = float(n)
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < step:
            return f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} PB"


def safe_get(d: dict, path: list, default=None):
    """
    Safe nested dict getter.
    Example: safe_get(s, ["cpu_stats","cpu_usage","total_usage"], 0)
    """
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def compute_container_row(c) -> dict:
    """
    Calcule les stats d'un conteneur.
    IMPORTANT: `c.stats(stream=False)` est très lent -> parallélisé.
    """
    image = (c.image.tags[0] if c.image.tags else c.image.short_id)

    row = {
        "name": c.name,
        "status": c.status,
        "image": image,

        "cpu": 0.0,

        "mem_percent": 0.0,
        "mem_usage": None,
        "mem_limit": None,

        "net_rx": 0,
        "net_tx": 0,

        "pids": None,
        "health": None,  # healthy/unhealthy/starting/None
    }

    # -----------------------
    # Stats (CPU/MEM/NET/PIDs)
    # -----------------------
    try:
        s = c.stats(stream=False)

        cpu_total = safe_get(s, ["cpu_stats", "cpu_usage", "total_usage"], 0) or 0
        precpu_total = safe_get(s, ["precpu_stats", "cpu_usage", "total_usage"], 0) or 0
        sys_total = safe_get(s, ["cpu_stats", "system_cpu_usage"], 0) or 0
        presys_total = safe_get(s, ["precpu_stats", "system_cpu_usage"], 0) or 0

        cpu_delta = cpu_total - precpu_total
        sys_delta = sys_total - presys_total

        percpu = safe_get(s, ["cpu_stats", "cpu_usage", "percpu_usage"], None) or []
        cpu_count = len(percpu) or 1

        if sys_delta > 0:
            row["cpu"] = (cpu_delta / sys_delta) * cpu_count * 100.0

        mem_usage = safe_get(s, ["memory_stats", "usage"], 0) or 0
        mem_limit = safe_get(s, ["memory_stats", "limit"], 0) or 0

        row["mem_usage"] = mem_usage
        row["mem_limit"] = mem_limit
        row["mem_percent"] = (mem_usage / mem_limit) * 100.0 if mem_limit else 0.0

        networks = s.get("networks") or {}
        for _, v in networks.items():
            row["net_rx"] += int(v.get("rx_bytes", 0) or 0)
            row["net_tx"] += int(v.get("tx_bytes", 0) or 0)

        row["pids"] = (s.get("pids_stats") or {}).get("current")

    except Exception:
        # Docker peut timeout / refuser / conteneur mort -> on garde valeurs par défaut
        pass

    # -----------------------
    # Healthcheck (via attrs)
    # -----------------------
    # NOTE:
    # - `c.attrs` peut déclencher un appel Docker API
    # - Ici tu as mesuré ~0ms, mais on garde le try/except pour être safe
    try:
        attrs = c.attrs
        health_obj = ((attrs.get("State") or {}).get("Health") or {})
        row["health"] = health_obj.get("Status")
    except Exception:
        row["health"] = None

    return row


# =============================================================================
# Routes
# =============================================================================

@app.get("/", response_class=HTMLResponse)
def status_page(request: Request):
    """
    Page principale.
    - Utilise un cache TTL pour éviter de recalculer en boucle
    - Parallélise les stats Docker
    """

    now_ts = time.time()

    # -----------------------
    # Cache TTL
    # -----------------------
    with _CACHE_LOCK:
        if _CACHE["response"] is not None and (now_ts - _CACHE["ts"]) < CACHE_TTL_SECONDS:
            return _CACHE["response"]

    # -----------------------
    # HOST metrics (rapide)
    # -----------------------
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # IMPORTANT:
    # - interval=0.3 => bloque ~300ms (mais "plus stable")
    # - interval=0.0 => quasi instantané (recommandé pour dashboard)
    cpu_percent = psutil.cpu_percent(interval=CPU_PERCENT_INTERVAL)

    load1, load5, load15 = psutil.getloadavg()
    disk = shutil.disk_usage("/")
    net = psutil.net_io_counters()
    diskio = psutil.disk_io_counters()

    # -----------------------
    # DOCKER metrics (lent)
    # -----------------------
    rows = []
    docker_error = None

    try:
        client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        containers = client.containers.list(all=True)

        # Parallélisation des stats() -> gain énorme
        with ThreadPoolExecutor(max_workers=DOCKER_MAX_WORKERS) as ex:
            futures = [ex.submit(compute_container_row, c) for c in containers]
            for f in as_completed(futures):
                rows.append(f.result())

        # Tri stable (UX)
        rows.sort(key=lambda x: x["name"])

    except Exception as e:
        docker_error = str(e)
        rows = []

    # -----------------------
    # Template context
    # -----------------------
    context = {
        "request": request,
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": {
            "cpu_percent": cpu_percent,
            "load": (load1, load5, load15),

            "mem_used": bytes_to_human(vm.used),
            "mem_total": bytes_to_human(vm.total),
            "mem_percent": vm.percent,

            "swap_used": bytes_to_human(swap.used),
            "swap_total": bytes_to_human(swap.total),
            "swap_percent": swap.percent,

            "disk_used": bytes_to_human(disk.used),
            "disk_total": bytes_to_human(disk.total),
            "disk_percent": (disk.used / disk.total) * 100.0 if disk.total else 0.0,

            "disk_read": bytes_to_human(getattr(diskio, "read_bytes", 0) if diskio else 0),
            "disk_write": bytes_to_human(getattr(diskio, "write_bytes", 0) if diskio else 0),

            "net_rx": bytes_to_human(getattr(net, "bytes_recv", 0) if net else 0),
            "net_tx": bytes_to_human(getattr(net, "bytes_sent", 0) if net else 0),
        },
        "containers": rows,
        "docker_error": docker_error,  # optionnel: afficher un message si Docker KO
        "fmt": {
            "bytes_to_human": bytes_to_human,  # pour réutiliser dans le template
        },
    }

    resp = templates.TemplateResponse("index.html", context)

    # -----------------------
    # Store in cache
    # -----------------------
    with _CACHE_LOCK:
        _CACHE["ts"] = now_ts
        _CACHE["response"] = resp

    return resp


@app.get("/health")
def health():
    # Healthcheck minimal (pour docker-compose)
    return {"status": "ok"}
