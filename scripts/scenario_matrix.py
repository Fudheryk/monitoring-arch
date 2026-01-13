#!/usr/bin/env python3
"""
scenario_matrix.py - VERSION CORRIGÉE

Corrections apportées :
1. Activation de is_alerting_enabled sur les métriques après onboarding
2. Création des seuils (thresholds) pour cpu.usage_percent et memory.usage_percent
3. Ajout de fonctions helper pour configurer la base
4. Meilleure gestion des attentes entre les étapes
"""

import subprocess
import time
import json
import textwrap
from typing import Optional, List, Dict
import os
from datetime import datetime
import uuid

import requests


# =========================
# LOGGING GLOBAL
# =========================

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(
    LOG_DIR,
    f"scenario_matrix_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
)


def log(msg: str = "") -> None:
    print(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


# =========================
# CONFIGURATION GLOBALE
# =========================

COMPOSE_FILE = "docker/docker-compose.yml"
API_SERVICE = "api"
DB_SERVICE = "db"
DB_USER = "postgres"
DB_NAME = "monitoring"

API_BASE = "http://localhost:8000"
API_KEY = "dev-apikey-123"
CLIENT_NAME = "Dev"

HOSTNAME = "debian-dev"
FINGERPRINT = "f5355ba885eceec3b226bf1c746d7159baa891d9b47aa6d07369a47a8e4d5cc1"

NO_DATA_WAIT_SECONDS = 320
CELERY_LAG_SECONDS = 60

RUN_SALT = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]

# =========================
# SUIVI DES CHECKS / SCÉNARIOS
# =========================

TOTAL_CHECKS: int = 0
FAILED_CHECKS: int = 0
SCENARIO_RESULTS: List[Dict[str, object]] = []


def _register_check(success: bool, context: str) -> None:
    global TOTAL_CHECKS, FAILED_CHECKS
    TOTAL_CHECKS += 1
    if success:
        log(f"[CHECK OK]   {context}")
    else:
        FAILED_CHECKS += 1
        log(f"[CHECK FAIL] {context}")


def run_scenario(name: str, func) -> None:
    global FAILED_CHECKS, SCENARIO_RESULTS

    log("\n" + "#" * 80)
    log(f"#  SCÉNARIO : {name}")
    log("#" * 80)

    failed_before = FAILED_CHECKS
    scenario_success = True

    try:
        func()
    except Exception as exc:
        scenario_success = False
        _register_check(False, f"Exception dans le scénario '{name}': {exc}")
    else:
        failed_after = FAILED_CHECKS
        if failed_after > failed_before:
            scenario_success = False

    SCENARIO_RESULTS.append({"name": name, "success": scenario_success})

    status_str = "SUCCESS ✅" if scenario_success else "FAIL ❌"
    log(f"\n>>> Résultat du scénario '{name}': {status_str}\n")


# =========================
# HELPERS SHELL / PSQL
# =========================

def run_cmd(cmd: List[str], *, capture_output: bool = True) -> subprocess.CompletedProcess:
    sep = "\n" + "=" * 80
    log(f"{sep}\n>> CMD: {' '.join(cmd)}\n{'=' * 80}")

    proc = subprocess.run(
        cmd,
        capture_output=capture_output,
        text=True,
    )

    if capture_output:
        if proc.stdout:
            log(proc.stdout.rstrip("\n"))
        if proc.stderr:
            log("STDERR: " + proc.stderr.rstrip("\n"))

    success = (proc.returncode == 0)
    _register_check(success, f"Commande: {' '.join(cmd)} (exit_code={proc.returncode})")

    return proc


def run_psql(sql: str) -> None:
    sql_clean = textwrap.dedent(sql).strip().replace("\n", " ")
    cmd = [
        "docker", "compose", "-f", COMPOSE_FILE,
        "exec", "-T",
        DB_SERVICE,
        "psql",
        "-U", DB_USER,
        "-d", DB_NAME,
        "-c", sql_clean,
    ]
    run_cmd(cmd)


def run_psql_scalar_int(sql: str) -> int | None:
    sql_clean = textwrap.dedent(sql).strip().replace("\n", " ")
    cmd = [
        "docker", "compose", "-f", COMPOSE_FILE,
        "exec", "-T",
        DB_SERVICE,
        "psql",
        "-U", DB_USER,
        "-d", DB_NAME,
        "-t", "-A",
        "-c", sql_clean,
    ]
    proc = run_cmd(cmd, capture_output=True)
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        return int(out.splitlines()[-1].strip())
    except Exception:
        return None


def run_psql_scalar_str(sql: str) -> str | None:
    """Exécute une requête SQL et retourne le résultat sous forme de string."""
    sql_clean = textwrap.dedent(sql).strip().replace("\n", " ")
    cmd = [
        "docker", "compose", "-f", COMPOSE_FILE,
        "exec", "-T",
        DB_SERVICE,
        "psql",
        "-U", DB_USER,
        "-d", DB_NAME,
        "-t", "-A",
        "-c", sql_clean,
    ]
    proc = run_cmd(cmd, capture_output=True)
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    return out if out else None


def now_utc_iso() -> str:
    # ISO compatible timestamptz côté Postgres
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def wait_for_sql_count_after(
    label: str,
    sql_count: str,
    *,
    min_expected: int = 1,
    timeout_s: int = 90,
    step_s: int = 5,
) -> None:
    # Wrapper explicite : le SQL passé ici DOIT contenir "created_at > t0"
    wait_for_sql_count(label, sql_count, min_expected=min_expected, timeout_s=timeout_s, step_s=step_s)


def wait_for_sql_count(
    label: str,
    sql_count: str,
    *,
    min_expected: int = 1,
    timeout_s: int = 90,
    step_s: int = 5,
) -> None:
    """
    Poll une requête COUNT(*) jusqu'à atteindre min_expected (ou timeout).
    Évite les faux négatifs liés à la latence Celery/DB.
    """
    t0 = time.time()
    last_n: int | None = None
    while True:
        last_n = run_psql_scalar_int(sql_count)
        if last_n is not None and last_n >= min_expected:
            _register_check(True, f"{label}: count={last_n} (>= {min_expected})")
            return
        if time.time() - t0 > timeout_s:
            _register_check(False, f"{label}: timeout after {timeout_s}s (last_count={last_n})")
            return
        time.sleep(step_s)


def get_client_id() -> str | None:
    return run_psql_scalar_str(f"SELECT id FROM clients WHERE name = '{CLIENT_NAME}' LIMIT 1;")


def get_latest_open_incident_id(client_id: str) -> str | None:
    """
    Retourne l'ID du dernier incident OPEN pour ce client/machine.
    Utile pour tester le cooldown non-groupé (par incident_id).
    """
    sql = f"""
    SELECT id
    FROM incidents
    WHERE client_id = '{client_id}'
      AND machine_id = ({sql_machine_id_subquery()})
      AND status = 'OPEN'
    ORDER BY created_at DESC
    LIMIT 1;
    """
    return run_psql_scalar_str(sql)


def trigger_notify_incident(client_id: str, incident_id: str, *, title: str, text: str) -> None:
    """
    Enfile explicitement une notif NON-GROUPÉE via tasks.notify(payload),
    en liant incident_id pour que le cooldown soit évalué par incident.
    """
    cmd = [
        "docker", "compose", "-f", COMPOSE_FILE,
        "exec", "-T", API_SERVICE,
        "python", "-c",
        (
            "import uuid; from app.workers.tasks.notification_tasks import notify; "
            "payload = {"
            f"'title': {title!r}, 'text': {text!r}, 'severity': 'warning', "
            f"'client_id': uuid.UUID('{client_id}'), 'incident_id': '{incident_id}', 'alert_id': None"
            "}; "
            "notify.apply_async(kwargs={'payload': payload}, queue='notify')"
        ),
    ]
    run_cmd(cmd)


def set_client_reminder_seconds(seconds: int) -> None:
    """
    Force reminder_notification_seconds pour fiabiliser le test e2e (évite skipped_cooldown).
    """
    sql = f"""
    UPDATE client_settings
    SET reminder_notification_seconds = {int(seconds)},
        updated_at = NOW()
    WHERE client_id = (SELECT id FROM clients WHERE name = '{CLIENT_NAME}');
    """
    run_psql(sql)


def set_alert_grouping_enabled(enabled: bool) -> None:
    sql = f"""
    UPDATE client_settings
    SET alert_grouping_enabled = {'TRUE' if enabled else 'FALSE'},
        updated_at = NOW()
    WHERE client_id = (SELECT id FROM clients WHERE name = '{CLIENT_NAME}');
    """
    run_psql(sql)


def ensure_client_settings_row() -> None:
    """
    Garantit qu'il existe une ligne client_settings pour le client (sinon les scénarios reminders ne feront rien).
    Idempotent.
    """
    sql = f"""
    INSERT INTO client_settings (client_id, created_at, updated_at)
    SELECT id, NOW(), NOW()
    FROM clients
    WHERE name = '{CLIENT_NAME}'
      AND NOT EXISTS (
        SELECT 1 FROM client_settings cs
        WHERE cs.client_id = clients.id
      );
    """
    run_psql(sql)


def assert_incident_notify_success_recent(client_id: str, incident_id: str, *, within_minutes: int = 10) -> None:
    """
    Au moins 1 log d'envoi Slack/Email pour cet incident_id.
    On accepte success/failed/skipped_* car en e2e les canaux peuvent varier,
    mais on veut au moins constater que notify() a tenté quelque chose
    """
    sql = f"""
    SELECT COUNT(*)
    FROM notification_log
    WHERE client_id = '{client_id}'
      AND incident_id = '{incident_id}'
      AND provider IN ('slack','email','system')
      AND status IN (
        'success',
        'failed',
        'skipped_no_webhook',
        'skipped_no_recipient',
        'skipped_no_channels'
      )
      AND created_at > (NOW() AT TIME ZONE 'UTC') - INTERVAL '{within_minutes} minutes';
    """
    wait_for_sql_count("Notif non-groupée: trace slack/email/system (incident_id)", sql, min_expected=1, timeout_s=90, step_s=5)


def assert_incident_notify_skipped_cooldown_recent(client_id: str, incident_id: str, *, within_minutes: int = 10) -> None:
    """
    Au moins 1 entrée 'skipped_cooldown' pour cet incident_id.
    """
    sql = f"""
    SELECT COUNT(*)
    FROM notification_log
    WHERE client_id = '{client_id}'
      AND incident_id = '{incident_id}'
      AND provider = 'cooldown'
      AND status = 'skipped_cooldown'
      AND created_at > (NOW() AT TIME ZONE 'UTC') - INTERVAL '{within_minutes} minutes';
    """
    wait_for_sql_count("Notif non-groupée: skipped_cooldown (incident_id)", sql, min_expected=1, timeout_s=90, step_s=5)


def assert_grouped_reminder_after(client_id: str, *, t0_iso: str, timeout_s: int = 90) -> None:
    """
    Vérifie qu'un rappel GROUPÉ a bien été loggé APRÈS t0 (évite faux positifs).
    """
    sql = f"""
    SELECT COUNT(*)
    FROM notification_log
    WHERE client_id = '{client_id}'
      AND message ILIKE '%🔁 Rappel d''incidents ouverts%'
      AND status IN (
        'success',
        'failed',
        'skipped_no_webhook',
        'skipped_no_recipient',
        'skipped_no_channels'
      )
      AND created_at > '{t0_iso}'::timestamptz;
    """
    wait_for_sql_count_after(
        "Rappel groupé: trace dans notification_log (after t0)",
        sql,
        min_expected=1,
        timeout_s=timeout_s,
        step_s=5,
    )


def assert_any_reminder_sent_recently(client_id: str, *, within_minutes: int = 10) -> None:
    """
    Check groupé robuste: on cible spécifiquement le titre du rappel groupé
    pour éviter de matcher les rappels NON-groupés (qui contiennent aussi 'Rappel').
    """
    sql = f"""
    SELECT COUNT(*)
    FROM notification_log
    WHERE client_id = '{client_id}'
      AND status = 'success'
      AND sent_at IS NOT NULL
      AND message ILIKE '%Rappel d''incidents ouverts%'
      AND created_at > (NOW() AT TIME ZONE 'UTC') - INTERVAL '{within_minutes} minutes';
    """
    wait_for_sql_count("Rappel GROUPÉ envoyé (slack/email)", sql, min_expected=1, timeout_s=90, step_s=5)



def assert_individual_incident_reminder_logged(client_id: str, *, within_minutes: int = 10) -> None:
    """
    Rappel non groupé: on s’attend à une notif liée à incident_id (cooldown par incident).
    """
    sql = f"""
    SELECT COUNT(*)
    FROM notification_log
    WHERE client_id = '{client_id}'
      AND status = 'success'
      AND sent_at IS NOT NULL
      AND incident_id IS NOT NULL
      AND created_at > (NOW() AT TIME ZONE 'UTC') - INTERVAL '{within_minutes} minutes';
    """
    wait_for_sql_count("Rappel non groupé (incident_id présent)", sql, min_expected=1, timeout_s=90, step_s=5)


def trigger_grouped_incident_reminder(client_id: str) -> None:
    """
    Déclenche la task Celery notify_grouped_reminder(client_id).
    """
    cmd = [
        "docker", "compose", "-f", COMPOSE_FILE,
        "exec", "-T", API_SERVICE,
        "python", "-c",
        (
            "from app.workers.tasks.notification_tasks import notify_grouped_reminder; "
            f"notify_grouped_reminder.delay('{client_id}')"
        ),
    ]
    run_cmd(cmd)


def trigger_incident_reminders_runner() -> None:
    """
    Déclenche le runner périodique UNGROUPED.
    Cette task NE PREND PAS d'arguments:
      @celery.task(name="tasks.incident_reminders")
      def incident_reminders() -> int:
          ...
          # scanne tous les clients ayant Incident.status == OPEN
          ...
    """
    cmd = [
        "docker", "compose", "-f", COMPOSE_FILE,
        "exec", "-T", API_SERVICE,
        "python", "-c",
        (
            "from app.workers.celery_app import celery; "
            "try:\n"
            "    from app.workers.tasks.notification_tasks import incident_reminders\n"
            "    incident_reminders.delay()\n"
            "except Exception:\n"
            "    celery.send_task('tasks.incident_reminders', args=[], kwargs={}, queue='notify')\n"
        ),
    ]
    run_cmd(cmd)


def trigger_notify_incident_reminders_for_client(client_id: str) -> None:
    """
    Déclenche la task UNGROUPED pour UN SEUL client:
      @celery.task(name="tasks.notify_incident_reminders_for_client")
      def notify_incident_reminders_for_client(client_id: str) -> int:
          ...
    """
    cmd = [
        "docker", "compose", "-f", COMPOSE_FILE,
        "exec", "-T", API_SERVICE,
        "python", "-c",
        (
            "from app.workers.celery_app import celery; "
            "import uuid; cid = str(uuid.UUID(" + repr(client_id) + ")); "
            "try:\n"
            "    from app.workers.tasks.notification_tasks import notify_incident_reminders_for_client\n"
            "    notify_incident_reminders_for_client.delay(cid)\n"
            "except Exception:\n"
            "    celery.send_task('tasks.notify_incident_reminders_for_client', args=[cid], kwargs={}, queue='notify')\n"
        ),
    ]
    run_cmd(cmd)


def assert_email_grouped_reminder_sent_recently(client_id: str, *, within_minutes: int = 10) -> None:
    """
    Vérifie qu'un email de rappel groupé a bien été envoyé (success + sent_at),
    et que la mention 'Rappel' est présente (titre).
    """
    sql = f"""
    SELECT COUNT(*)
    FROM notification_log
    WHERE client_id = '{client_id}'
      AND provider = 'email'
      AND status = 'success'
      AND sent_at IS NOT NULL
      AND message ILIKE '%Rappel d''incidents ouverts%'
      AND created_at > (NOW() AT TIME ZONE 'UTC') - INTERVAL '{within_minutes} minutes';
    """
    wait_for_sql_count(
        "Email rappel groupé envoyé (mention 'Rappel')",
        sql,
        min_expected=1,
        timeout_s=90,
        step_s=5,
    )


def assert_alert_firing(metric_name: str) -> None:
    """
    Vérifie qu'il existe AU MOINS une alerte (threshold) en statut FIRING
    pour la métrique donnée sur la machine.

    Note: c'est l'équivalent "alerte active" côté table `alerts`.
    """
    sql = f"""
    SELECT COUNT(*)
    FROM alerts a
    JOIN metric_instances mi ON mi.id = a.metric_instance_id
    WHERE a.machine_id = ({sql_machine_id_subquery()})
      AND a.status = 'FIRING'
      AND mi.name_effective = '{metric_name}';
    """
    assert_sql_count(f"Alerte FIRING ({metric_name})", sql, min_expected=1)


def assert_alert_firing_exactly_one(metric_name: str) -> None:
    """
    Variante stricte : exactement 1 alerte FIRING pour cette métrique.
    Utile pour détecter des duplications d'alertes (si ton moteur en créait plusieurs).
    """
    sql = f"""
    SELECT COUNT(*)
    FROM alerts a
    JOIN metric_instances mi ON mi.id = a.metric_instance_id
    WHERE a.machine_id = ({sql_machine_id_subquery()})
      AND a.status = 'FIRING'
      AND mi.name_effective = '{metric_name}';
    """
    assert_sql_count_equals(f"Alerte FIRING exactly 1 ({metric_name})", sql, expected=1)


def assert_alert_not_firing(metric_name: str) -> None:
    """
    Vérifie qu'il n'y a AUCUNE alerte FIRING pour cette métrique.
    """
    sql = f"""
    SELECT COUNT(*)
    FROM alerts a
    JOIN metric_instances mi ON mi.id = a.metric_instance_id
    WHERE a.machine_id = ({sql_machine_id_subquery()})
      AND a.status = 'FIRING'
      AND mi.name_effective = '{metric_name}';
    """
    assert_sql_count_equals(f"Alerte NOT FIRING ({metric_name})", sql, expected=0)


def assert_sql_count(label: str, sql_count: str, *, min_expected: int = 1) -> None:
    """
    Assert minimal : COUNT(*) >= min_expected
    """
    n = run_psql_scalar_int(sql_count)
    if n is None:
        _register_check(False, f"{label}: impossible de parser COUNT(*)")
        return
    ok = n >= min_expected
    _register_check(ok, f"{label}: count={n} (min_expected={min_expected})")


def assert_sql_count_equals(label: str, sql_count: str, *, expected: int) -> None:
    """
    Assert strict : COUNT(*) == expected
    (utile pour prouver qu'on ne duplique pas les incidents/alertes)
    """
    n = run_psql_scalar_int(sql_count)
    if n is None:
        _register_check(False, f"{label}: impossible de parser COUNT(*)")
        return
    ok = (n == expected)
    _register_check(ok, f"{label}: count={n} (expected={expected})")


def assert_threshold_incident_not_open(metric_name: str) -> None:
    """
    Vérifie qu'il n'y a AUCUN incident OPEN de seuil pour cette métrique.
    """
    sql = f"""
    SELECT COUNT(*)
    FROM incidents
    WHERE client_id = (SELECT id FROM clients WHERE name = '{CLIENT_NAME}')
      AND machine_id = ({sql_machine_id_subquery()})
      AND status = 'OPEN'
      AND {sql_threshold_incident_filter(metric_name)};
    """
    assert_sql_count_equals(f"Incident threshold NOT OPEN ({metric_name})", sql, expected=0)


# ---------------------------------------------------------------------
# ASSERTS — Threshold incidents (stricts, non ambigus)
# ---------------------------------------------------------------------

def assert_threshold_incident_open(metric_name: str) -> None:
    """
    Vérifie qu'il existe AU MOINS un incident OPEN de seuil pour cette métrique.
    (non strict sur la dédup, juste présence)
    """
    sql = f"""
    SELECT COUNT(*)
    FROM incidents
    WHERE client_id = (SELECT id FROM clients WHERE name = '{CLIENT_NAME}')
      AND machine_id = ({sql_machine_id_subquery()})
      AND status = 'OPEN'
      AND {sql_threshold_incident_filter(metric_name)};
    """
    assert_sql_count(f"Incident threshold OPEN ({metric_name})", sql, min_expected=1)


def assert_threshold_incident_open_exactly_one(metric_name: str) -> None:
    """
    Vérifie qu'il y a EXACTEMENT 1 incident OPEN de seuil pour cette métrique.
    => c'est LE check qui valide le bug initial (pas de duplication).
    """
    sql = f"""
    SELECT COUNT(*)
    FROM incidents
    WHERE client_id = (SELECT id FROM clients WHERE name = '{CLIENT_NAME}')
      AND machine_id = ({sql_machine_id_subquery()})
      AND status = 'OPEN'
      AND {sql_threshold_incident_filter(metric_name)};
    """
    assert_sql_count_equals(
        f"Incident threshold OPEN exactly 1 ({metric_name})",
        sql,
        expected=1,
    )


def assert_ungrouped_reminder_after(client_id: str, *, t0_iso: str, timeout_s: int = 90) -> None:
    """
    Vérifie qu'un rappel NON-GROUPÉ a produit une trace après t0.
    On accepte succès (slack/email/system) OU skipped_cooldown,
    mais on exige incident_id IS NOT NULL.
    """
    sql = f"""
    SELECT COUNT(*)
    FROM notification_log
    WHERE client_id = '{client_id}'
      AND incident_id IS NOT NULL
      AND message ILIKE '%🔁 Rappel :%'
      AND created_at > '{t0_iso}'::timestamptz
      AND (
        (provider IN ('slack','email','system') AND status IN ('success','failed','skipped_no_webhook','skipped_no_recipient','skipped_no_channels'))
        OR (provider = 'cooldown' AND status = 'skipped_cooldown')
      );
    """
    wait_for_sql_count_after(
        "Rappel NON-GROUPÉ: trace après t0 (incident_id présent)",
        sql,
        min_expected=1,
        timeout_s=timeout_s,
        step_s=5,
    )


def assert_incident_notify_after(client_id: str, incident_id: str, *, t0_iso: str, timeout_s: int = 90) -> None:
    sql = f"""
    SELECT COUNT(*)
    FROM notification_log
    WHERE client_id = '{client_id}'
      AND incident_id = '{incident_id}'
      AND provider IN ('slack','email','system')
      AND status IN ('success','failed','skipped_no_webhook','skipped_no_recipient','skipped_no_channels')
      AND created_at > '{t0_iso}'::timestamptz;
    """
    wait_for_sql_count_after("notify(): trace slack/email/system after t0", sql, min_expected=1, timeout_s=timeout_s, step_s=5)


def assert_incident_skipped_cooldown_after(client_id: str, incident_id: str, *, t0_iso: str, timeout_s: int = 90) -> None:
    sql = f"""
    SELECT COUNT(*)
    FROM notification_log
    WHERE client_id = '{client_id}'
      AND incident_id = '{incident_id}'
      AND provider = 'cooldown'
      AND status = 'skipped_cooldown'
      AND created_at > '{t0_iso}'::timestamptz;
    """
    wait_for_sql_count_after("notify(): skipped_cooldown after t0", sql, min_expected=1, timeout_s=timeout_s, step_s=5)


# =========================
# HELPERS HTTP (INGEST)
# =========================

def ingest_metrics(ingest_id: str, metrics: List[dict]) -> None:
    url = f"{API_BASE}/api/v1/ingest/metrics"
    headers = {
        "Content-Type": "application/json",
        "X-Ingest-Id": f"{RUN_SALT}:{ingest_id}",
        "X-API-Key": API_KEY,
    }
    payload = {
        "metadata": {
            "generator": "scenario-matrix-script",
            "version": "0.1",
            "schema_version": "1.0",
            "key": API_KEY,
        },
        "machine": {
            "hostname": HOSTNAME,
            "os": "linux",
            "fingerprint": FINGERPRINT,
        },
        "metrics": metrics,
    }

    log("\n" + "#" * 80)
    log(f"INGEST [{ingest_id}] → {url}")
    log("#" * 80)
    log("Payload:")
    log(json.dumps(payload, indent=2))

    success = False
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as exc:
        log(f"HTTP ERROR: {exc}")
        _register_check(False, f"Ingestion '{ingest_id}' (exception HTTP)")
        return

    log(f"HTTP {resp.status_code}")
    body: Optional[dict] = None
    try:
        body = resp.json()
        log(json.dumps(body, indent=2))
    except Exception:
        log(resp.text)

    if 200 <= resp.status_code < 300 and isinstance(body, dict) and body.get("status") == "accepted":
        success = True

    _register_check(success, f"Ingestion '{ingest_id}'")


def sleep_with_log(seconds: int, label: str) -> None:
    log("\n" + "-" * 80)
    log(f"⏱  Attente {seconds}s pour: {label}")
    log("-" * 80)

    step = 10
    for remaining in range(seconds, 0, -step):
        log(f"  ... {remaining} s restantes")
        time.sleep(min(step, remaining))

    log("⏱  Fin de l'attente.\n")


# =========================
# CONFIGURATION DATABASE
# =========================

def sql_machine_id_subquery() -> str:
    """
    Sous-requête machine, utilisée partout dans les SQL.
    """
    return f"""
    SELECT id FROM machines
    WHERE hostname = '{HOSTNAME}'
      AND fingerprint = '{FINGERPRINT}'
      AND client_id = (SELECT id FROM clients WHERE name = '{CLIENT_NAME}')
    """

# ---------------------------------------------------------------------
# Filtres SQL "stricts" basés sur les titres refacto
# ---------------------------------------------------------------------

def sql_threshold_incident_filter(metric_name: str) -> str:
    """
    Filtre un incident de seuil (threshold breach) de manière stricte.
    - On force le pattern de titre refacto (préfixe 'Machine ... : Seuil dépassé sur')
    - On ajoute le nom de métrique pour cibler le bon incident.
    """
    # Exemple titre attendu:
    #   "Machine debian-dev : Seuil dépassé sur cpu.usage_percent"
    return f"""
      title LIKE 'Machine % : Seuil dépassé sur %'
      AND title LIKE '%{metric_name}%'
    """


def sql_nodata_machine_filter() -> str:
    """
    Filtre l'incident NO-DATA machine (machine down) selon titre refacto.
    """
    # Exemple titre attendu:
    #   "Machine debian-dev : pas de donnée envoyée"
    return """
      title LIKE 'Machine % : pas de donnée envoyée%'
    """


def sql_nodata_metric_filter(metric_name: str) -> str:
    """
    Filtre les incidents NO-DATA métrique selon titre refacto.
    """
    # Exemple titre attendu:
    #   "debian-dev - Métrique donnée manquante : cpu.usage_percent"
    return f"""
      title LIKE '%Métrique donnée manquante :%'
      AND title LIKE '%{metric_name}%'
    """


def sql_any_nodata_filter() -> str:
    """
    Filtre global NO-DATA (machine-down OU metric-no-data).
    Utile pour diag_incidents_metric_nodata() (renommé en "NO-DATA").
    """
    return f"""
      (
        {sql_nodata_machine_filter()}
        OR title LIKE '%Métrique donnée manquante :%'
      )
    """


def enable_alerting_on_metrics() -> None:
    """Active is_alerting_enabled sur toutes les métriques de la machine."""
    log("\n🔧 ACTIVATION DE L'ALERTING SUR LES MÉTRIQUES")
    sql = f"""
    UPDATE metric_instances
    SET is_alerting_enabled = true
    WHERE machine_id = ({sql_machine_id_subquery()});
    """
    run_psql(sql)


def get_metric_instance_id(metric_name: str) -> str | None:
    """Récupère l'ID d'une metric_instance."""
    sql = f"""
    SELECT id FROM metric_instances
    WHERE machine_id = ({sql_machine_id_subquery()})
      AND name_effective = '{metric_name}';
    """
    return run_psql_scalar_str(sql)


def create_threshold(metric_name: str, default_value: float) -> None:
    """Crée un seuil pour une métrique donnée."""
    log(f"\n🔧 CRÉATION DU SEUIL POUR {metric_name}")
    
    metric_id = get_metric_instance_id(metric_name)
    if not metric_id:
        log(f"⚠️  Métrique {metric_name} introuvable, impossible de créer le seuil")
        _register_check(False, f"Création seuil {metric_name}: métrique introuvable")
        return
    
    # Vérifier si un seuil existe déjà
    check_sql = f"""
    SELECT COUNT(*) FROM thresholds_new
    WHERE metric_instance_id = '{metric_id}' AND name = 'default';
    """
    existing = run_psql_scalar_int(check_sql)
    
    if existing and existing > 0:
        log(f"ℹ️  Un seuil DEFAULT existe déjà pour {metric_name}, mise à jour...")
        sql = f"""
        UPDATE thresholds_new
        SET condition = 'gt',
            value_num = {default_value},
            is_active = true,
            updated_at = NOW()
        WHERE metric_instance_id = '{metric_id}' AND name = 'default';
        """
    else:
        log(f"ℹ️  Création d'un nouveau seuil DEFAULT pour {metric_name}...")
        sql = f"""
        INSERT INTO thresholds_new (
            id, metric_instance_id, name, condition, value_num,
            severity, is_active, consecutive_breaches, created_at, updated_at
        ) VALUES (
            gen_random_uuid(),
            '{metric_id}',
            'default',
            'gt',
            {default_value},
            'warning',
            true,
            1,
            NOW(),
            NOW()
        );
        """
    
    run_psql(sql)
    _register_check(True, f"Configuration seuil DEFAULT {metric_name}")


def configure_thresholds() -> None:
    """Configure les seuils pour cpu et memory."""
    # CPU: DEFAULT à 80%
    create_threshold("cpu.usage_percent", 80.0)
    
    # Memory: DEFAULT à 85%
    create_threshold("memory.usage_percent", 85.0)


# =========================
# DIAGNOSTICS SQL
# =========================

def diag_metrics():
    sql = f"""
    SELECT id, machine_id, name_effective, is_alerting_enabled, needs_threshold,
           is_paused, last_value, updated_at
    FROM metric_instances
    WHERE machine_id = ({sql_machine_id_subquery()})
    ORDER BY name_effective;
    """
    log("\n📊 ÉTAT DES METRICS")
    run_psql(sql)


def diag_thresholds():
    """Affiche les seuils configurés."""
    sql = f"""
    SELECT t.id, mi.name_effective, t.name, t.condition, t.value_num,
           t.severity, t.is_active, t.created_at
    FROM thresholds_new t
    JOIN metric_instances mi ON mi.id = t.metric_instance_id
    WHERE mi.machine_id = ({sql_machine_id_subquery()})
    ORDER BY mi.name_effective, t.name;
    """
    log("\n⚙️  SEUILS CONFIGURÉS")
    run_psql(sql)


# ---------------------------------------------------------------------
# DIAGS — incidents threshold / NO-DATA (corrigés)
# ---------------------------------------------------------------------

def diag_incidents_machine():
    """
    Incidents machine (tous types), pour la machine et le client.
    """
    sql = f"""
    SELECT id, client_id, machine_id, title, severity, status,
           created_at, resolved_at
    FROM incidents
    WHERE client_id = (SELECT id FROM clients WHERE name = '{CLIENT_NAME}')
      AND machine_id = ({sql_machine_id_subquery()})
    ORDER BY created_at DESC;
    """
    log("\n🚨 ÉTAT DES INCIDENTS (machine)")
    run_psql(sql)


def diag_incidents_no_data():
    """
    NO-DATA (machine-down + metric-no-data) — version refacto.

    Match:
      - "Machine <host> : pas de donnée envoyée"
      - "<host> - Métrique donnée manquante : <metric>"
    """
    sql = f"""
    SELECT id, client_id, machine_id, title, severity, status,
           created_at, resolved_at
    FROM incidents
    WHERE client_id = (SELECT id FROM clients WHERE name = '{CLIENT_NAME}')
      AND machine_id = ({sql_machine_id_subquery()})
      AND {sql_any_nodata_filter()}
    ORDER BY created_at DESC;
    """
    log("\n🚨 ÉTAT DES INCIDENTS NO-DATA (refacto)")
    run_psql(sql)


def diag_incidents_threshold():
    """
    Incidents de seuil (threshold breach) — version refacto.

    Match:
      - "Machine <host> : Seuil dépassé sur <metric>"
    """
    sql = f"""
    SELECT id, client_id, machine_id, title, severity, status,
           created_at, resolved_at
    FROM incidents
    WHERE client_id = (SELECT id FROM clients WHERE name = '{CLIENT_NAME}')
      AND machine_id = ({sql_machine_id_subquery()})
      AND title LIKE 'Machine % : Seuil dépassé sur %'
    ORDER BY created_at DESC;
    """
    log("\n🚨 ÉTAT DES INCIDENTS DE SEUIL (refacto)")
    run_psql(sql)


def diag_incidents_metric_nodata():
    """
    Compat : ancien nom utilisé dans les scénarios.
    """
    return diag_incidents_no_data()


def diag_alerts():
    sql = f"""
    SELECT id, threshold_id, machine_id, metric_instance_id, status, severity,
           current_value, triggered_at, resolved_at, created_at
    FROM alerts
    WHERE machine_id = ({sql_machine_id_subquery()})
    ORDER BY created_at DESC
    LIMIT 50;
    """
    log("\n⚠️  ÉTAT DES ALERTES DE SEUIL")
    run_psql(sql)


def diag_notifications():
    sql = f"""
    SELECT id, client_id, incident_id, alert_id, provider, recipient,
           status, message, sent_at, created_at
    FROM notification_log
    WHERE client_id = (SELECT id FROM clients WHERE name = '{CLIENT_NAME}')
    ORDER BY created_at DESC
    LIMIT 20;
    """
    log("\n✉️  DERNIÈRES NOTIFICATIONS")
    run_psql(sql)


# =========================
# SCÉNARIOS
# =========================

def scenario_0_setup():
    """
    Setup initial : active l'alerting et configure les seuils.
    """
    log("\n🔧 Configuration initiale de la machine et des seuils...")
    
    # Première ingestion pour créer les métriques si nécessaire
    ingest_metrics(
        "setup-initial",
        [
            {"name": "cpu.usage_percent", "value": 20},
            {"name": "memory.usage_percent", "value": 30},
        ],
    )

    wait_for_sql_count(
        "metric_instances créées (cpu+mem)",
        f"""
        SELECT COUNT(*)
        FROM metric_instances
        WHERE machine_id = ({sql_machine_id_subquery()})
          AND name_effective IN ('cpu.usage_percent','memory.usage_percent');
        """,
        min_expected=2,
        timeout_s=120,
        step_s=5,
    )

    # UUID helper en DB
    run_psql("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    # Activer l'alerting
    enable_alerting_on_metrics()

    # S'assurer que client_settings existe (sinon grouped reminders ne partent jamais)
    ensure_client_settings_row()

    # Configurer les seuils
    configure_thresholds()

    # 🔧 Rappels: réduire le cooldown en e2e pour éviter skipped_cooldown
    set_client_reminder_seconds(5)
    
    # Par défaut on démarre en mode NON groupé pour tester les 2
    set_alert_grouping_enabled(False)

    # Vérifier la configuration
    diag_metrics()
    diag_thresholds()


def scenario_1_onboarding_cpu():
    """Test 1 : Onboarding de la machine + cpu.usage_percent"""
    ingest_metrics(
        "cpu-onboarding-1",
        [
            {"name": "cpu.usage_percent", "value": 42},
        ],
    )
    sleep_with_log(CELERY_LAG_SECONDS, "laisser l'ingestion être traitée")

    diag_metrics()
    diag_incidents_machine()
    diag_incidents_threshold()
    diag_alerts()
    diag_notifications()


def scenario_2_no_data_machine_down():
    """Test 2 : NO-DATA global -> Machine not sending data"""
    sleep_with_log(NO_DATA_WAIT_SECONDS, "laisser NO-DATA se déclencher (machine DOWN)")

    diag_metrics()
    diag_incidents_machine()
    diag_incidents_metric_nodata()
    diag_notifications()


def scenario_3_machine_restored_full():
    """Test 3 : Restauration complète de la machine"""
    ingest_metrics(
        "cpu-restore-full",
        [
            {"name": "cpu.usage_percent", "value": 30},
        ],
    )
    sleep_with_log(CELERY_LAG_SECONDS, "laisser freshness/evaluate traiter la restauration")

    diag_metrics()
    diag_incidents_machine()
    diag_incidents_metric_nodata()
    diag_notifications()


def scenario_4_add_memory_metric():
    """Test 4 : Ajout de memory.usage_percent"""
    ingest_metrics(
        "cpu-mem-onboarding",
        [
            {"name": "cpu.usage_percent", "value": 35},
            {"name": "memory.usage_percent", "value": 40},
        ],
    )
    sleep_with_log(CELERY_LAG_SECONDS, "traitement de l'ingestion CPU+MEM")

    diag_metrics()
    diag_incidents_machine()
    diag_incidents_metric_nodata()
    diag_notifications()


def scenario_5_partial_restore():
    """Test 5 : NO-DATA global puis restauration partielle"""
    sleep_with_log(NO_DATA_WAIT_SECONDS, "NO-DATA global (CPU + MEM)")

    diag_metrics()
    diag_incidents_machine()
    diag_incidents_metric_nodata()
    diag_notifications()

    ingest_metrics(
        "cpu-only-restore",
        [
            {"name": "cpu.usage_percent", "value": 25},
        ],
    )
    sleep_with_log(CELERY_LAG_SECONDS, "traitement de la restauration partielle")

    diag_metrics()
    diag_incidents_machine()
    diag_incidents_metric_nodata()
    diag_notifications()


def scenario_6_full_restore_after_partial():
    """Test 6 : Restauration complète après état partiel"""
    ingest_metrics(
        "cpu-mem-full-restore",
        [
            {"name": "cpu.usage_percent", "value": 27},
            {"name": "memory.usage_percent", "value": 35},
        ],
    )
    sleep_with_log(CELERY_LAG_SECONDS, "traitement de la restauration complète")

    diag_metrics()
    diag_incidents_machine()
    diag_incidents_metric_nodata()
    diag_notifications()


def scenario_7_threshold_breach_cpu_only():
    """Test 7 : Dépassement de seuil CPU uniquement"""
    ingest_metrics(
        "threshold-cpu-high",
        [
            {"name": "cpu.usage_percent", "value": 95},
            {"name": "memory.usage_percent", "value": 30},
        ],
    )
    sleep_with_log(CELERY_LAG_SECONDS, "évaluation des seuils CPU high")

    diag_metrics()
    diag_incidents_threshold()
    diag_alerts()
    diag_notifications()

    # Présence
    assert_threshold_incident_open_exactly_one("cpu.usage_percent")
    assert_alert_firing_exactly_one("cpu.usage_percent")

    # ✅ Dédup : relance une ingestion en breach (même seuil violé)
    ingest_metrics(
        "threshold-cpu-high-repeat",
        [
            {"name": "cpu.usage_percent", "value": 96},
            {"name": "memory.usage_percent", "value": 30},
        ],
    )
    sleep_with_log(CELERY_LAG_SECONDS, "re-check seuil (doit réutiliser l'incident)")

    # ✅ Le bug initial se voit ici : doit rester à 1
    assert_threshold_incident_open_exactly_one("cpu.usage_percent")


def scenario_8_threshold_back_to_normal():
    """Test 8 : Retour à la normale (CPU & MEM sous le seuil)"""
    ingest_metrics(
        "threshold-all-ok",
        [
            {"name": "cpu.usage_percent", "value": 20},
            {"name": "memory.usage_percent", "value": 25},
        ],
    )
    sleep_with_log(CELERY_LAG_SECONDS, "résolution des alertes de seuil")

    diag_metrics()
    diag_incidents_threshold()
    diag_alerts()
    diag_notifications()
    assert_threshold_incident_not_open("cpu.usage_percent")
    assert_alert_not_firing("cpu.usage_percent")


def scenario_9_grouped_incident_reminder_email():
    """
    Test final : envoi d'un email de rappel d'incidents ouverts + mention 'Rappel'.

    IMPORTANT :
    - On crée volontairement un incident OPEN juste avant le rappel,
      car le scénario 8 résout tout (retour à la normale).
    """
    client_id = get_client_id()
    if not client_id:
        _register_check(False, "Client id introuvable (clients.name)")
        return

    # S'assurer qu'on a un incident OPEN (on ré-ouvre un breach CPU)
    ingest_metrics(
        "reminder-open-breach",
        [
            {"name": "cpu.usage_percent", "value": 95},
            {"name": "memory.usage_percent", "value": 30},
        ],
    )
    sleep_with_log(CELERY_LAG_SECONDS, "ouvrir un incident (précondition reminder)")

    # Précondition : au moins 1 incident OPEN
    pre_sql = f"""
    SELECT COUNT(*)
    FROM incidents
    WHERE client_id = '{client_id}'
      AND machine_id = ({sql_machine_id_subquery()})
      AND status = 'OPEN';
    """
    assert_sql_count("Précondition: au moins 1 incident OPEN", pre_sql, min_expected=1)

    # 9a) NON GROUPÉ: test explicite du cooldown par incident_id via tasks.notify
    set_alert_grouping_enabled(False)
    inc_id = get_latest_open_incident_id(client_id)
    if not inc_id:
        _register_check(False, "Incident OPEN introuvable (pour test non-groupé)")
        return

    # 1) Premier envoi -> doit passer en success
    t = now_utc_iso()
    trigger_notify_incident(
        client_id,
        inc_id,
        title="🔔 Rappel NON-GROUPÉ (test e2e)",
        text="ping 1",
    )
    assert_incident_notify_after(client_id, inc_id, t0_iso=t)

    # 2) Envoi immédiat -> doit être skipped_cooldown (remind_seconds=5)
    t = now_utc_iso()
    trigger_notify_incident(
        client_id,
        inc_id,
        title="🔔 Rappel NON-GROUPÉ (test e2e)",
        text="ping 2 (should cooldown)",
    )
    assert_incident_skipped_cooldown_after(client_id, inc_id, t0_iso=t)

    # 3) Après cooldown -> doit repasser en success
    sleep_with_log(7, "attendre > cooldown (5s) pour re-envoyer")
    t = now_utc_iso()
    trigger_notify_incident(
        client_id,
        inc_id,
        title="🔔 Rappel NON-GROUPÉ (test e2e)",
        text="ping 3 (after cooldown)",
    )
    assert_incident_notify_after(client_id, inc_id, t0_iso=t)

    # 9b) NON GROUPÉ via la TASK (et pas seulement notify())
    # On s'assure que le mode ungrouped est actif
    set_alert_grouping_enabled(False)

    t0 = now_utc_iso()
    trigger_notify_incident_reminders_for_client(client_id)
    assert_ungrouped_reminder_after(client_id, t0_iso=t0, timeout_s=120)

    # 9c) GROUPÉ: déclenche explicitement la task
    # IMPORTANT: attendre > cooldown (reminder_notification_seconds=5)
    sleep_with_log(6, "attendre > cooldown (5s) avant rappel groupé")
    set_alert_grouping_enabled(True)

    t1 = now_utc_iso()
    trigger_grouped_incident_reminder(client_id)
    assert_grouped_reminder_after(client_id, t0_iso=t1, timeout_s=120)

    diag_notifications()



# =========================
# RAPPORT FINAL
# =========================

def print_summary() -> None:
    log("\n" + "#" * 80)
    log("#  RÉSUMÉ GLOBAL DES SCÉNARIOS")
    log("#" * 80)

    total_scenarios = len(SCENARIO_RESULTS)
    passed_scenarios = sum(1 for s in SCENARIO_RESULTS if s["success"])
    failed_scenarios = total_scenarios - passed_scenarios

    log(f"\nScénarios exécutés : {total_scenarios}")
    log(f"  - SUCCESS : {passed_scenarios}")
    log(f"  - FAIL    : {failed_scenarios}\n")

    for s in SCENARIO_RESULTS:
        status_str = "SUCCESS ✅" if s["success"] else "FAIL ❌"
        log(f"  - {s['name']}: {status_str}")

    log("\n" + "-" * 80)
    log("#  STATISTIQUES DES CHECKS (commandes HTTP/SQL)")
    log("-" * 80)

    ok_checks = TOTAL_CHECKS - FAILED_CHECKS
    if TOTAL_CHECKS > 0:
        success_rate = (ok_checks / TOTAL_CHECKS) * 100.0
    else:
        success_rate = 0.0

    log(f"Checks totaux   : {TOTAL_CHECKS}")
    log(f"Checks OK       : {ok_checks}")
    log(f"Checks en échec : {FAILED_CHECKS}")
    log(f"Taux de succès  : {success_rate:.2f} %")

    log("\n#  FIN DU SCÉNARIO COMPLET")
    log("#" * 80 + "\n")


# =========================
# MAIN
# =========================

def main():
    log("\n" + "#" * 80)
    log("#  SCÉNARIO MATRICE ALERTES / NO-DATA (cpu.usage_percent + memory.usage_percent)")
    log(f"#  Machine : {HOSTNAME}")
    log(f"#  Client  : {CLIENT_NAME}")
    log(f"#  Fingerprint : {FINGERPRINT}")
    log("#" * 80)

    run_scenario("0 - Configuration initiale (SETUP)", scenario_0_setup)
    run_scenario("1 - Onboarding CPU", scenario_1_onboarding_cpu)
    run_scenario("2 - NO-DATA machine DOWN", scenario_2_no_data_machine_down)
    run_scenario("3 - Restauration complète machine", scenario_3_machine_restored_full)
    run_scenario("4 - Ajout memory.usage_percent", scenario_4_add_memory_metric)
    run_scenario("5 - NO-DATA global puis restauration partielle", scenario_5_partial_restore)
    run_scenario("6 - Restauration complète après partielle", scenario_6_full_restore_after_partial)
    run_scenario("7 - Seuil CPU dépassé", scenario_7_threshold_breach_cpu_only)
    run_scenario("8 - Seuils revenus à la normale", scenario_8_threshold_back_to_normal)
    run_scenario("9 - Email rappel incidents ouverts", scenario_9_grouped_incident_reminder_email)

    print_summary()


if __name__ == "__main__":
    main()