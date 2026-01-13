#!/usr/bin/env bash
set -euo pipefail

DOMAIN="monitoring.local"
CERT_DIR="docker/certs"
COMPOSE_FILE="docker/docker-compose.yml"

echo "▶ Installing mkcert + deps if missing"
if ! command -v mkcert >/dev/null 2>&1; then
  sudo apt update
  sudo apt install -y libnss3-tools
  # mkcert binaire: à installer comme tu l'as fait (github release) ou via package si dispo
  echo "❌ mkcert not found. Install it in /usr/local/bin/mkcert then re-run."
  exit 1
fi

echo "▶ Installing mkcert local CA (browser + system)"
mkcert -install

echo "▶ Making sure curl trusts mkcert CA (Debian ca-certificates bundle)"
sudo cp "$(mkcert -CAROOT)/rootCA.pem" /usr/local/share/ca-certificates/mkcert-rootCA.crt
sudo update-ca-certificates >/dev/null

echo "▶ Generating TLS cert for ${DOMAIN} into ${CERT_DIR}"
mkdir -p "${CERT_DIR}"
mkcert \
  -cert-file "${CERT_DIR}/${DOMAIN}.pem" \
  -key-file "${CERT_DIR}/${DOMAIN}-key.pem" \
  "${DOMAIN}"

echo "▶ Starting docker compose stack"
docker compose -f "${COMPOSE_FILE}" up -d --build

echo "▶ Smoke test"
curl -fsS "https://${DOMAIN}/api/v1/health" | cat
echo
echo "✅ OK: https://${DOMAIN} is up"
