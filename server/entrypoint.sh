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
  log "Applying migrations…"
  alembic upgrade head

  if [[ "${API_COVERAGE:-0}" == "1" ]] && _have_coverage; then
    log "API_COVERAGE=1 → running uvicorn under coverage"
    cd /app/server
    _run_with_coverage "/app/server/.coverage.api" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
  else
    [[ "${API_COVERAGE:-0}" == "1" ]] && log "coverage not found → starting uvicorn normally"
    log "Starting uvicorn…"
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
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
