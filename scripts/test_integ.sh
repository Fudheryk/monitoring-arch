#!/usr/bin/env bash
set -euo pipefail

#  ─────────────────────────────────────────────────────────────────────────────
# test_integ.sh — Démarre la stack (compose), applique les migrations, puis
#                 lance les tests d'intégration côté hôte.
#
# Points clés :
# - Se place à la racine du repo (indépendant du CWD appelant)
# - NE PAS exporter DATABASE_URL/PG_DSN avant le 'docker compose up' (évite de
#   polluer l'env des conteneurs ; ils doivent parler à db:5432).
# - Choisit automatiquement l'env-file pour compose :
#     1) $INTEG_ENV_FILE si fourni (chemin absolu/relatif)
#     2) ./.env.integration.host (pour overrides *hôte* ultérieurs)
#     3) ./.env.docker (par défaut pour la stack docker)
# - Attend DB (pg_isready) + applique migrations Alembic dans le conteneur 'api'
# - Attend que l'API réponde (santé) vue de l'hôte
# - Charge ENSUITE les overrides hôte (.env.integration.host) pour pytest
# - Optionnel : CLEANUP=1 pour faire un 'docker compose down -v' à la fin
# - DEBUG=1 pour activer 'set -x'
#
# Couverture côté containers :
# - WITH_COVERAGE=1 inclut l’overlay docker-compose.coverage.yml
#   (API_COVERAGE/WORKER_COVERAGE, COVERAGE_FILE etc.). Les fragments sont
#   écrits sous ./server/.coverage.* (utiles pour "combine" plus tard).
#
# Variables utiles (surchageables avant appel) :
#   API=http://localhost:8000
#   KEY=dev-apikey-123
#   INTEG_STACK_UP=1
#   INTEG_ENV_FILE=/chemin/vers/mon/envfile
#   WITH_COVERAGE=1        # active overlay de couverture pour API/worker
#   CLEANUP=1              # teardown après tests
#   DEBUG=1                # trace commandes
# ──────────────────────────────────────────────────────────────────────────────

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
export WITH_COVERAGE="${WITH_COVERAGE:-0}"

# IMPORTANT : ne pas polluer l'env des conteneurs avec une DB hôte
unset DATABASE_URL || true
unset PG_DSN || true

# ---------- Helper: résolution en chemin absolu (realpath ou readlink -f) ----------
_abspath() {
  local p="$1"
  if command -v realpath >/dev/null 2>&1; then
    realpath "$p"
  else
    readlink -f "$p"
  fi
}

# ---------- 2) Choix du fichier d'env pour docker compose ----------
# Priorité:
#   - INTEG_ENV_FILE (si défini)
#   - ./.env.integration.host (pour config *hôte* post-up ; utilisé aussi ici si présent)
#   - ./.env.docker (par défaut pour la stack docker)
CANDIDATE_ENV_FILE="${INTEG_ENV_FILE:-}"
if [[ -z "${CANDIDATE_ENV_FILE}" ]]; then
  if [[ -f ".env.integration.host" ]]; then
    CANDIDATE_ENV_FILE=".env.integration.host"
  else
    CANDIDATE_ENV_FILE=".env.docker"
  fi
fi

ENV_FILE="$(_abspath "${CANDIDATE_ENV_FILE}")"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[integ] ERROR: env-file not found: ${ENV_FILE}" >&2
  exit 1
fi
echo "[integ] Using env-file for docker compose: ${ENV_FILE}"

# ---------- 3) Déterminer les fichiers compose (overlay coverage optionnel) ----------
# On exécute toujours depuis ./docker
pushd docker >/dev/null

DC_FILES="-f docker-compose.yml"
if [[ "${WITH_COVERAGE}" == "1" ]]; then
  if [[ -f "docker-compose.coverage.yml" ]]; then
    DC_FILES="${DC_FILES} -f docker-compose.coverage.yml"
    echo "[integ] WITH_COVERAGE=1 → overlay docker-compose.coverage.yml activé"
  else
    echo "[integ] WARNING: WITH_COVERAGE=1 mais docker-compose.coverage.yml introuvable"
  fi
fi

# petit wrapper pour moins se répéter
dco() { docker compose ${DC_FILES} --env-file "${ENV_FILE}" "$@"; }

# ---------- 4) Monter la stack ----------
echo "[integ] docker compose up -d --build"
dco up -d --build

# ---------- 5) Attendre Postgres prêt (dans le conteneur) ----------
echo "[integ] Waiting for Postgres readiness..."
ok_db=0
for i in {1..60}; do
  if dco exec -T db pg_isready -U postgres >/dev/null 2>&1; then
    ok_db=1
    break
  fi
  sleep 2
done
if [[ "${ok_db}" -ne 1 ]]; then
  echo "[integ] ERROR: Postgres not ready." >&2
  dco ps || true
  dco logs db --tail=200 || true
  popd >/dev/null
  exit 1
fi

# (Diagnostic) Vérifier l'URL DB vue DANS le conteneur API
echo "[integ] Inspecting DATABASE_URL inside api container (should point to db:5432)"
dco exec -T api sh -lc 'echo "API: ${DATABASE_URL}"'

# ---------- 6) Appliquer les migrations Alembic dans le conteneur 'api' ----------
echo "[integ] Running Alembic migrations in container..."
if ! dco run --rm -w /app/server api alembic -c /app/server/alembic.ini upgrade head; then
  echo "[integ] ERROR: Alembic migrations failed." >&2
  dco logs api --tail=300 || true
  dco logs db  --tail=200 || true
  popd >/dev/null
  exit 1
fi
popd >/dev/null

# ---------- 7) Attendre l'API exposée (vue de l'hôte) ----------
echo "[integ] Waiting for API health at ${API}/api/v1/health"
ok_api=0
for i in {1..60}; do
  if curl -fsS -m 2 -H "X-API-Key: ${KEY}" "${API}/api/v1/health" >/dev/null; then
    ok_api=1
    break
  fi
  sleep 2
done
if [[ "${ok_api}" -ne 1 ]]; then
  echo "[integ] ERROR: API not healthy after waiting." >&2
  pushd docker >/dev/null
  dco ps || true
  dco logs api    --tail=300 || true
  dco logs worker --tail=300 || true
  dco logs db     --tail=150 || true
  popd >/dev/null
  exit 1
fi

# ---------- 8) (Optionnel) Charger les overrides hôte pour pytest ----------
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

# ---------- 9) Lancer pytest d'intégration sur l'hôte ----------
echo "[integ] Running pytest -m integration"
if ! pytest -m integration -vv -ra "$@"; then
  echo "[integ] ❌ Integration tests FAILED."
  # dump minimal pour debug rapide
  pushd docker >/dev/null
  dco ps || true
  dco logs api    --tail=200 || true
  dco logs worker --tail=200 || true
  dco logs db     --tail=120 || true
  popd >/dev/null

  # Teardown si demandé
  if [[ "${CLEANUP:-0}" == "1" ]]; then
    echo "[integ] CLEANUP=1 → docker compose down -v"
    pushd docker >/dev-null
    dco down -v || true
    popd >/dev/null
  fi
  exit 1
fi

echo "[integ] ✅ Integration tests finished OK."

# ---------- 10) Teardown optionnel ----------
if [[ "${CLEANUP:-0}" == "1" ]]; then
  echo "[integ] CLEANUP=1 → docker compose down -v"
  pushd docker >/dev/null
  dco down -v || true
  popd >/dev/null
fi
