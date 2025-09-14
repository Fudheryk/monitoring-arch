#!/usr/bin/env bash
#  ─────────────────────────────────────────────────────────────────────────────
# Vérif globale: unit -> (stack up) -> integration -> e2e -> combine coverage -> (stack down)
# - Crée/active un venv "CI-like" (.venv-ci) et installe les deps (dont psycopg)
# - Force PYTHONPATH=server pour que "app.*" soit importable
# - Unit  : SQLite in-memory, coverage stricte (fail-under=60 ici, puis seuil global)
# - Integ/E2E : stack Docker (db/redis/api/worker), migrations Alembic, tests host
# - Combine : coverage host + fragments écrits par les containers sous ./server
# - Utilise *toujours* l'override docker-compose.coverage.yml pour capturer la
#   couverture côté API/worker.
#  ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# --- Config -------------------------------------------------------------------
: "${API:=http://localhost:8000}"           # endpoint public de l'API
: "${KEY:=dev-apikey-123}"                  # API key par défaut
: "${THRESHOLD:=70}"                        # seuil de couverture finale (report --fail-under)
: "${BUILD:=0}"                             # BUILD=1 pour docker compose --build
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- Helpers ------------------------------------------------------------------
log() { printf "\n\033[1;34m[%s]\033[0m %s\n" "verify" "$*"; }

cleanup_coverage() {
  log "nettoyage fragments de coverage…"
  # fragments host (racine)
  find "$PROJECT_ROOT" -maxdepth 1 -type f -name ".coverage*" ! -name ".coveragerc" -print -delete || true
  # fragments containers (montés dans ./server)
  find "$PROJECT_ROOT/server" -maxdepth 1 -type f -name ".coverage*" ! -name ".coveragerc" -print -delete || true
  rm -rf "$PROJECT_ROOT/htmlcov" || true
  rm -f  "$PROJECT_ROOT/coverage.xml" || true
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

# Wrapper docker compose (toujours avec l'override coverage)
dc() {
  # on ajoute systématiquement le fichier coverage pour que l’API/worker
  #      émettent des fichiers .coverage.* dans ./server
  ( cd "$PROJECT_ROOT/docker" && docker compose \
      -f docker-compose.yml \
      -f docker-compose.coverage.yml \
      "$@" )
}

ensure_env_docker() {
  # prépare .env.docker à la racine et copie dans docker/.env.docker
  if [[ ! -f "$PROJECT_ROOT/.env.docker" ]]; then
    if [[ -f "$PROJECT_ROOT/.env.example" ]]; then
      cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env.docker"
    else
      echo "ERROR: .env.example introuvable à la racine" >&2
      exit 1
    fi
    # impose quelques défauts sûrs pour la CI/locale
    awk 'BEGIN{pslack=0; prem=0}
         /^SLACK_WEBHOOK=/ {print "SLACK_WEBHOOK=http://httpbin:80/status/204"; pslack=1; next}
         /^ALERT_REMINDER_MINUTES=/ {print "ALERT_REMINDER_MINUTES=1"; prem=1; next}
         {print}
         END{
           if(!pslack) print "SLACK_WEBHOOK=http://httpbin:80/status/204";
           if(!prem)   print "ALERT_REMINDER_MINUTES=1";
           print "STUB_SLACK=1";
         }' "$PROJECT_ROOT/.env.docker" > "$PROJECT_ROOT/.env.docker.tmp"
    mv "$PROJECT_ROOT/.env.docker.tmp" "$PROJECT_ROOT/.env.docker"
  fi
  cp "$PROJECT_ROOT/.env.docker" "$PROJECT_ROOT/docker/.env.docker"
}

dump_logs_on_error() {
  echo "──── docker compose ps";         (cd "$PROJECT_ROOT/docker" && docker compose ps) || true
  echo "──── api logs (tail 200)";      (cd "$PROJECT_ROOT/docker" && docker compose logs api    | tail -n 200) || true
  echo "──── worker logs (tail 200)";   (cd "$PROJECT_ROOT/docker" && docker compose logs worker | tail -n 200) || true
  echo "──── db logs (tail 120)";       (cd "$PROJECT_ROOT/docker" && docker compose logs db     | tail -n 120) || true
}

# --- Start --------------------------------------------------------------------
cd "$PROJECT_ROOT"

# 0) Venv "CI-like" + deps
log "prépare venv .venv-ci + dépendances…"
python -m venv .venv-ci
# shellcheck disable=SC1091
source .venv-ci/bin/activate
python -m pip install -U pip
pip install -r requirements-dev.txt
# Requis pour INTEG/E2E (accès Postgres côté host)
pip install "psycopg[binary]>=3.1,<4.0"

export PYTHONPATH=server

# 0bis) Nettoyage coverage
cleanup_coverage

# 1) UNIT TESTS (host) -> .coverage.host
#    (ENV SQLite et Celery eager sont gérés par server/tests/unit/conftest.py)
log "pytest UNIT (host) + coverage → .coverage.host"
COVERAGE_FILE=".coverage.host" \
pytest -m "unit" -n auto \
  --maxfail=1 \
  --cov=server/app --cov-report=term-missing --cov-config=.coveragerc --cov-branch \
  --cov-fail-under=60

# 2) STACK UP (db/redis/api/worker) + migrations (toujours avec override coverage)
log "docker compose up…"
ensure_env_docker
if [[ "$BUILD" == "1" ]]; then
  dc --env-file ../.env.docker up -d --build db redis api worker
else
  dc --env-file ../.env.docker up -d db redis api worker
fi

# Stopper proprement à la fin quoi qu’il arrive
trap 'log "docker compose down -v"; dc --env-file ../.env.docker down -v || true' EXIT

# Wait DB ready
log "attente DB (pg_isready)…"
for i in {1..60}; do
  if dc --env-file ../.env.docker exec -T db pg_isready -U postgres >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# Migrations Alembic dans le conteneur API (chemins typiques de ton projet)
log "alembic upgrade head (dans le conteneur api)…"
if ! dc --env-file ../.env.docker run --rm -w /app/server api alembic -c /app/server/alembic.ini upgrade head; then
  echo "❌ Alembic upgrade failed"; dump_logs_on_error; exit 1
fi

# Wait API healthy (healthcheck)
wait_api

# 3) INTEGRATION TESTS (host)
#    (force host → Postgres exposé par Docker)
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://postgres:postgres@localhost:5432/monitoring?connect_timeout=5}"
log "DATABASE_URL (host) -> $DATABASE_URL"

log "pytest INTEGRATION (host) + coverage append"
COVERAGE_FILE=".coverage.host" \
pytest -m "integration" \
  --maxfail=1 \
  --cov=server/app --cov-report=term-missing --cov-config=.coveragerc --cov-branch \
  --cov-append --cov-fail-under=0

# 4) E2E TESTS (host → containers)
log "pytest E2E (host → API) + coverage append"
export E2E_STACK_UP=1
COVERAGE_FILE=".coverage.host" \
pytest -m "e2e" \
  --maxfail=1 \
  --cov=server/app --cov-report=term-missing --cov-config=.coveragerc --cov-branch \
  --cov-append --cov-fail-under=0

# ⚠️ IMPORTANT : stop API/worker pour *flusher* les fichiers coverage côté containers
log "stop API/worker (flush coverage data)…"
dc --env-file ../.env.docker stop api || true
dc --env-file ../.env.docker stop worker || true

# 5) COMBINE COVERAGE (host + fragments containers)
log "combine coverage (host + containers)…"
files=""
test -s .coverage.host && files="$files .coverage.host"
# *tous* les fragments écrits sous ./server par api/worker (volume partagé)
api_worker_files="$(find server -maxdepth 1 -type f -name '.coverage*' ! -name '.coveragerc' -size +0c -printf ' %p' 2>/dev/null || true)"
files="$files$api_worker_files"

if [[ -z "${files// /}" ]]; then
  echo "❌ Aucun fichier coverage trouvé à combiner"
  echo "📂 Debug list:"
  ls -la . server || true
  exit 1
fi

log "⏳ Combine: $files"
COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc python -m coverage combine -q $files
COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc python -m coverage report -m --fail-under="$THRESHOLD"
COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc python -m coverage xml -o coverage.xml

log "✅ OK — couverture ≥ ${THRESHOLD}%"
