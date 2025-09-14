#!/usr/bin/env bash
# ccc: Vérif globale: unit -> (stack up) -> integration -> e2e -> combine coverage -> (stack down)
set -euo pipefail

# --- Config -------------------------------------------------------------------
: "${API:=http://localhost:8000}"       # ccc: endpoint public de l'API
: "${KEY:=dev-apikey-123}"              # ccc: API key par défaut (doit matcher ta conf)
THRESHOLD="${THRESHOLD:-70}"            # ccc: seuil de couverture attendu (override: THRESHOLD=0 ...)
BUILD="${BUILD:-0}"                     # ccc: BUILD=1 pour forcer docker compose --build
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- Helpers ------------------------------------------------------------------
log() { printf "\n\033[1;34m[%s]\033[0m %s\n" "verify" "$*"; }
cleanup_coverage() {
  log "nettoyage fragments de coverage…"
  find "$PROJECT_ROOT" -maxdepth 1 -type f -name ".coverage*" ! -name ".coveragerc" -print -delete || true
  find "$PROJECT_ROOT/server" -maxdepth 1 -type f -name ".coverage*" ! -name ".coveragerc" -print -delete || true
}
wait_api() {
  log "attente de l'API ($API/api/v1/health)…"
  for i in {1..60}; do
    if curl -fsS -H "X-API-Key: $KEY" "$API/api/v1/health" >/dev/null 2>&1; then
      log "API ok."
      return 0
    fi
    sleep 2
  done
  log "❌ API indisponible après attente."
  return 1
}
dc() {
  # ccc: wrapper docker compose à la racine docker/
  ( cd "$PROJECT_ROOT/docker" && docker compose "$@" )
}

# --- Start --------------------------------------------------------------------
cd "$PROJECT_ROOT"

# 0) Nettoyage
cleanup_coverage

# 1) UNIT TESTS (host) -> .coverage.host
log "pytest UNIT (host) + coverage → .coverage.host"
COVERAGE_FILE=".coverage.host" \
pytest server/tests/unit \
  --maxfail=1 \
  --cov=server/app --cov-report=term-missing --cov-config=.coveragerc --cov-branch \
  --cov-fail-under=0

# 2) STACK UP (api/worker/beat/db/redis)
log "docker compose up (coverage activé dans containers)…"
if [[ "$BUILD" == "1" ]]; then
  dc --env-file ../.env.docker up -d --build
else
  dc --env-file ../.env.docker up -d
fi

# Stopper proprement à la fin quoi qu’il arrive
trap 'log "docker compose down -v"; dc -f docker-compose.yml down -v || true' EXIT

# 3) WAIT API
wait_api

# 4) INTEGRATION TESTS (host)

# forcer les tests host à parler au Postgres exposé par Docker (localhost:5432)
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://postgres:postgres@localhost:5432/monitoring?connect_timeout=5}"
log "DATABASE_URL (host) -> $DATABASE_URL"

log "pytest INTEGRATION (host) + coverage append"
COVERAGE_FILE=".coverage.host" \
pytest server/tests/integration \
  --maxfail=1 \
  --cov=server/app --cov-report=term-missing --cov-config=.coveragerc --cov-branch \
  --cov-append --cov-fail-under=0

# 5) E2E TESTS (host -> containers)
log "pytest E2E (host → API) + coverage append"
export E2E_STACK_UP=1
COVERAGE_FILE=".coverage.host" \
pytest server/tests/e2e -m e2e \
  --maxfail=1 \
  --cov=server/app --cov-report=term-missing --cov-config=.coveragerc --cov-branch \
  --cov-append --cov-fail-under=0

# 6) COMBINE COVERAGE (host + fragments containers)
log "combine coverage (host + containers)…"
files=""
test -s .coverage.host && files="$files .coverage.host"
# ccc: on ajoute *tous* les fragments côté /server (api/worker/beat écrivent là)
api_worker_files="$(find server -maxdepth 1 -type f -name '.coverage*' ! -name '.coveragerc' -size +0c -printf ' %p' 2>/dev/null || true)"
files="$files$api_worker_files"

if [[ -z "${files// /}" ]]; then
  echo "❌ Aucun fichier coverage trouvé à combiner"; exit 1;
fi

log "⏳ Combine: $files"
COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc python -m coverage combine -q $files
COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc python -m coverage report -m --fail-under="$THRESHOLD"
COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc python -m coverage xml -o coverage.xml

log "✅ OK — couverture ≥ ${THRESHOLD}%"
