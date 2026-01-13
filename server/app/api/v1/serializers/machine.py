# server/app/api/v1/serializers/machine.py

from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.persistence.database.models.machine import Machine


def serialize_machine_summary(m: "Machine") -> Dict[str, Any]:
    return {
        "id": str(m.id),
        "hostname": m.hostname,
        "os_type": m.os_type,
        "last_seen": m.last_seen.isoformat() if m.last_seen else None,
        "is_active": m.is_active,
        "status": m.status,
        "registered_at": m.registered_at.isoformat(),
        "unregistered_at": m.unregistered_at.isoformat() if m.unregistered_at else None,
        # status ajouté dynamiquement dans l’endpoint
    }


def serialize_machine_detail(m: "Machine") -> Dict[str, Any]:
    return {
        "id": str(m.id),
        "hostname": m.hostname,
        "os_type": m.os_type,
        "os_version": m.os_version,
        "last_seen": m.last_seen.isoformat() if m.last_seen else None,
        "registered_at": m.registered_at.isoformat() if m.registered_at else None,
        "unregistered_at": m.unregistered_at.isoformat() if m.unregistered_at else None,
        "is_active": m.is_active,
        # status ajouté dans l’endpoint
    }
