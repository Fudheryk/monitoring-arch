from __future__ import annotations

"""
0002_seed_builtin_metrics

---------------------------------
Cette migration ne seed PLUS aucune donnée "Dev/Acme" (clients, users, api keys, http_targets,
client_settings, etc.). Elle ne fait que seed le référentiel stable des métriques builtin
dans metric_definitions.

La donnée de démo doit être provisionnée via un script séparé (Option A),
ex: server/scripts/seed_demo_data.py, exécuté uniquement en dev/staging.

Pourquoi ?
----------
- Les migrations Alembic doivent idéalement rester liées au schéma + référentiels stables.
- Éviter toute création de comptes/keys en prod via "alembic upgrade head".
"""

from alembic import op
from sqlalchemy import text
import uuid

revision = "0002_seed_builtin_metrics"
down_revision = "0001_initial_full"
branch_labels = None
depends_on = None


# ──────────────────────────── Seed builtin metrics ─────────────────────────────
BUILTIN_METRICS_SEED = [
    # --- FIREWALL -----------------------------------------------------------
    {
        "name": "iptables.rules_count",
        "type": "numeric",
        "group_name": "firewall",
        "description": (
            "Nombre total de règles configurées dans iptables."
        ),
        "is_suggested_critical": True,
        "default_condition": "lt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "iptables.version",
        "type": "string",
        "group_name": "firewall",
        "description": (
            "Version d'iptables."
        ),
        "is_suggested_critical": True,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "firewalld.running",
        "type": "boolean",
        "group_name": "firewall",
        "description": "Indique si firewalld est actif (running).",
        "is_suggested_critical": True,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "firewalld.version",
        "type": "string",
        "group_name": "firewall",
        "description": "Version de firewalld.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "ufw.enabled",
        "type": "boolean",
        "group_name": "firewall",
        "description": "Indique si UFW est activé (Status: active).",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "ufw.version",
        "type": "string",
        "group_name": "firewall",
        "description": "Version de UFW.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },

    # --- SYSTEME (contexte) -----------------------------------------------
    {
        "name": "system.hostname",
        "type": "string",
        "group_name": "system",
        "description": "Nom d'hôte du système.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.os",
        "type": "string",
        "group_name": "system",
        "description": "Système d'exploitation (Linux, Windows, etc.).",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.kernel_version",
        "type": "string",
        "group_name": "system",
        "description": "Version du noyau.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.kernel_full_version",
        "type": "string",
        "group_name": "system",
        "description": "Version complète du noyau.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.distribution",
        "type": "string",
        "group_name": "system",
        "description": "Nom complet de la distribution Linux.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.architecture",
        "type": "string",
        "group_name": "system",
        "description": "Architecture du système.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.uptime_seconds",
        "type": "numeric",
        "group_name": "system",
        "description": (
            "Temps écoulé depuis le dernier démarrage du système (en secondes)."
        ),
        "is_suggested_critical": True,
        "default_condition": "lt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.process_count",
        "type": "numeric",
        "group_name": "system",
        "description": "Nombre de processus en cours d'exécution.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.python_version",
        "type": "string",
        "group_name": "system",
        "description": "Version de Python utilisée sur le système.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.memory_total_gb",
        "type": "numeric",
        "group_name": "system",
        "description": "Mémoire totale (en Go).",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.memory_available_gb",
        "type": "numeric",
        "group_name": "system",
        "description": "Mémoire disponible (en Go).",
        "is_suggested_critical": True,
        "default_condition": "lt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },

    # --- MISES A JOUR ------------------------------------------------------
    {
        "name": "apt.security_updates",
        "type": "numeric",
        "group_name": "updates",
        "description": "Nombre de mises à jour sécurité disponibles via apt.",
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "apt.updates_available",
        "type": "numeric",
        "group_name": "updates",
        "description": "Nombre de mises à jour disponibles via apt.",
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "apt.version",
        "type": "string",
        "group_name": "updates",
        "description": "Version actuelle de apt sur le système.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "yum.updates_available",
        "type": "numeric",
        "group_name": "updates",
        "description": "Nombre de mises à jour disponibles via yum.",
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "yum.version",
        "type": "string",
        "group_name": "updates",
        "description": "Version actuelle de yum sur le système.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },

    # --- SECURITE ----------------------------------------------------------
    {
        "name": "logged_users",
        "type": "numeric",
        "group_name": "security",
        "description": "Nombre d'utilisateurs connectés au système.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "ssh_connections",
        "type": "numeric",
        "group_name": "security",
        "description": "Nombre de connexions SSH actives.",
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "suspicious_processes",
        "type": "numeric",
        "group_name": "security",
        "description": "Nombre de processus suspects détectés.",
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "high_cpu_processes",
        "type": "numeric",
        "group_name": "security",
        "description": "Nombre de processus consommant plus de 80% de CPU.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "open_ports_count",
        "type": "numeric",
        "group_name": "security",
        "description": "Nombre de ports ouverts sur le système.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "sshd_version",
        "type": "string",
        "group_name": "security",
        "description": "Version actuelle de SSH (sshd) sur le système.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },

    # --- MEMOIRE -----------------------------------------------------------
    {
        "name": "memory.usage_percent",
        "type": "numeric",
        "group_name": "memory",
        "description": "Pourcentage de la mémoire utilisée.",
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "memory.total_bytes",
        "type": "numeric",
        "group_name": "memory",
        "description": "Mémoire totale disponible (bytes).",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "memory.available_bytes",
        "type": "numeric",
        "group_name": "memory",
        "description": "Mémoire disponible (bytes).",
        "is_suggested_critical": False,
        "default_condition": "lt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "swap.usage_percent",
        "type": "numeric",
        "group_name": "memory",
        "description": "Pourcentage de la mémoire swap utilisée.",
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "swap.total_bytes",
        "type": "numeric",
        "group_name": "memory",
        "description": "Mémoire swap totale disponible (en bytes).",
        "is_suggested_critical": False,
        "default_condition": "lt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },

    # --- CPU ---------------------------------------------------------------
    {
        "name": "cpu.usage_percent",
        "type": "numeric",
        "group_name": "cpu",
        "description": "Pourcentage d'utilisation de la CPU.",
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "cpu.count",
        "type": "numeric",
        "group_name": "cpu",
        "description": "Nombre de cœurs de processeur.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.load_1m",
        "type": "numeric",
        "group_name": "system",
        "description": "Charge moyenne du processeur sur 1 minute.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.load_5m",
        "type": "numeric",
        "group_name": "system",
        "description": "Charge moyenne du processeur sur 5 minutes.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "system.load_15m",
        "type": "numeric",
        "group_name": "system",
        "description": "Charge moyenne du processeur sur 15 minutes.",
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },

    # --- RESEAU (global) ---------------------------------------------------
    {
        "name": "network.<iface>.up",
        "type": "boolean",
        "group_name": "network",
        "description": "Valeur Actif si l'interface réseau <iface> est active (UP), Inactif sinon.",
        "is_suggested_critical": True,
        "default_condition": "ne",
        "is_dynamic_family": True,
        "dynamic_dimension": "interface",
    },
    {
        "name": "network.<iface>.speed_mbps",
        "type": "numeric",
        "group_name": "network",
        "description": "Vitesse du lien réseau de l'interface <iface> (en Mbit/s).",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "interface",
    },
    {
        "name": "network.<iface>.errin",
        "type": "numeric",
        "group_name": "network",
        "description": "Nombre total d'erreurs de réception (paquets en erreur) sur l'interface réseau <iface>.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "interface",
    },
    {
        "name": "network.<iface>.errout",
        "type": "numeric",
        "group_name": "network",
        "description": "Nombre total d'erreurs d'émission (paquets en erreur) sur l'interface réseau <iface>.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "interface",
    },
    {
        "name": "network.<iface>.dropin",
        "type": "numeric",
        "group_name": "network",
        "description": "Nombre total de paquets entrants abandonnés (dropped) sur l'interface réseau <iface>.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "interface",
    },
    {
        "name": "network.<iface>.dropout",
        "type": "numeric",
        "group_name": "network",
        "description": "Nombre total de paquets sortants abandonnés (dropped) sur l'interface réseau <iface>.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "interface",
    },
    {
        "name": "network.<iface>.bytes_sent",
        "type": "numeric",
        "group_name": "network",
        "description": "Nombre de bytes envoyés sur l'interface réseau <iface>.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "interface",
    },
    {
        "name": "network.<iface>.bytes_recv",
        "type": "numeric",
        "group_name": "network",
        "description": "Nombre de bytes reçus sur l'interface <iface>.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "interface",
    },
    {
        "name": "network.<iface>.packets_sent",
        "type": "numeric",
        "group_name": "network",
        "description": "Nombre de paquets envoyés sur l'interface <iface>.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "interface",
    },
    {
        "name": "network.<iface>.packets_recv",
        "type": "numeric",
        "group_name": "network",
        "description": "Nombre de paquets reçus sur l'interface <iface>.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "interface",
    },
    {
        "name": "network.<iface>.connections_count",
        "type": "numeric",
        "group_name": "network",
        "description": "Nombre de connexions réseau sur l'interface <iface>.",
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "interface",
    },


    # --- DISQUE (famille dynamique par point de montage) -------------------
    {
        "name": "disk[<mountpoint>].usage_percent",
        "type": "numeric",
        "group_name": "disk",
        "description": (
            "Pourcentage d'utilisation d'une partition pour un point de "
            "montage <mountpoint>."
        ),
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "mountpoint",
    },
    {
        "name": "disk[<mountpoint>].total_gb",
        "type": "numeric",
        "group_name": "disk",
        "description": (
            "Capacité totale (en Go) d'une partition pour un point de montage <mountpoint>."
        ),
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": True,
        "dynamic_dimension": "mountpoint",
    },
    {
        "name": "disk[<mountpoint>].free_gb",
        "type": "numeric",
        "group_name": "disk",
        "description": (
            "Espace libre (en Go) sur une partition pour un point de montage <mountpoint>."
        ),
        "is_suggested_critical": False,
        "default_condition": "lt",
        "is_dynamic_family": True,
        "dynamic_dimension": "mountpoint",
    },

    # --- TEMPERATURE (famille dynamique par cœur CPU) ----------------------
    {
        "name": "temperature.coretemp.current",
        "type": "numeric",
        "group_name": "temperature",
        "description": "Température actuelle d'un cœur du processeur.",
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": True,
        "dynamic_dimension": "core",
    },

    # --- DOCKER (agrégats) -------------------------------------------------
    {
        "name": "docker.daemon_running",
        "type": "boolean",
        "group_name": "docker",
        "description": "Indique si le démon Docker est en cours d'exécution.",
        "is_suggested_critical": True,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "docker.containers_total",
        "type": "numeric",
        "group_name": "docker",
        "description": (
            "Nombre total de conteneurs Docker présents sur l’hôte (tous états confondus)."
        ),
        "is_suggested_critical": True,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "docker.containers_running",
        "type": "numeric",
        "group_name": "docker",
        "description": (
            "Nombre de conteneurs Docker actuellement en cours d'exécution."
        ),
        "is_suggested_critical": True,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "docker.images_total",
        "type": "numeric",
        "group_name": "docker",
        "description": (
            "Nombre total d'images Docker présentes sur le système."
        ),
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "docker.containers_paused",
        "type": "numeric",
        "group_name": "docker",
        "description": (
            "Nombre de conteneurs Docker actuellement en pause."
        ),
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },

    # --- BASE DE DONNEES ---------------------------------------------------
    {
        "name": "mysql.service_active",
        "type": "boolean",
        "group_name": "database",
        "description": (
            "État du service MySQL (actif/inactif) sur l’hôte."
        ),
        "is_suggested_critical": True,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },

    # --- SERVICES (résumés globaux) ---------------------------------------
    {
        "name": "services.active_count",
        "type": "numeric",
        "group_name": "services",
        "description": "Nombre total de services actifs.",
        "is_suggested_critical": True,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "services.failed_count",
        "type": "numeric",
        "group_name": "services",
        "description": "Nombre total de services échoués.",
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },

    # --- SERVICES (famille dynamique par unité systemd) --------------------
    {
        "name": "<unit>.service",
        "type": "boolean",
        "group_name": "services",
        "description": "Indique si le service systemd <unit> est actif.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": True,
        "dynamic_dimension": "service_name",
    },

    # --- LOGS --------------------------------------------------------------
    {
        "name": "logs.errors_count",
        "type": "numeric",
        "group_name": "logs",
        "description": (
            "Le nombre d'erreurs dans les logs système."
        ),
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "logs.warnings_count",
        "type": "numeric",
        "group_name": "logs",
        "description": (
            "Le nombre d'avertissements dans les logs système."
        ),
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "logs.auth_failures",
        "type": "numeric",
        "group_name": "logs",
        "description": (
            "Le nombre d'échecs d'authentification."
        ),
        "is_suggested_critical": True,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "logs.journal_errors_last_hour",
        "type": "numeric",
        "group_name": "logs",
        "description": (
            "Le nombre d'erreurs dans les journaux système au cours de la dernière heure."
        ),
        "is_suggested_critical": False,
        "default_condition": "gt",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },

    # --- TACHES PLANIFIEES -------------------------------------------------
    {
        "name": "cron.available",
        "type": "boolean",
        "group_name": "scheduled_tasks",
        "description": "Indique si le service cron est disponible.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "cron.jobs_count",
        "type": "numeric",
        "group_name": "scheduled_tasks",
        "description": "Nombre de tâches cron programmées.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "anacron.available",
        "type": "boolean",
        "group_name": "scheduled_tasks",
        "description": "Indique si le service Anacron est disponible.",
        "is_suggested_critical": False,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },
    {
        "name": "systemd_timers.count",
        "type": "numeric",
        "group_name": "scheduled_tasks",
        "description": "Nombre de timers systemd actifs.",
        "is_suggested_critical": True,
        "default_condition": "ne",
        "is_dynamic_family": False,
        "dynamic_dimension": None,
    },  
]


def upgrade():
    """
    Seed des métriques builtin dans metric_definitions.

    Idempotence:
    - On insert chaque métrique uniquement si name n'existe pas déjà.
    - On ne met PAS à jour une métrique existante (pour ne pas écraser des modifs prod).
      Si tu veux gérer des mises à jour, fais-le via migrations dédiées (ALTER/UPDATE ciblés).
    """
    conn = op.get_bind()

    for m in BUILTIN_METRICS_SEED:
        params = {
            "id": str(uuid.uuid4()),
            **m,
        }

        conn.execute(
            text(
                """
                INSERT INTO metric_definitions (
                    id,
                    name,
                    type,
                    group_name,
                    description,
                    vendor,
                    is_suggested_critical,
                    default_condition,
                    is_dynamic_family,
                    dynamic_dimension
                )
                SELECT
                    CAST(:id AS UUID),
                    CAST(:name AS VARCHAR(100)),
                    CAST(:type AS metric_type),
                    CAST(:group_name AS TEXT),
                    CAST(:description AS TEXT),
                    'builtin',
                    CAST(:is_suggested_critical AS BOOLEAN),
                    CAST(:default_condition AS VARCHAR(32)),
                    CAST(:is_dynamic_family AS BOOLEAN),
                    CAST(:dynamic_dimension AS VARCHAR(64))
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM metric_definitions
                    WHERE name = CAST(:name AS VARCHAR(100))
                )
                """
            ),
            params,
        )


def downgrade():
    """
    Rollback des métriques builtin seedées.

    Remarque:
    - On supprime par name.
    - C'est idempotent.
    - On ne touche à aucune autre table.
    """
    conn = op.get_bind()

    for m in BUILTIN_METRICS_SEED:
        conn.execute(
            text("DELETE FROM metric_definitions WHERE name = :name"),
            {"name": m["name"]},
        )
