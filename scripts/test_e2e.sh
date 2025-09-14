#!/usr/bin/env bash
set -euo pipefail

# ccc ──────────────────────────────────────────────────────────────────────────
# test_e2e.sh — Démarre la stack Docker, attend la santé de l’API et lance les
#                tests marqués "e2e" côté hôte.
#
# Points clés :
# - Crée .env.docker depuis .env.example si manquant (valeurs sûres pour l’E2E)
# - Option overlay coverage : WITH_COVERAGE=1 => inclut docker-compose.coverage.yml
#   (l’API/worker écrivent des fragments .coverage.* dans ./server/).
# - Attente DB (pg_isready) puis (optionnel) migrations Alembic (MIGRATE=1 par défaut).
# - Attente /api/v1/health vue de l’hôte.
# - Lancement pytest -m e2e avec couverture affichée + XML (pas de seuil bloquant ici).
# - Coupe la stack à la fin (sauf KEEP_STACK_UP=1).
#
# Variables utiles (surchageables avant appel) :
#   API=http://localhost:8000
#   KEY=dev-apikey-123
#   DOCKER_DIR=docker
#   WITH_COVERAGE=1          # active overlay docker-compose.coverage.yml
#   START_WORKER=0           # 1 si vous voulez aussi démarrer le worker
#   MIGRATE=1                # applique les migrations via un one-off "api"
#   HEALTH_RETRIES=60        # nb essais (2s d’intervalle) pour le /health
#   KEEP_STACK_UP=0          # 1 => ne stoppe pas la stack en sortie (debug)
#   BUILD=0                  # 1 => rebuild images au up
#   PYTHONPATH=server        # pour importer "app.*" côté hôte
# ──────────────────────────────────────────────────────────────────────────────

# --- Paramètres par défaut ----------------------------------------------------
: "${API:=http://localhost:8000}"
: "${KEY:=dev-apikey-123}"
: "${DOCKER_DIR:=docker}"
: "${WITH_COVERAGE:=0}"
: "${START_WORKER:=0}"
: "${MIGRATE:=1}"
: "${HEALTH_RETRIES:=60}"
: "${KEEP_STACK_UP:=0}"
: "${BUILD:=0}"
: "${PYTHONPATH:=server}"

export PYTHONPATH

# --- Garde-fous ---------------------------------------------------------------
if [[ ! -f "${DOCKER_DIR}/docker-compose.yml" ]]; then
  echo "ERR: ${DOCKER_DIR}/docker-compose.yml introuvable" >&2
  exit 2
fi

# --- Prépare .env.docker si manquant -----------------------------------------
ensure_env_docker() {
  if [[ ! -f ".env.docker" ]]; then
    if [[ -f ".env.example" ]]; then
      echo "[e2e] no .env.docker, creating from .env.example"
      cp .env.example .env.docker
      # ccc: impose quelques valeurs « sûres » pour l’E2E local
      awk 'BEGIN{slack=0;rem=0}
           /^SLACK_WEBHOOK=/ {print "SLACK_WEBHOOK=http://httpbin:80/status/204"; slack=1; next}
           /^ALERT_REMINDER_MINUTES=/ {print "ALERT_REMINDER_MINUTES=1"; rem=1; next}
           {print}
           END{
             if(!slack) print "SLACK_WEBHOOK=http://httpbin:80/status/204";
             if(!rem)   print "ALERT_REMINDER_MINUTES=1";
             print "STUB_SLACK=1";
           }' .env.docker > .env.tmp && mv .env.tmp .env.docker
    else
      echo "ERR: .env.docker absent et .env.example introuvable" >&2
      exit 2
    fi
  fi
  # copie dans docker/.env.docker à des fins de cohérence si nécessaire
  cp .env.docker "${DOCKER_DIR}/.env.docker"
}

# --- Compose wrapper (overlay coverage optionnel) -----------------------------
DC_FILES="-f ${DOCKER_DIR}/docker-compose.yml"
if [[ "${WITH_COVERAGE}" == "1" ]]; then
  if [[ -f "${DOCKER_DIR}/docker-compose.coverage.yml" ]]; then
    DC_FILES="${DC_FILES} -f ${DOCKER_DIR}/docker-compose.coverage.yml"
    echo "[e2e] WITH_COVERAGE=1 → overlay ${DOCKER_DIR}/docker-compose.coverage.yml activé"
  else
    echo "[e2e] WARNING: WITH_COVERAGE=1 mais ${DOCKER_DIR}/docker-compose.coverage.yml introuvable"
  fi
fi

dco() {
  # Utilise .env.docker à la racine (copié aussi dans docker/ ci-dessus)
  docker compose ${DC_FILES} --env-file ./.env.docker "$@"
}

# --- Housekeeping / teardown --------------------------------------------------
cleanup() {
  if [[ "${KEEP_STACK_UP}" == "1" ]]; then
    echo "[e2e] KEEP_STACK_UP=1 → je laisse la stack en place."
    return 0
  fi
  echo "[e2e] down docker compose"
  ( set -x; dco down -v ) || true
}
trap cleanup EXIT

dump_logs() {
  echo "[e2e] ==== docker compose ps ===="
  dco ps || true
  echo "[e2e] ==== api logs (tail 200) ===="
  dco logs api | tail -n 200 || true
  echo "[e2e] ==== worker logs (tail 200) ===="
  dco logs worker | tail -n 200 || true
  echo "[e2e] ==== db logs (tail 100) ===="
  dco logs db | tail -n 100 || true
}

# --- Bring up stack -----------------------------------------------------------
ensure_env_docker
echo "[e2e] bring up docker compose"
if [[ "${BUILD}" == "1" ]]; then
  dco up -d --build db redis api
else
  dco up -d db redis api
fi

# Optionnel: démarrer aussi le worker (inutile pour la plupart des e2e)
if [[ "${START_WORKER}" == "1" ]]; then
  echo "[e2e] starting worker service…"
  if [[ "${BUILD}" == "1" ]]; then
    dco up -d --build worker
  else
    dco up -d worker
  fi
fi

# --- Wait DB ------------------------------------------------------------------
echo "[e2e] wait for DB readiness (pg_isready)…"
ok_db=0
for i in {1..60}; do
  if dco exec -T db pg_isready -U postgres >/dev/null 2>&1; then
    ok_db=1
    break
  fi
  sleep 2
done
if [[ "${ok_db}" -ne 1 ]]; then
  echo "ERR: Postgres not ready." >&2
  dump_logs
  exit 3
fi

# --- (Optionnel) migrations Alembic ------------------------------------------
if [[ "${MIGRATE}" == "1" ]]; then
  echo "[e2e] applying Alembic migrations (one-off api)…"
  if ! dco run --rm -w /app/server api alembic -c /app/server/alembic.ini upgrade head; then
    echo "ERR: migrations failed" >&2
    dump_logs
    exit 3
  fi
fi

# --- Wait API health ----------------------------------------------------------
echo "[e2e] wait for API health on ${API}/api/v1/health"
ok=0
for (( i=1; i<=HEALTH_RETRIES; i++ )); do
  if curl -fsS -m 2 -H "X-API-Key: ${KEY}" "${API}/api/v1/health" >/dev/null; then
    ok=1
    break
  fi
  sleep 2
done
if [[ "${ok}" != "1" ]]; then
  echo "ERR: API not healthy after $((HEALTH_RETRIES*2))s" >&2
  dump_logs
  exit 3
fi

# --- E2E tests ----------------------------------------------------------------
export E2E_STACK_UP=1   # lève les garde-fous/skip dans les tests e2e
echo "[e2e] run pytest -m e2e (coverage shown + XML, no gate here)"

# ccc: on produit coverage côté hôte (utilisable pour combine ultérieurement)
# - pas de seuil bloquant ici (gate ailleurs, ex: verify_all.sh)
set +e
COVERAGE_FILE=".coverage.host" \
pytest -m "e2e" \
  --maxfail=1 \
  --cov=server/app --cov-config=.coveragerc \
  --cov-report=term-missing --cov-report=xml \
  --cov-branch \
  --cov-fail-under=0
rc=$?
set -e

if [[ "${rc}" -ne 0 ]]; then
  echo "[e2e] pytest failed (rc=${rc}), dumping logs…"
  dump_logs
  exit "${rc}"
fi

echo "[e2e] success ✅ (coverage.xml généré ; combine/threshold gérés ailleurs)"
