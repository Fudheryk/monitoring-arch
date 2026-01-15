#!/usr/bin/env bash
set -euo pipefail

# Starts:
#  - API (default or "api")
#  - or any other command (celery worker/beat, migrations, shell…)
#
# Extras:
#  - Alembic migrations before API
#  - API_COVERAGE=1 → uvicorn under coverage (writes /app/server/.coverage.api)
#  - WORKER_COVERAGE=1 → celery worker under coverage (→ /app/server/.coverage.worker)
#  - BEAT_COVERAGE=1 → celery beat under coverage (→ /app/server/.coverage.beat)
#  - If "coverage" not installed → graceful fallback to plain commands.

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

  # Décide le chemin: 10=has_alembic, 20=fresh, 30=has_schema_no_alembic, 40=incoherent
  state=$(python - <<'PY'
import os, sys
from sqlalchemy import create_engine, inspect

url = os.environ.get("DATABASE_URL")
if not url:
    print("no-db-url"); sys.exit(99)

eng = create_engine(url)
insp = inspect(eng)

tables = set(insp.get_table_names(schema="public"))
have_alembic = "alembic_version" in tables

# Ensemble minimal des tables "app" (ajuste si nécessaire)
app_tables = {
    "clients","api_keys","machines","metrics","samples","thresholds",
    "alerts","incidents","http_targets","ingest_events","outbox_events",
    "notification_log","client_settings","users"
}

if have_alembic:
    code = 10              # Schéma versionné → upgrade
elif tables.isdisjoint(app_tables):
    code = 20              # Base “vide” (pas de tables app) → upgrade
elif app_tables.issubset(tables):
    code = 30              # Schéma complet mais sans alembic_version → stamp head, puis upgrade
else:
    code = 40              # État incohérent → stop (intervention humaine)
print(code)
sys.exit(0)
PY
)

  case "$state" in
    10)
      log "alembic_version present → alembic upgrade head"
      alembic upgrade head
      ;;
    20)
      log "fresh DB (no app tables) → alembic upgrade head"
      alembic upgrade head
      ;;
    30)
      log "schema present but no alembic_version → alembic stamp head + upgrade head"
      alembic stamp head
      alembic upgrade head
      ;;
    40)
      log "Inconsistent DB schema detected → refusing to auto-heal. Please fix manually."
      exit 1
      ;;
    *)
      log "Unexpected state from checker: $state"
      exit 1
      ;;
  esac

  # -------------------------------------------------------------------
  # Uvicorn options for reverse proxy (nginx)
  # - PROXY_HEADERS=1 -> add --proxy-headers
  # - FORWARDED_ALLOW_IPS="127.0.0.1,172.30.0.0/16" (prod) or "*" (dev)
  # -------------------------------------------------------------------
  UVICORN_EXTRA_ARGS=()
  if [[ "${PROXY_HEADERS:-0}" == "1" ]]; then
    UVICORN_EXTRA_ARGS+=(--proxy-headers)
    # Default: trust only localhost if not provided
    UVICORN_EXTRA_ARGS+=(--forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}")
  fi

  # Démarrage Uvicorn (inchangé)
  if [[ "${API_COVERAGE:-0}" == "1" ]] && _have_coverage; then
    log "API_COVERAGE=1 → running uvicorn under coverage"
    cd /app/server
    _run_with_coverage "/app/server/.coverage.api" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 "${UVICORN_EXTRA_ARGS[@]}"
  else
    [[ "${API_COVERAGE:-0}" == "1" ]] && log "coverage not found → starting uvicorn normally"
    log "Starting uvicorn…"
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 "${UVICORN_EXTRA_ARGS[@]}"
  fi
}


run_celery_like() {
  # Wrap celery worker/beat in coverage when requested
  # Example invocations reaching here:
  #   celery -A app.workers.celery_app.celery worker ...
  #   celery -A app.workers.celery_app.celery beat ...
  local sub="${1:-}"
  shift || true

  if [[ "${sub}" == "worker" ]]; then
    if [[ "${WORKER_COVERAGE:-0}" == "1" ]] && _have_coverage; then
      log "WORKER_COVERAGE=1 → celery worker under coverage"
      _run_with_coverage "/app/server/.coverage.worker" -m celery -A app.workers.celery_app.celery worker "$@"
    else
      [[ "${WORKER_COVERAGE:-0}" == "1" ]] && log "coverage not found → celery worker normally"
      exec celery -A app.workers.celery_app.celery worker "$@"
    fi
  elif [[ "${sub}" == "beat" ]]; then
    if [[ "${BEAT_COVERAGE:-0}" == "1" ]] && _have_coverage; then
      log "BEAT_COVERAGE=1 → celery beat under coverage"
      _run_with_coverage "/app/server/.coverage.beat" -m celery -A app.workers.celery_app.celery beat "$@"
    else
      [[ "${BEAT_COVERAGE:-0}" == "1" ]] && log "coverage not found → celery beat normally"
      exec celery -A app.workers.celery_app.celery beat "$@"
    fi
  else
    # Not worker/beat → just exec celery with whatever args
    exec celery "${sub}" "$@"
  fi
}

# ─────────────────────────────────────────────────────────────

if [[ $# -eq 0 || "${1:-}" == "api" ]]; then
  run_api
else
  if [[ "${1:-}" == "celery" ]]; then
    shift
    run_celery_like "$@"
  else
    # Any other command
    exec "$@"
  fi
fi
