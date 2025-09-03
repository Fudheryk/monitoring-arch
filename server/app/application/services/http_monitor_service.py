from __future__ import annotations
"""server/app/application/services/http_monitor_service.py
~~~~~~~~~~~~~~~~~~~~~~~~
Service de monitoring HTTP :
- sélectionne les cibles dues (is_active && intervalle écoulé)
- exécute la requête HTTP (méthode, timeout)
- met à jour les champs last_* (status_code, latency, erreur)
- TODO : ouvrir/fermer des incidents + notifications.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

import time
import httpx
from sqlalchemy import select

from app.infrastructure.persistence.database.session import get_sync_session
from app.infrastructure.persistence.database.models.http_target import HttpTarget
from app.infrastructure.persistence.repositories.incident_repository import IncidentRepository


def _should_check(t: HttpTarget, now: datetime) -> bool:
    if not t.is_active:
        return False
    if t.last_check_at is None:
        return True
    interval = timedelta(seconds=t.check_interval_seconds or 300)
    return (now - t.last_check_at) >= interval


def _perform_check(t: HttpTarget) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """Retourne (status_code, response_time_ms, error_message)."""
    timeout = (t.timeout_seconds or 30)
    method = (t.method or "GET").upper()
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.request(method, t.url)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return resp.status_code, elapsed_ms, None
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return None, elapsed_ms, str(exc)


def _update_result(t: HttpTarget, status: Optional[int], elapsed_ms: Optional[int], err: Optional[str]) -> None:
    t.last_check_at = datetime.now(timezone.utc)
    t.last_status_code = status
    t.last_response_time_ms = elapsed_ms
    t.last_error_message = err

def check_http_targets() -> int:
    """Parcourt les cibles HTTP actives et retourne le nombre de checks effectués."""
    from sqlalchemy import select
    import httpx
    import time
    import logging
    from datetime import datetime, timezone
    
    logger = logging.getLogger(__name__)
    DEFAULT_METHOD = "GET"
    INCIDENT_TITLE_PREFIX = "HTTP check failed: "
    
    updated = 0
    with get_sync_session() as s:
        irepo = IncidentRepository(s)
        targets = s.scalars(select(HttpTarget).where(HttpTarget.is_active.is_(True))).all()
        
        for t in targets:
            start = time.perf_counter()
            status = None
            err = None
            
            try:
                timeout = httpx.Timeout(t.timeout_seconds, connect=5.0)
                with httpx.Client(timeout=timeout) as c:
                    r = c.request(t.method or DEFAULT_METHOD, t.url)
                    status = r.status_code
            except Exception as e:
                logger.warning(f"HTTP check failed for {t.url}: {str(e)}")
                err = str(e)
            
            rt_ms = int((time.perf_counter() - start) * 1000)
            now = datetime.now(timezone.utc)
            
            # Mise à jour de la cible
            t.last_check_at = now
            t.last_status_code = status
            t.last_response_time_ms = rt_ms
            t.last_error_message = err
            updated += 1
            
            # Gestion des incidents
            title = f"{INCIDENT_TITLE_PREFIX}{t.name}"
            if (status is None) or (t.expected_status_code and status != t.expected_status_code):
                irepo.open(
                    client_id=t.client_id,
                    title=title,
                    severity="warning",
                    machine_id=None,
                    description=err or f"Got {status}, expected {t.expected_status_code}"
                )
            else:
                irepo.resolve_by_title(client_id=t.client_id, title=title)
        
        if updated:
            s.commit()
    
    return updated


def check_one_target(target_id: str) -> dict:
    """Check manuel d’une seule cible — pratique pour les tests / UI."""
    with get_sync_session() as s:
        t = s.get(HttpTarget, target_id)
        if not t:
            return {"ok": False, "reason": "not_found"}
        status, elapsed_ms, err = _perform_check(t)
        _update_result(t, status, elapsed_ms, err)
        s.commit()
        return {
            "ok": err is None,
            "status": status,
            "ms": elapsed_ms,
            "error": err,
            "expected": t.expected_status_code,
        }
