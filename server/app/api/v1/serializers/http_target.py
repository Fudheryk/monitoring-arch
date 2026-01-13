# coding: utf-8
#!/usr/bin/env python3
# server/app/api/v1/serializers/http_target.py
"""
Sérialise un HttpTarget en dictionnaire JSON prêt à exposer.
"""

from __future__ import annotations
from typing import Any, Dict, TYPE_CHECKING

from app.core.utils.datetime import age_in_seconds

if TYPE_CHECKING:
    from app.infrastructure.persistence.database.models.http_target import HttpTarget


def serialize_http_target(t: HttpTarget) -> Dict[str, Any]:
    """Convertit un HttpTarget SQLAlchemy en dictionnaire JSON prêt à exposer."""

    status = t.last_status_code

    # Libellé "lisible" du dernier status :
    # - None            → None (pas encore vérifié)
    # - 0               → "network_error" (timeout/DNS/TLS/transport)
    # - nombre (int)    → str(code)
    if status is None:
        status_label = None
    elif status == 0:
        status_label = "network_error"
    else:
        status_label = str(status)

    # Message utilisateur cohérent avec la logique du modèle :
    # - En mode simple : messages “En ligne”, “Problème serveur (5xx)”, “Pas de réponse”, etc.
    # - En mode expert : “Code X accepté / non accepté (ranges …)”
    status_message = t.get_status_message()

    return {
        # Identité / configuration
        "id": str(t.id),
        "name": t.name,
        "url": t.url,
        "method": t.method,
        "accepted_status_codes": t.accepted_status_codes,  # [[start,end], ...] ou None (mode simple)
        "timeout_seconds": t.timeout_seconds,
        "check_interval_seconds": t.check_interval_seconds,
        "is_active": t.is_active,

        # État courant (logique centralisée dans le modèle)
        "is_up": bool(t.is_up),                 # ✅ corrige l’oubli d’appel de la méthode
        "is_pending": t.last_check_at is None,    # Jamais vérifié → en attente

        # Métadonnées temporelles
        "last_check_at": t.last_check_at.isoformat() if t.last_check_at else None,
        "last_check_age_sec": age_in_seconds(t.last_check_at),
        "last_state_change_age_sec": age_in_seconds(getattr(t, "last_state_change_at", None)),

        # Dernier résultat brut + interprétation légère
        "last_status_code": status,
        "last_status_label": status_label,        # "network_error" | "200" | "404" | None
        "last_response_time_ms": getattr(t, "last_response_time_ms", None),
        "last_error_message": getattr(t, "last_error_message", None),

        # Message humain prêt pour l’UI/notifications
        "status_message": status_message,
    }
