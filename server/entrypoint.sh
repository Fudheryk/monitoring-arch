#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# ENTRYPOINT - Monitoring API
# =============================================================================
# Starts:
#  - API (default or "api")
#  - Celery worker/beat
#  - Any other command (migrations, shell, etc.)
#
# Features:
#  - Auto-detects DB schema state and runs Alembic migrations
#  - Coverage support (API_COVERAGE=1, WORKER_COVERAGE=1, BEAT_COVERAGE=1)
#  - Proxy headers support for Uvicorn (PROXY_HEADERS=1)
#
# IMPORTANT FIX:
#  - Uses settings.DATABASE_URL (built from DB_PASSWORD) instead of
#    raw DATABASE_URL env var which doesn't exist in .env.production
# =============================================================================

log() { echo "[$(date -u +'%H:%M:%S')] $*"; }

_have_coverage() {
  python - <<'PY' >/dev/null 2>&1 || exit 1
import importlib, sys
sys.exit(0 if importlib.util.find_spec("coverage") else 1)
PY
}

_run_with_coverage() {
  # $1: COVERAGE_FILE path
  # remaining: module and args ("-m <module> ...")
  local cov_file="${1}"; shift
  export COVERAGE_RCFILE="${COVERAGE_RCFILE:-/app/.coveragerc}"
  export COVERAGE_PROCESS_START="${COVERAGE_PROCESS_START:-${COVERAGE_RCFILE}}"
  export COVERAGE_FILE="${cov_file}"
  exec python -m coverage run "$@"
}

run_api() {
  log "Checking DB schema state…"

  # =========================================================================
  # DB Schema Detection
  # =========================================================================
  # Détecte l'état du schéma PostgreSQL:
  #   10 = alembic_version présent → alembic upgrade head
  #   20 = base vide (aucune table app) → alembic upgrade head
  #   30 = schéma complet mais sans alembic_version → alembic stamp + upgrade
  #   40 = état incohérent → erreur (intervention manuelle requise)
  #
  # IMPORTANT:
  # - Utilise settings.DATABASE_URL (construit depuis DB_PASSWORD)
  # - La variable DATABASE_URL brute n'existe PAS dans .env.production
  # =========================================================================
  
  state=$(python - <<'PY'
import sys
from app.core.config import settings
from sqlalchemy import create_engine, inspect

# ✅ Utilise settings.DATABASE_URL (construit depuis DB_PASSWORD)
eng = create_engine(settings.DATABASE_URL)
insp = inspect(eng)

tables = set(insp.get_table_names(schema="public"))
have_alembic = "alembic_version" in tables

# Liste complète des tables applicatives attendues
# NOTE: Correspond aux modèles SQLAlchemy dans app/infrastructure/persistence/database/models/
app_tables = {
    "clients", 
    "api_keys", 
    "machines", 
    "metric_definitions", 
    "metric_instances",
    "samples", 
    "thresholds",  # ✅ Renommé de thresholds_new
    "alerts", 
    "incidents", 
    "http_targets",
    "ingest_events", 
    "outbox_events", 
    "notification_log", 
    "client_settings",
    "users", 
    "client_incident_counter", 
    "threshold_templates"
}

# Détermination de l'état
if have_alembic:
    # Schéma versionné Alembic → migration normale
    code = 10
elif tables.isdisjoint(app_tables):
    # Base "vide" (aucune table app) → migration initiale
    code = 20
elif app_tables.issubset(tables):
    # Schéma complet mais sans alembic_version → stamp puis upgrade
    code = 30
else:
    # État incohérent (tables partielles) → arrêt
    code = 40

print(code)
sys.exit(0)
PY
)

  # =========================================================================
  # Actions selon l'état détecté
  # =========================================================================
  
  case "$state" in
    10)
      log "✅ alembic_version présent → alembic upgrade head"
      alembic upgrade head
      ;;
    20)
      log "✅ Fresh DB (no app tables) → alembic upgrade head"
      alembic upgrade head
      ;;
    30)
      log "⚠️  Schema présent mais sans alembic_version → alembic stamp head + upgrade"
      alembic stamp head
      alembic upgrade head
      ;;
    40)
      log "❌ Inconsistent DB schema detected → refusing to auto-heal."
      log "   Please fix manually or drop/recreate the database."
      exit 1
      ;;
    *)
      log "❌ Unexpected state from schema checker: $state"
      exit 1
      ;;
  esac

  # =========================================================================
  # Uvicorn Configuration
  # =========================================================================
  # Options pour reverse proxy (nginx):
  # - PROXY_HEADERS=1 → ajoute --proxy-headers
  # - FORWARDED_ALLOW_IPS → IPs autorisées (ex: "127.0.0.1,172.30.0.0/16")
  #   Default: 127.0.0.1 si non fourni
  # =========================================================================
  
  UVICORN_EXTRA_ARGS=()
  
  if [[ "${PROXY_HEADERS:-0}" == "1" ]]; then
    UVICORN_EXTRA_ARGS+=(--proxy-headers)
    UVICORN_EXTRA_ARGS+=(--forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}")
    log "🔧 Proxy headers enabled: forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-127.0.0.1}"
  fi

  # =========================================================================
  # Démarrage Uvicorn (avec ou sans coverage)
  # =========================================================================
  
  if [[ "${API_COVERAGE:-0}" == "1" ]] && _have_coverage; then
    log "📊 API_COVERAGE=1 → running uvicorn under coverage"
    cd /app/server
    _run_with_coverage "/app/server/.coverage.api" \
      -m uvicorn app.main:app \
      --host 0.0.0.0 \
      --port 8000 \
      "${UVICORN_EXTRA_ARGS[@]}"
  else
    [[ "${API_COVERAGE:-0}" == "1" ]] && log "⚠️  coverage not found → starting uvicorn normally"
    log "🚀 Starting uvicorn (API)..."
    exec uvicorn app.main:app \
      --host 0.0.0.0 \
      --port 8000 \
      "${UVICORN_EXTRA_ARGS[@]}"
  fi
}

run_celery_like() {
  # =========================================================================
  # Celery Worker / Beat
  # =========================================================================
  # Wrap celery worker/beat in coverage when requested:
  # - WORKER_COVERAGE=1 → celery worker under coverage
  # - BEAT_COVERAGE=1 → celery beat under coverage
  #
  # Example invocations:
  #   celery -A app.workers.celery_app.celery worker -l info
  #   celery -A app.workers.celery_app.celery beat -l info
  # =========================================================================
  
  local sub="${1:-}"
  shift || true

  if [[ "${sub}" == "worker" ]]; then
    if [[ "${WORKER_COVERAGE:-0}" == "1" ]] && _have_coverage; then
      log "📊 WORKER_COVERAGE=1 → celery worker under coverage"
      _run_with_coverage "/app/server/.coverage.worker" \
        -m celery -A app.workers.celery_app.celery worker "$@"
    else
      [[ "${WORKER_COVERAGE:-0}" == "1" ]] && log "⚠️  coverage not found → celery worker normally"
      log "🚀 Starting celery worker..."
      exec celery -A app.workers.celery_app.celery worker "$@"
    fi
    
  elif [[ "${sub}" == "beat" ]]; then
    if [[ "${BEAT_COVERAGE:-0}" == "1" ]] && _have_coverage; then
      log "📊 BEAT_COVERAGE=1 → celery beat under coverage"
      _run_with_coverage "/app/server/.coverage.beat" \
        -m celery -A app.workers.celery_app.celery beat "$@"
    else
      [[ "${BEAT_COVERAGE:-0}" == "1" ]] && log "⚠️  coverage not found → celery beat normally"
      log "🚀 Starting celery beat..."
      exec celery -A app.workers.celery_app.celery beat "$@"
    fi
    
  else
    # Autres commandes celery (inspect, etc.)
    exec celery "${sub}" "$@"
  fi
}

# =============================================================================
# MAIN ENTRYPOINT LOGIC
# =============================================================================

if [[ $# -eq 0 || "${1:-}" == "api" ]]; then
  # Pas d'argument ou "api" explicite → démarrer l'API
  run_api
else
  if [[ "${1:-}" == "celery" ]]; then
    # Commande celery
    shift
    run_celery_like "$@"
  else
    # Toute autre commande (shell, migrations manuelles, etc.)
    exec "$@"
  fi
fi