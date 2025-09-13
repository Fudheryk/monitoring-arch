#!/usr/bin/env bash
set -euo pipefail

# Ce script démarre soit :
#  - l'API FastAPI (par défaut, ou si "api" est passé en argument)
#  - n'importe quelle autre commande (celery worker/beat, migrations, shell…)
#
# Particularités :
#  - applique les migrations Alembic avant de lancer l'API
#  - si API_COVERAGE=1, lance uvicorn sous "coverage run" et écrit
#    le fichier de data coverage dans /app/server/.coverage
#  - si "coverage" n'est pas installé dans l'image : fallback vers uvicorn simple
#
# NB : pas de wait-for-it ici ; les healthchecks docker-compose font foi.

log() { echo "[$(date -u +'%H:%M:%S')] $*"; }

run_api() {
  # 1) DB migrations (fail fast if problem)
  log "Applying migrations…"
  alembic upgrade head

  # 2) Coverage mode?
  if [[ "${API_COVERAGE:-0}" == "1" ]]; then
    log "API_COVERAGE=1 → tentative de démarrer uvicorn sous coverage"
    cd /app/server

    # Where to write coverage data (read by make cov-combine)
    export COVERAGE_FILE=/app/server/.coverage
    # Where to read coverage config (mounted by docker-compose)
    export COVERAGE_RCFILE="${COVERAGE_RCFILE:-/app/.coveragerc}"

    # Start coverage in background (PID 1), receive SIGINT when docker stop
    exec python -m coverage run -m uvicorn app.main:app --host 0.0.0.0 --port 8000

  else
    log "Starting uvicorn…"
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
  fi
}

# ─────────────────────────────────────────────────────────────

if [[ $# -eq 0 || "${1:-}" == "api" ]]; then
  # Démarrage API par défaut (ou si "api" est passé en arg)
  run_api
else
  # Pour worker/beat (ou toute autre commande passée par docker-compose)
  exec "$@"
fi
