# status-dashboard/app/main.py
import time
from pathlib import Path

import docker
import psutil
import shutil

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent  # /app dans le container

app = FastAPI()

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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


@app.get("/", response_class=HTMLResponse)
def status_page(request: Request):
    # HOST
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    cpu_percent = psutil.cpu_percent(interval=0.3)
    load1, load5, load15 = psutil.getloadavg()
    disk = shutil.disk_usage("/")
    net = psutil.net_io_counters()
    diskio = psutil.disk_io_counters()

    # DOCKER
    client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
    containers = client.containers.list(all=True)

    rows = []
    for c in containers:
        image = (c.image.tags[0] if c.image.tags else c.image.short_id)

        cpu = 0.0

        mem_percent = 0.0
        mem_usage = None
        mem_limit = None

        net_rx = 0
        net_tx = 0

        pids_current = None
        health = None  # "healthy" / "unhealthy" / "starting" / None

        try:
            s = c.stats(stream=False)

            # CPU %
            cpu_delta = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
            sys_delta = s["cpu_stats"]["system_cpu_usage"] - s["precpu_stats"]["system_cpu_usage"]
            cpu_count = len(s["cpu_stats"]["cpu_usage"].get("percpu_usage") or []) or 1
            cpu = (cpu_delta / sys_delta) * cpu_count * 100.0 if sys_delta > 0 else 0.0

            # MEM
            mem_usage = s.get("memory_stats", {}).get("usage", 0) or 0
            mem_limit = s.get("memory_stats", {}).get("limit", 0) or 0
            mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit else 0.0

            # NET (sum all interfaces)
            networks = s.get("networks") or {}
            for _, v in networks.items():
                net_rx += int(v.get("rx_bytes", 0) or 0)
                net_tx += int(v.get("tx_bytes", 0) or 0)

            # PIDs
            pids_current = (s.get("pids_stats") or {}).get("current")

        except Exception:
            pass

        # Healthcheck (pas dans stats -> via attrs)
        try:
            attrs = c.attrs  # may trigger API call
            health_obj = ((attrs.get("State") or {}).get("Health") or {})
            health = health_obj.get("Status")  # healthy/unhealthy/starting
        except Exception:
            health = None

        rows.append({
            "name": c.name,
            "status": c.status,
            "image": image,

            "cpu": cpu,

            "mem_percent": mem_percent,
            "mem_usage": mem_usage,
            "mem_limit": mem_limit,

            "net_rx": net_rx,
            "net_tx": net_tx,

            "pids": pids_current,
            "health": health,
        })

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
        "fmt": {
            "bytes_to_human": bytes_to_human,  # pour réutiliser dans le template
        }
    }
    return templates.TemplateResponse("index.html", context)

@app.get("/health")
def health():
    return {"status": "ok"}