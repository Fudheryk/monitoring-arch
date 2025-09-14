#!/usr/bin/env bash
set -euo pipefail

# =====================================================================================
# test_integ.sh — Démarre la stack (compose), applique les migrations, puis lance
#                  les tests d'intégration côté hôte.
#
# Points clés :
# - Se place à la racine du repo (indépendant du CWD appelant)
# - NE PAS exporter DATABASE_URL/PG_DSN avant le 'docker compose up' (pas de fuite
#   d'ENV hôte vers les conteneurs).
# - Choisit automatiquement l'env-file pour compose :
#       1) $INTEG_ENV_FILE si fourni
#       2) ./.env.integration.local (si présent)
#       3) ./.env.example (fallback)
# - Attend DB (pg_isready) + applique migrations Alembic dans le conteneur 'api'
# - Attend que l'API réponde (santé) vue de l'hôte
# - Charge ENSUITE les overrides hôte (.env.integration.local) pour pytest
# - Optionnel : CLEANUP=1 pour faire un 'docker compose down -v' à la fin
# - DEBUG=1 pour activer 'set -x'
#
# Variables utiles (surchageables avant appel) :
#   API=http://localhost:8000
#   KEY=dev-apikey-123
#   INTEG_STACK_UP=1
#   INTEG_ENV_FILE=/chemin/vers/mon/envfile
#   CLEANUP=1            # teardown après tests
#   DEBUG=1              # trace commandes
# =====================================================================================

# ---------- Debug facultatif ----------
if [[ "${DEBUG:-0}" == "1" ]]; then
  set -x
fi

# ---------- 0) Se placer à la racine du repo ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -d "docker" ]]; then
  echo "[integ] ERROR: 'docker/' directory not found at ${ROOT_DIR}" >&2
  exit 1
fi

# ---------- 1) Defaults hôte (ne PAS toucher à la DB avant compose up) ----------
export API="${API:-http://localhost:8000}"
export KEY="${KEY:-dev-apikey-123}"
export INTEG_STACK_UP="${INTEG_STACK_UP:-1}"

# IMPORTANT : ne pas polluer l'env des conteneurs avec une DB hôte
unset DATABASE_URL || true
unset PG_DSN || true

# ---------- Helper: résolution en chemin absolu (realpath ou readlink -f) ----------
_abspath() {
  local p="$1"
  if command -v realpath >/dev/null 2>&1; then
    realpath "$p"
  else
    # readlink -f existe sur la plupart des Linux
    readlink -f "$p"
  fi
}

# ---------- 2) Choix du fichier d'env pour docker compose ----------
# Ordre de priorité:
#   - INTEG_ENV_FILE (si défini)
#   - ./.env.integration.local (repo root)
#   - ./.env.docker (repo root)
CANDIDATE_ENV_FILE="${INTEG_ENV_FILE:-}"
if [[ -z "${CANDIDATE_ENV_FILE}" ]]; then
  if [[ -f ".env.integration.host" ]]; then
    CANDIDATE_ENV_FILE=".env.integration.host"
  else
    CANDIDATE_ENV_FILE=".env.docker"
  fi
fi

# Convertir en absolu pour éviter les soucis de chemin après pushd docker
ENV_FILE="$(_abspath "${CANDIDATE_ENV_FILE}")"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[integ] ERROR: env-file not found: ${ENV_FILE}" >&2
  exit 1
fi

echo "[integ] Using env-file for docker compose: ${ENV_FILE}"

# ---------- 3) Monter la stack ----------
pushd docker >/dev/null
echo "[integ] docker compose up -d --build"
docker compose --env-file "${ENV_FILE}" up -d --build

# ---------- 4) Attendre Postgres prêt (dans le conteneur) ----------
echo "[integ] Waiting for Postgres readiness..."
ok_db=0
for i in {1..60}; do
  if docker compose --env-file "${ENV_FILE}" exec -T db pg_isready -U postgres >/dev/null 2>&1; then
    ok_db=1
    break
  fi
  sleep 2
done
if [[ "${ok_db}" -ne 1 ]]; then
  echo "[integ] ERROR: Postgres not ready." >&2
  docker compose ps || true
  docker compose logs db --tail=200 || true
  popd >/dev/null
  exit 1
fi

# (Diagnostic) Vérifier l'URL DB vue DANS le conteneur API
echo "[integ] Inspecting DATABASE_URL inside api container (should point to db:5432)"
docker compose --env-file "${ENV_FILE}" exec -T api sh -lc 'echo "API: ${DATABASE_URL}"'

# ---------- 5) Appliquer les migrations Alembic dans le conteneur 'api' ----------
echo "[integ] Running Alembic migrations in container..."
if ! docker compose --env-file "${ENV_FILE}" run --rm -w /app/server api alembic -c /app/server/alembic.ini upgrade head; then
  echo "[integ] ERROR: Alembic migrations failed." >&2
  docker compose logs api --tail=300 || true
  docker compose logs db  --tail=200 || true
  popd >/dev/null
  exit 1
fi
popd >/dev/null

# ---------- 6) Attendre l'API exposée (vue de l'hôte) ----------
echo "[integ] Waiting for API health at ${API}/api/v1/health"
ok_api=0
for i in {1..60}; do
  if curl -fsS -H "X-API-Key: ${KEY}" "${API}/api/v1/health" >/dev/null; then
    ok_api=1
    break
  fi
  sleep 2
done
if [[ "${ok_api}" -ne 1 ]]; then
  echo "[integ] ERROR: API not healthy after waiting." >&2
  pushd docker >/dev/null
  docker compose ps || true
  docker compose logs api    --tail=300 || true
  docker compose logs worker --tail=300 || true
  docker compose logs db     --tail=150 || true
  popd >/dev/null
  exit 1
fi

# ---------- 7) (Optionnel) Charger les overrides hôte pour pytest ----------
# Ici SEULEMENT : on peut définir une DATABASE_URL/PG_DSN "localhost"
# pour les tests Python qui se connectent depuis l'hôte.
if [[ -f ".env.integration.host" ]]; then
  echo "[integ] Loading .env.integration.host for host pytest overrides"
  set -o allexport
  # shellcheck disable=SC1091
  source ".env.integration.host"
  set +o allexport
fi

# Defaults de repli si non définis par le fichier ci-dessus
export API="${API:-http://localhost:8000}"
export KEY="${KEY:-dev-apikey-123}"
export INTEG_STACK_UP="${INTEG_STACK_UP:-1}"

# Si aucune DATABASE_URL hôte n’est définie, on propose un défaut sûr (localhost)
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://postgres:postgres@localhost:5432/monitoring}"
export PG_DSN="${PG_DSN:-postgresql://postgres:postgres@localhost:5432/monitoring}"

# Petit diagnostic côté hôte
echo "[integ] Host DATABASE_URL=${DATABASE_URL}"
echo "[integ] Host PG_DSN=${PG_DSN}"

# ---------- 8) Lancer pytest d'intégration sur l'hôte ----------
echo "[integ] Running pytest -m integration"
if ! pytest -m integration -vv -ra "$@"; then
  echo "[integ] ❌ Integration tests FAILED."
  # dump minimal pour debug rapide
  pushd docker >/dev/null
  docker compose ps || true
  docker compose logs api    --tail=200 || true
  docker compose logs worker --tail=200 || true
  docker compose logs db     --tail=120 || true
  popd >/dev/null

  # Teardown si demandé
  if [[ "${CLEANUP:-0}" == "1" ]]; then
    echo "[integ] CLEANUP=1 → docker compose down -v"
    pushd docker >/dev/null
    docker compose --env-file "${ENV_FILE}" down -v || true
    popd >/dev/null
  fi
  exit 1
fi

echo "[integ] ✅ Integration tests finished OK."

# ---------- 9) Teardown optionnel ----------
if [[ "${CLEANUP:-0}" == "1" ]]; then
  echo "[integ] CLEANUP=1 → docker compose down -v"
  pushd docker >/dev/null
  docker compose --env-file "${ENV_FILE}" down -v || true
  popd >/dev/null
fi
