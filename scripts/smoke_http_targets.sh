#!/usr/bin/env bash
set -euo pipefail

# --- Dépendances minimales ----------------------------------------------------
command -v curl >/dev/null || { echo "curl manquant"; exit 1; }
command -v jq   >/dev/null || { echo "jq manquant"; exit 1; }

# --- Paramètres (surchargeables via env) --------------------------------------
API="${API:-http://localhost:8000}"
KEY="${KEY:-dev-apikey-123}"
hdr=(-H "Content-Type: application/json" -H "X-API-Key: $KEY")

# --- Attente que l'API soit "healthy" ----------------------------------------
echo ">>> Wait for API health at $API"
ok=false
for i in {1..60}; do
  if curl -fsS -m 2 "$API/api/v1/health" >/dev/null; then
    ok=true
    break
  fi
  sleep 2
done
$ok || { echo "❌ API not healthy after waiting"; exit 1; }

# --- Corps de base de la ressource à créer -----------------------------------
base='{
  "name":"Temp via script",
  "url":"__URL__",
  "method":"GET",
  "timeout_seconds":10,
  "check_interval_seconds":60,
  "is_active":true
}'

# URL unique (évite le 409 dû à un run précédent avec la même URL)
U="https://httpbin.org/status/500?rnd=$(date +%s%N)"
BODY="${base/__URL__/$U}"

# --- Fichiers temporaires + cleanup ------------------------------------------
aj=$(mktemp) bj=$(mktemp) cj=$(mktemp)
ac=$(mktemp) bc=$(mktemp)
trap 'rm -f "$aj" "$bj" "$cj" "$ac" "$bc"' EXIT

# --- Deux POST concurrents -> on attend 201 et 409 ----------------------------
echo ">>> Deux POST concurrents vers $U"
(
  curl -s -o "$aj" -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BODY" >"$ac" &
  curl -s -o "$bj" -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BODY" >"$bc" &
  wait
)

ca=$(cat "$ac"); cb=$(cat "$bc")
echo "Codes: A=$ca  B=$cb"
if ! { [ "$ca$cb" = "201409" ] || [ "$ca$cb" = "409201" ]; }; then
  echo "❌ Attendu un 201 et un 409"; exit 1
fi

# Récupère l'id créé (201) et l'existing_id (409), qui doivent coïncider
new_id=$(jq -r '.id // empty' "$aj" "$bj" | head -n1 || true)
existing_id=$(jq -r '.detail.existing_id // empty' "$aj" "$bj" | head -n1 || true)
if [ -z "$new_id" ] || [ "$existing_id" != "$new_id" ]; then
  echo "❌ existing_id ($existing_id) doit égaler l'id créé ($new_id)"; exit 1
fi
echo "✅ existing_id = $new_id"

# --- Idempotence: re-POST identique => 409 -----------------------------------
echo ">>> Idempotence (re-POST => 409)"
code=$(curl -s -o "$cj" -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BODY")
[ "$code" = "409" ] && echo "✅ 409 OK" || { echo "❌ Re-POST devrait renvoyer 409, obtenu $code"; exit 1; }

# --- Validation: mauvais schéma d'URL => 422 ---------------------------------
echo ">>> Validation: URL scheme invalide => 422"
BAD=$(jq -c '.url="ftp://example.com"' <<<"$BODY")
code=$(curl -s -o /dev/null -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BAD")
[ "$code" = "422" ] && echo "✅ 422 OK" || echo "⚠️ Attendu 422 pour scheme invalide (obtenu $code)"

# --- Validation: méthode invalide => 422 (si Enum côté schéma) ---------------
echo ">>> Validation: méthode invalide => 422 (si Enum côté schema)"
BAD=$(jq -c '.method="FETCH"' <<<"$BODY")
code=$(curl -s -o /dev/null -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BAD")
[ "$code" = "422" ] && echo "✅ 422 OK (method)" || echo "ℹ️ Si pas 422, vérifie l’Enum sur HttpTargetIn.method"

echo "✅ Smoke test terminé"
