#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# E2E runner (ccc)
# - Démarre la stack Docker (docker/docker-compose.yml)
# - Attend que l’API réponde /api/v1/health
# - Exécute les tests marqués "e2e"
# - Produit la couverture (term + XML) sans imposer de seuil ici
# - Déverse les logs Docker en cas d’échec
# - Coupe la stack à la fin (sauf si KEEP_STACK_UP=1)
# -----------------------------------------------------------------------------

set -euo pipefail

# --- (ccc) Paramètres configurables -------------------------------------------------
: "${API:=http://localhost:8000}"          # Base URL de l’API exposée par le conteneur "api"
: "${KEY:=dev-apikey-123}"                 # Clé API à passer dans X-API-Key
: "${KEEP_STACK_UP:=0}"                    # Si =1, on NE stoppe PAS la stack à la fin
: "${HEALTH_RETRIES:=60}"                  # Nb d’essais healthcheck (2s d’intervalle)
: "${DOCKER_DIR:=docker}"                  # Répertoire contenant docker-compose.yml
# -----------------------------------------------------------------------------------

# (ccc) Housekeeping : on veut pouvoir faire du nettoyage quoiqu’il arrive
cleanup() {
  # Si demandé, on garde la stack pour debug
  if [[ "${KEEP_STACK_UP}" == "1" ]]; then
    echo "[test_e2e] KEEP_STACK_UP=1 → je laisse la stack en place."
    return 0
  fi
  echo "[test_e2e] down docker compose"
  ( set -x; docker compose -f "${DOCKER_DIR}/docker-compose.yml" down -v ) || true
}
trap cleanup EXIT

# (ccc) Petit helper de dump logs en cas d’échec
dump_logs() {
  echo "[test_e2e] ==== docker compose ps ===="
  docker compose -f "${DOCKER_DIR}/docker-compose.yml" ps || true
  echo "[test_e2e] ==== api logs (tail 400) ===="
  docker compose -f "${DOCKER_DIR}/docker-compose.yml" logs api | tail -n 400 || true
  echo "[test_e2e] ==== worker logs (tail 400) ===="
  docker compose -f "${DOCKER_DIR}/docker-compose.yml" logs worker | tail -n 400 || true
  echo "[test_e2e] ==== db logs (tail 200) ===="
  docker compose -f "${DOCKER_DIR}/docker-compose.yml" logs db | tail -n 200 || true
}

# (ccc) Vérifs de base
if [[ ! -f "${DOCKER_DIR}/docker-compose.yml" ]]; then
  echo "ERR: ${DOCKER_DIR}/docker-compose.yml introuvable" >&2
  exit 2
fi

# (ccc) Prépare un .env.docker minimal si manquant (utile en local)
if [[ ! -f ".env.docker" ]] && [[ -f ".env.example" ]]; then
  echo "[test_e2e] no .env.docker, creating from .env.example"
  cp .env.example .env.docker
  # forcer quelques valeurs « sûres » pour l’e2e local
  awk 'BEGIN{slack=0;rem=0}
       /^SLACK_WEBHOOK=/ {print "SLACK_WEBHOOK=http://httpbin:80/status/204"; slack=1; next}
       /^ALERT_REMINDER_MINUTES=/ {print "ALERT_REMINDER_MINUTES=1"; rem=1; next}
       {print}
       END{
         if(!slack) print "SLACK_WEBHOOK=http://httpbin:80/status/204";
         if(!rem)   print "ALERT_REMINDER_MINUTES=1";
         print "STUB_SLACK=1";
       }' .env.docker > .env.tmp && mv .env.tmp .env.docker
fi

echo "[test_e2e] bring up docker compose"
(
  set -x
  docker compose --env-file .env.docker -f "${DOCKER_DIR}/docker-compose.yml" up -d --build
)

# (ccc) Healthcheck API
echo "[test_e2e] wait for API health on ${API}/api/v1/health"
ok=0
for (( i=1; i<=HEALTH_RETRIES; i++ )); do
  if curl -fsS -H "X-API-Key: ${KEY}" "${API}/api/v1/health" >/dev/null; then
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

# (ccc) L’indicateur permet aux tests d’activer certains chemins (ex: pas de skip e2e)
export E2E_STACK_UP=1

# (ccc) Lancer les tests e2e :
#  - Couverture affichée au terminal + XML (coverage.xml) pour agrégation éventuelle,
#  - SANS seuil ici (le gate de couverture reste dans la combine globale).
echo "[test_e2e] run pytest -m e2e"
set +e
pytest -m "e2e" \
  --maxfail=1 \
  --cov=server/app --cov-report=term-missing --cov-report=xml \
  --cov-fail-under=0
rc=$?
set -e

if [[ "${rc}" -ne 0 ]]; then
  echo "[test_e2e] pytest failed (rc=${rc}), dumping logs…"
  dump_logs
  exit "${rc}"
fi

echo "[test_e2e] success ✅ (coverage.xml généré, pas de seuil bloquant ici)"
