#!/usr/bin/env bash
set -euo pipefail

# Dépendances: curl, jq
command -v curl >/dev/null || { echo "curl manquant"; exit 1; }
command -v jq   >/dev/null || { echo "jq manquant"; exit 1; }

API="${API:-http://localhost:8000}"
KEY="${KEY:-dev-apikey-123}"
hdr=(-H "Content-Type: application/json" -H "X-API-Key: $KEY")

base='{
  "name":"Temp via script",
  "url":"__URL__",
  "method":"GET",
  "expected_status_code":200,
  "timeout_seconds":10,
  "check_interval_seconds":60,
  "is_active":true
}'

U="https://httpbin.org/status/500?rnd=$(date +%s%N)"
BODY="${base/__URL__/$U}"

echo ">>> Deux POST concurrents vers $U"
( curl -s -o /tmp/a.json -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BODY" > /tmp/a.code & \
  curl -s -o /tmp/b.json -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BODY" > /tmp/b.code & wait )

ca=$(cat /tmp/a.code); cb=$(cat /tmp/b.code)
echo "Codes: A=$ca  B=$cb"
if ! { [ "$ca$cb" = "201409" ] || [ "$ca$cb" = "409201" ]; }; then
  echo "❌ Attendu un 201 et un 409"; exit 1
fi

new_id=$(jq -r '.id // empty' /tmp/a.json /tmp/b.json | head -n1 || true)
existing_id=$(jq -r '.detail.existing_id // empty' /tmp/a.json /tmp/b.json | head -n1 || true)
if [ -z "$new_id" ] || [ "$existing_id" != "$new_id" ]; then
  echo "❌ existing_id ($existing_id) doit égaler l'id créé ($new_id)"; exit 1
fi
echo "✅ existing_id = $new_id"

echo ">>> Idempotence (re-POST => 409)"
code=$(curl -s -o /tmp/c.json -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BODY")
[ "$code" = "409" ] && echo "✅ 409 OK" || { echo "❌ Re-POST devrait renvoyer 409, obtenu $code"; exit 1; }

echo ">>> Validation: URL scheme invalide => 422"
BAD=$(jq -c '.url="ftp://example.com"' <<<"$BODY")
code=$(curl -s -o /dev/null -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BAD")
[ "$code" = "422" ] && echo "✅ 422 OK" || echo "⚠️ Attendu 422 pour scheme invalide (obtenu $code)"

echo ">>> Validation: méthode invalide => 422 (si Enum côté schema)"
BAD=$(jq -c '.method="FETCH"' <<<"$BODY")
code=$(curl -s -o /dev/null -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BAD")
[ "$code" = "422" ] && echo "✅ 422 OK (method)" || echo "ℹ️ Si pas 422, vérifie l’Enum sur HttpTargetIn.method"

echo "✅ Smoke test terminé"
