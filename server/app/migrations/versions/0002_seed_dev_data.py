from __future__ import annotations

"""
0002_seed_dev_data

- Crée un client "Dev" + un user admin + une API key.
- Ne crée PAS de machine pour le client Dev : la machine sera créée
  et liée à la clé à la première ingestion via ensure_machine().
- Crée un client "Acme Corp" avec une clé prod + une cible HTTP.
- Ajoute aussi client_settings pour Dev.
- Seed des métriques builtin dans metric_definitions à partir de BUILTIN_METRICS_SEED.
"""

from alembic import op
from sqlalchemy import text
from passlib.context import CryptContext
import uuid
import os

revision = "0002_seed_dev_data"
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
    conn = op.get_bind()

    # ───────────────────────────────── Client "Dev" ─────────────────────────
    SEED_CLIENT_NAME = os.getenv("SEED_CLIENT_NAME", "Dev")
    SEED_API_KEY_1 = os.getenv("SEED_API_KEY_1", "dev-apikey-123")
    SEED_API_KEY_2 = os.getenv("SEED_API_KEY_2", "dev-apikey-124")

    # 1) Client Dev (idempotent sur name)
    client_uuid = str(uuid.uuid4())
    conn.execute(
        text(
            """
        INSERT INTO clients (id, name)
        SELECT CAST(:id AS UUID), CAST(:name AS VARCHAR(255))
        WHERE NOT EXISTS (SELECT 1 FROM clients WHERE name = :name)
        """
        ),
        {"id": client_uuid, "name": SEED_CLIENT_NAME},
    )

    # 2) Récupérer l'id du client Dev (quelle que soit l'insert précédente)
    client_id = conn.execute(
        text(
            "SELECT id FROM clients WHERE name = CAST(:name AS VARCHAR(255)) LIMIT 1"
        ),
        {"name": SEED_CLIENT_NAME},
    ).scalar()
    client_id = str(client_id)

    # 3) Utilisateur admin pour le client Dev (idempotent sur email)
    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    admin_email = "admin@example.com"
    admin_pass = "admin"
    user_id = str(uuid.uuid4())

    conn.execute(
        text(
            """
        INSERT INTO users (id, client_id, email, password_hash, role, is_active)
        SELECT
            CAST(:id AS UUID),
            CAST(:client_id AS UUID),
            CAST(:email AS VARCHAR(255)),
            CAST(:ph AS TEXT),
            CAST(:role AS VARCHAR(32)),
            TRUE
        WHERE NOT EXISTS (
            SELECT 1 FROM users WHERE email = CAST(:email AS VARCHAR(255))
        )
        """
        ),
        {
            "id": user_id,
            "client_id": client_id,
            "email": admin_email,
            "ph": pwd.hash(admin_pass),
            "role": "admin_client",
        },
    )

    # 4.1) API Key Dev (première clé)
    conn.execute(
        text(
            """
        INSERT INTO api_keys (id, client_id, key, name, is_active)
        SELECT 
            CAST(:id AS UUID),
            CAST(:client_id AS UUID),
            CAST(:key AS VARCHAR(255)),
            'dev',
            TRUE
        WHERE NOT EXISTS (SELECT 1 FROM api_keys WHERE key = :key)
        """
        ),
        {
            "id": str(uuid.uuid4()),
            "client_id": client_id,
            "key": SEED_API_KEY_1,
        },
    )

    # 4.2) API Key Dev (seconde clé)
    conn.execute(
        text(
            """
        INSERT INTO api_keys (id, client_id, key, name, is_active)
        SELECT 
            CAST(:id AS UUID),
            CAST(:client_id AS UUID),
            CAST(:key AS VARCHAR(255)),
            'dev',
            TRUE
        WHERE NOT EXISTS (SELECT 1 FROM api_keys WHERE key = :key)
        """
        ),
        {
            "id": str(uuid.uuid4()),
            "client_id": client_id,
            "key": SEED_API_KEY_2,
        },
    )

    # 5) client_settings pour Dev
    client_settings_id = str(uuid.uuid4())
    conn.execute(
        text(
            """ 
        INSERT INTO client_settings (
            id, client_id, notification_email, slack_webhook_url, slack_channel_name, heartbeat_threshold_minutes,
            consecutive_failures_threshold, alert_grouping_enabled, alert_grouping_window_seconds,
            reminder_notification_seconds, grace_period_seconds, created_at, updated_at
        ) 
        SELECT 
            CAST(:cs_id AS UUID),
            CAST(:client_id AS UUID),
            :notification_email,
            :slack_webhook_url,
            :slack_channel_name,
            :heartbeat_threshold_minutes,
            :consecutive_failures_threshold,
            :alert_grouping_enabled,
            :alert_grouping_window_seconds,
            :reminder_notification_seconds,
            :grace_period_seconds,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        WHERE NOT EXISTS (
            SELECT 1 FROM client_settings WHERE client_id = CAST(:client_id AS UUID)
        )
        """
        ),
        {
            "cs_id": client_settings_id,
            "client_id": client_id,
            "notification_email": "frederic.gilgarcia@gmail.com",
            "slack_webhook_url": (os.getenv("SLACK_WEBHOOK_URL") or "").strip() or None,
            "slack_channel_name": "#notif-webhook",
            "heartbeat_threshold_minutes": 5,
            "consecutive_failures_threshold": 2,
            "alert_grouping_enabled": True,
            "alert_grouping_window_seconds": 300,
            "reminder_notification_seconds": 600,
            "grace_period_seconds": 120,
        },
    )

    # 6) Cible HTTP d’exemple pour Dev
    conn.execute(
        text(
            """
        INSERT INTO http_targets (
            id, client_id, name, url, method,
            timeout_seconds,
            check_interval_seconds, is_active
        )
        SELECT
            CAST(:id AS UUID),
            CAST(:client_id AS UUID),
            CAST(:name AS VARCHAR(255)),
            CAST(:url AS VARCHAR(1000)),
            'GET',
            30,
            300,
            TRUE
        WHERE NOT EXISTS (
            SELECT 1 FROM http_targets
            WHERE client_id = :client_id AND url = :url
        )
        """
        ),
        {
            "id": str(uuid.uuid4()),
            "client_id": client_id,
            "name": "Example Target",
            "url": "https://example.com",
        },
    )

    # ───────────────────────────────── Client "Acme Corp" ─────────────────────
    acme_client_id = str(uuid.uuid4())
    conn.execute(
        text(
            """
        INSERT INTO clients (id, name, email)
        SELECT CAST(:id AS UUID), 'Acme Corp', 'it@acme.com'
        WHERE NOT EXISTS (SELECT 1 FROM clients WHERE name = 'Acme Corp')
        """
        ),
        {"id": acme_client_id},
    )

    # API Key pour Acme (clé prod de démo)
    conn.execute(
        text(
            """
        INSERT INTO api_keys (id, client_id, key, name, is_active)
        SELECT 
            CAST(:id AS UUID),
            (SELECT id FROM clients WHERE name = 'Acme Corp' LIMIT 1),
            'acme-prod-key-456',
            'prod',
            TRUE
        WHERE NOT EXISTS (SELECT 1 FROM api_keys WHERE key = 'acme-prod-key-456')
        """
        ),
        {"id": str(uuid.uuid4())},
    )

    # ───────────────────────────── Seed metric_definitions ────────────────────────
    for m in BUILTIN_METRICS_SEED:
        # On enrichit le dict avec un id UUID string pour la colonne PK
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


    # ⚠ IMPORTANT :
    # On NE seed plus de machine / métriques / thresholds ici pour Dev.
    # - La machine sera créée dynamiquement à la première ingestion de l’agent
    #   via ensure_machine().
    # - Les métriques seront créées via process_samples / SampleRepository.
    # - Les thresholds peuvent être créés plus tard via l’UI ou un script dédié.


def downgrade():
    """Rollback des données seedées (idempotent)."""
    conn = op.get_bind()

    # ───────── suppression des métriques builtin du catalogue ─────────
    for m in BUILTIN_METRICS_SEED:
        conn.execute(
            text("DELETE FROM metric_definitions WHERE name = :name"),
            {"name": m["name"]},
        )

    # ───────── Client Acme ─────────
    conn.execute(
        text("DELETE FROM api_keys WHERE key = 'acme-prod-key-456'")
    )
    conn.execute(
        text("DELETE FROM clients WHERE name = 'Acme Corp'")
    )

    # ───────── Client Dev ─────────
    SEED_CLIENT_NAME = os.getenv("SEED_CLIENT_NAME", "Dev")
    SEED_API_KEY_1 = os.getenv("SEED_API_KEY_1", "dev-apikey-123")
    SEED_API_KEY_2 = os.getenv("SEED_API_KEY_2", "dev-apikey-124")

    # Cible HTTP de démo
    conn.execute(
        text("DELETE FROM http_targets WHERE url = 'https://example.com'")
    )

    # client_settings
    conn.execute(
        text(
            """
        DELETE FROM client_settings
        WHERE client_id IN (
            SELECT id FROM clients WHERE name = :name
        )
        """
        ),
        {"name": SEED_CLIENT_NAME},
    )

    # API key Dev
    conn.execute(
        text("DELETE FROM api_keys WHERE key = :key1 OR key = :key2"),
        {"key1": SEED_API_KEY_1, "key2": SEED_API_KEY_2},
    )

    # User admin
    conn.execute(
        text(   
            """
        DELETE FROM users
        WHERE email = :email
        """
        ),
        {"email": "admin@example.com"},
    )

    # Client Dev (uniquement s’il n’est plus référencé)
    conn.execute(
        text(
            """
        DELETE FROM clients
        WHERE name = :name
          AND NOT EXISTS (SELECT 1 FROM api_keys WHERE client_id = clients.id)
          AND NOT EXISTS (SELECT 1 FROM http_targets WHERE client_id = clients.id)
          AND NOT EXISTS (SELECT 1 FROM client_settings WHERE client_id = clients.id)
          AND NOT EXISTS (SELECT 1 FROM users WHERE client_id = clients.id)
        """
        ),
        {"name": SEED_CLIENT_NAME},
    )

    # On ne touche pas aux machines / metrics / thresholds dans ce downgrade :
    # elles sont désormais créées dynamiquement (ingest + UI) et non plus seedées ici.
