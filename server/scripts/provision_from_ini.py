#!/usr/bin/env python3
from __future__ import annotations

"""
provision_from_ini.py

Provisionnement "client réel" à partir d'un fichier INI.
Idempotent : relançable sans créer de doublons.

Fonctionnalités
---------------
- [client]        : create client (idempotent sur name)
- [admin]         : create admin user (idempotent sur email)
- [client_settings]: create settings (idempotent sur client_id)
- [api_keys]      : create N API keys (idempotent sur key, et aussi sur (client_id,name))
- [http_targets]  : create targets (idempotent sur (client_id,url))

Secrets
-------
- Si admin password vide => généré (token_urlsafe)
- Si api keys non fournies => générées (alphanum 32 par défaut)
- Les secrets générés sont écrits dans : <ini>.generated.secrets.env

Garde-fous
----------
- PROVISION_CLIENT=true requis
- En prod (APP_ENV=production/prod) => refus sauf ALLOW_PROD_PROVISIONING=true

Connexion DB
------------
- DATABASE_URL (recommandé) sinon SQLALCHEMY_DATABASE_URI
"""

import os
import sys
import uuid
import secrets
import string
import configparser
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from passlib.context import CryptContext


# ------------------------------- Guards / env --------------------------------

def _env(name: str, default: Optional[str] = None) -> str:
    v = os.getenv(name)
    if v is None:
        return default or ""
    return v.strip()


def _truthy(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_prod() -> bool:
    env = (_env("APP_ENV") or _env("ENV") or "").lower()
    return env in {"prod", "production"}


def _require_guards() -> None:
    if not _truthy(_env("PROVISION_CLIENT", "")):
        raise SystemExit(
            "Refus: PROVISION_CLIENT n'est pas activé. Mets PROVISION_CLIENT=true pour exécuter."
        )
    if _is_prod() and not _truthy(_env("ALLOW_PROD_PROVISIONING", "")):
        raise SystemExit(
            "Refus: prod détectée (APP_ENV=production). "
            "Pour autoriser explicitement : ALLOW_PROD_PROVISIONING=true."
        )


def _db_url() -> str:
    url = _env("DATABASE_URL") or _env("SQLALCHEMY_DATABASE_URI")
    if not url:
        raise SystemExit("DATABASE_URL (ou SQLALCHEMY_DATABASE_URI) est requis.")
    return url


def _engine() -> Engine:
    return create_engine(_db_url(), pool_pre_ping=True, future=True)


# ------------------------------ INI parsing ----------------------------------

def _read_ini(path: Path) -> configparser.ConfigParser:
    if not path.exists():
        raise SystemExit(f"INI introuvable: {path}")
    cfg = configparser.ConfigParser()
    cfg.read(path, encoding="utf-8")
    return cfg


def _cfg_get(cfg: configparser.ConfigParser, section: str, key: str, default: str = "") -> str:
    if not cfg.has_section(section):
        return default
    return cfg.get(section, key, fallback=default).strip()


def _cfg_get_int(cfg: configparser.ConfigParser, section: str, key: str, default: int) -> int:
    v = _cfg_get(cfg, section, key, str(default))
    try:
        return int(v)
    except ValueError:
        raise SystemExit(f"Valeur invalide pour [{section}] {key} (int attendu): {v!r}")


def _cfg_get_bool(cfg: configparser.ConfigParser, section: str, key: str, default: bool) -> bool:
    v = _cfg_get(cfg, section, key, str(default)).lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    raise SystemExit(f"Valeur invalide pour [{section}] {key} (bool attendu): {v!r}")


# ---------------------------- Secrets generation -----------------------------

ALNUM = string.ascii_letters + string.digits


def gen_alphanum(length: int) -> str:
    return "".join(secrets.choice(ALNUM) for _ in range(length))


def gen_password() -> str:
    # Password admin initial (copiable). Tu peux ajuster la longueur si besoin.
    return secrets.token_urlsafe(24)


# ---------------------------- HTTP target parsing ----------------------------

def parse_http_targets(cfg: configparser.ConfigParser) -> List[Dict[str, object]]:
    """
    [http_targets]
    target_01 = Name|https://url|GET|30|300|true
    """
    if not cfg.has_section("http_targets"):
        return []

    targets: List[Dict[str, object]] = []
    for k, v in cfg.items("http_targets"):
        if not k.lower().startswith("target_"):
            continue
        raw = (v or "").strip()
        if not raw:
            continue

        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 6:
            raise SystemExit(
                f"[http_targets] {k} invalide. Format attendu: name|url|method|timeout|interval|active. "
                f"Reçu: {raw!r}"
            )

        name, url, method, timeout_s, interval_s, active = parts
        try:
            timeout = int(timeout_s)
            interval = int(interval_s)
        except ValueError:
            raise SystemExit(f"[http_targets] {k} timeout/interval doivent être des ints: {raw!r}")

        act = active.lower() in {"1", "true", "yes", "y", "on"}

        targets.append(
            {
                "name": name,
                "url": url,
                "method": method.upper() or "GET",
                "timeout_seconds": timeout,
                "check_interval_seconds": interval,
                "is_active": act,
            }
        )

    return targets


# ------------------------------ Provision logic ------------------------------

def provision_from_ini(ini_path: Path) -> None:
    _require_guards()

    cfg = _read_ini(ini_path)

    # ---- client
    client_name = _cfg_get(cfg, "client", "name")
    client_email = _cfg_get(cfg, "client", "email", "")

    if not client_name:
        raise SystemExit("INI invalide: [client] name est requis.")

    # ---- admin
    admin_email = _cfg_get(cfg, "admin", "email")
    admin_role = _cfg_get(cfg, "admin", "role", "admin_client")
    admin_password = _cfg_get(cfg, "admin", "password", "")  # vide => généré

    if not admin_email:
        raise SystemExit("INI invalide: [admin] email est requis.")

    # ---- api keys
    api_count = _cfg_get_int(cfg, "api_keys", "count", 0)
    api_prefix = _cfg_get(cfg, "api_keys", "prefix", "key-")
    api_len = _cfg_get_int(cfg, "api_keys", "length", 32)
    api_alphabet = _cfg_get(cfg, "api_keys", "alphabet", "alnum").lower()

    if api_count < 0:
        raise SystemExit("[api_keys] count doit être >= 0")
    if api_len < 16:
        raise SystemExit("[api_keys] length trop court (>=16 recommandé).")
    if api_alphabet != "alnum":
        raise SystemExit("[api_keys] alphabet supporté: alnum (pour ton besoin)")

    # On NE génère plus les clés ici.
    # Idempotence: la génération se fera après vérification DB (client_id, name).
    generated_keys: List[Tuple[str, str]] = []  # (name, key_value) - uniquement celles réellement créées


    # ---- settings
    notif_email = _cfg_get(cfg, "client_settings", "notification_email", admin_email)
    slack_webhook_url = _cfg_get(cfg, "client_settings", "slack_webhook_url", "") or None
    slack_channel_name = _cfg_get(cfg, "client_settings", "slack_channel_name", "#alerts")

    heartbeat_threshold_minutes = _cfg_get_int(cfg, "client_settings", "heartbeat_threshold_minutes", 5)
    consecutive_failures_threshold = _cfg_get_int(cfg, "client_settings", "consecutive_failures_threshold", 2)
    alert_grouping_enabled = _cfg_get_bool(cfg, "client_settings", "alert_grouping_enabled", True)
    alert_grouping_window_seconds = _cfg_get_int(cfg, "client_settings", "alert_grouping_window_seconds", 300)
    reminder_notification_seconds = _cfg_get_int(cfg, "client_settings", "reminder_notification_seconds", 600)
    grace_period_seconds = _cfg_get_int(cfg, "client_settings", "grace_period_seconds", 120)

    # ---- http targets
    http_targets = parse_http_targets(cfg)

    # Output secrets file (append-safe but idempotent-ish in content)
    secrets_env_path = ini_path.with_suffix(ini_path.suffix + ".generated.secrets.env")

    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    engine = _engine()

    print(f"== Provision via INI: {ini_path} ==")
    print(f"- client: {client_name} ({client_email or 'no-email'})")
    print(f"- admin:  {admin_email} (role={admin_role})")
    print(f"- api keys: {api_count} (len={api_len}, alnum)")
    print(f"- http targets: {len(http_targets)}")
    print()

    # Track what was generated so we can export it
    generated_admin_password: Optional[str] = None

    with engine.begin() as conn:
        # 1) client (idempotent sur name)
        conn.execute(
            text(
                """
                INSERT INTO clients (id, name, email)
                SELECT
                    CAST(:id AS UUID),
                    CAST(:name AS VARCHAR(255)),
                    NULLIF(CAST(:email AS VARCHAR(255)), '')
                WHERE NOT EXISTS (
                    SELECT 1 FROM clients WHERE name = CAST(:name AS VARCHAR(255))
                )
                """
            ),
            {"id": str(uuid.uuid4()), "name": client_name, "email": client_email},
        )

        client_id = conn.execute(
            text("SELECT id FROM clients WHERE name = CAST(:name AS VARCHAR(255)) LIMIT 1"),
            {"name": client_name},
        ).scalar()

        if not client_id:
            raise RuntimeError("Impossible de récupérer client_id.")
        client_id = str(client_id)

        # 2) admin user (idempotent sur email)
        user_id = conn.execute(
            text("SELECT id FROM users WHERE email = CAST(:email AS VARCHAR(255)) LIMIT 1"),
            {"email": admin_email},
        ).scalar()

        if not user_id:
            if not admin_password:
                admin_password = gen_password()
                generated_admin_password = admin_password

            conn.execute(
                text(
                    """
                    INSERT INTO users (id, client_id, email, password_hash, role, is_active)
                    VALUES (
                        CAST(:id AS UUID),
                        CAST(:client_id AS UUID),
                        CAST(:email AS VARCHAR(255)),
                        CAST(:ph AS TEXT),
                        CAST(:role AS VARCHAR(32)),
                        TRUE
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "client_id": client_id,
                    "email": admin_email,
                    "ph": pwd_ctx.hash(admin_password),
                    "role": admin_role,
                },
            )
        # NOTE: si le user existe déjà, on ne modifie pas son password/role ici (sécurité + idempotence)

        # 3) client_settings (idempotent sur client_id)
        settings_id = conn.execute(
            text("SELECT id FROM client_settings WHERE client_id = CAST(:client_id AS UUID) LIMIT 1"),
            {"client_id": client_id},
        ).scalar()

        if not settings_id:
            conn.execute(
                text(
                    """
                    INSERT INTO client_settings (
                        id, client_id, notification_email,
                        slack_webhook_url, slack_channel_name,
                        heartbeat_threshold_minutes, consecutive_failures_threshold,
                        alert_grouping_enabled, alert_grouping_window_seconds,
                        reminder_notification_seconds, grace_period_seconds,
                        created_at, updated_at
                    )
                    VALUES (
                        CAST(:id AS UUID),
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
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "client_id": client_id,
                    "notification_email": notif_email,
                    "slack_webhook_url": slack_webhook_url,
                    "slack_channel_name": slack_channel_name,
                    "heartbeat_threshold_minutes": heartbeat_threshold_minutes,
                    "consecutive_failures_threshold": consecutive_failures_threshold,
                    "alert_grouping_enabled": alert_grouping_enabled,
                    "alert_grouping_window_seconds": alert_grouping_window_seconds,
                    "reminder_notification_seconds": reminder_notification_seconds,
                    "grace_period_seconds": grace_period_seconds,
                },
            )

        # 4) api_keys (idempotent sur (client_id,name))
        # (ne pas redéfinir generated_keys ici, on utilise celle déclarée plus haut)

        key_names = [f"{api_prefix}{i:02d}" for i in range(1, api_count + 1)]

        for idx, key_name in enumerate(key_names, start=1):
            # si l’INI fournit explicitement une clé, on la prend, sinon on générera si nécessaire
            ini_value = _cfg_get(cfg, "api_keys", f"key_{idx:02d}", "").strip() or None

            # 1) si une clé existe déjà pour ce client et ce name -> on ne touche à rien
            existing = conn.execute(
                text(
                    """
                    SELECT key
                    FROM api_keys
                    WHERE client_id = CAST(:client_id AS UUID)
                    AND name = CAST(:name AS VARCHAR(255))
                    LIMIT 1
                    """
                ),
                {"client_id": client_id, "name": key_name},
            ).scalar()

            if existing:
                continue

            # 2) sinon, on insère : clé imposée par INI ou générée
            key_value = ini_value or gen_alphanum(api_len)

            # sécurité: éviter collision globale (rare mais possible)
            exists_by_value = conn.execute(
                text("SELECT 1 FROM api_keys WHERE key = CAST(:key AS VARCHAR(255)) LIMIT 1"),
                {"key": key_value},
            ).scalar()
            if exists_by_value:
                # en cas de collision, on regen (ou on fail si elle venait de l'INI)
                if ini_value:
                    raise SystemExit(f"Clé API déjà utilisée ailleurs (collision) pour {key_name}.")
                key_value = gen_alphanum(api_len)

            conn.execute(
                text(
                    """
                    INSERT INTO api_keys (id, client_id, key, name, is_active)
                    VALUES (
                        CAST(:id AS UUID),
                        CAST(:client_id AS UUID),
                        CAST(:key AS VARCHAR(255)),
                        CAST(:name AS VARCHAR(255)),
                        TRUE
                    )
                    """
                ),
                {"id": str(uuid.uuid4()), "client_id": client_id, "key": key_value, "name": key_name},
            )

            # on n'écrit dans le fichier secrets QUE si on a effectivement créé une nouvelle clé
            if not ini_value:
                generated_keys.append((key_name, key_value))

        # 5) http_targets (idempotent sur (client_id,url))
        for t in http_targets:
            conn.execute(
                text(
                    """
                    INSERT INTO http_targets (
                        id, client_id, name, url, method,
                        timeout_seconds, check_interval_seconds, is_active
                    )
                    SELECT
                        CAST(:id AS UUID),
                        CAST(:client_id AS UUID),
                        CAST(:name AS VARCHAR(255)),
                        CAST(:url AS VARCHAR(1000)),
                        CAST(:method AS VARCHAR(16)),
                        CAST(:timeout_seconds AS INTEGER),
                        CAST(:check_interval_seconds AS INTEGER),
                        CAST(:is_active AS BOOLEAN)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM http_targets
                        WHERE client_id = CAST(:client_id AS UUID)
                          AND url = CAST(:url AS VARCHAR(1000))
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "client_id": client_id,
                    "name": str(t["name"]),
                    "url": str(t["url"]),
                    "method": str(t["method"]),
                    "timeout_seconds": int(t["timeout_seconds"]),
                    "check_interval_seconds": int(t["check_interval_seconds"]),
                    "is_active": bool(t["is_active"]),
                },
            )

    # ----------------------- Write generated secrets -------------------------
    # On écrit uniquement ce qui a été généré par le script (pas ce qui était dans l'INI).
    lines: List[str] = []
    header = (
        f"# Generated secrets for {ini_path.name}\n"
        f"# WARNING: store securely (vault/secret manager). Do not commit.\n"
    )

    if generated_admin_password:
        lines.append(f"ADMIN_EMAIL={admin_email}")
        lines.append(f"ADMIN_PASSWORD={generated_admin_password}")

    for name, key_value in generated_keys:
        # On exporte aussi le nom de la clé + la valeur
        # (le système utilise "key", mais garder le name aide l’opérationnel)
        lines.append(f"API_KEY_NAME__{name}={name}")
        lines.append(f"API_KEY_VALUE__{name}={key_value}")

    if lines:
        secrets_env_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
        print("✅ Secrets générés écrits dans :")
        print(f"   {secrets_env_path}")
    else:
        print("ℹ️ Aucun secret généré (tout existait déjà ou était fourni dans l'INI).")

    print("\nProvision terminé ✅")


def main(argv: List[str]) -> None:
    if len(argv) != 2:
        raise SystemExit(
            "Usage: python server/scripts/provision_from_ini.py <path/to/client.ini>\n"
            "Ex:    PROVISION_CLIENT=true APP_ENV=staging DATABASE_URL=... "
            "python server/scripts/provision_from_ini.py server/scripts/provisioning/smarthack.ini"
        )

    ini_path = Path(argv[1]).resolve()
    provision_from_ini(ini_path)


if __name__ == "__main__":
    main(sys.argv)
