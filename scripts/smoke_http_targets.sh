#!/usr/bin/env bash
# scripts/smoke_http_targets.sh
#
# Smoke test HTTP targets (API /api/v1/http-targets)
#
# Objectifs :
# - "0 traces" : aucune API key en dur -> KEY obligatoire via env
# - Vérifier l'idempotence / gestion des courses :
#     * 2 POST concurrents -> un 201 + un 409 (existing_id)
#     * re-POST identique -> 409
# - Vérifier la validation :
#     * URL non-HTTP(S) -> 422
#     * Méthode invalide -> 422 (si Enum strict côté schéma)
#
# Usage :
#   KEY="<YOUR_API_KEY>" API="http://localhost:8000" ./scripts/smoke_http_targets.sh
#
set -euo pipefail

# --- Dépendances minimales ----------------------------------------------------
command -v curl >/dev/null || { echo "curl manquant"; exit 1; }
command -v jq   >/dev/null || { echo "jq manquant"; exit 1; }

# --- Paramètres (surchargeables via env) --------------------------------------
API="${API:-http://localhost:8000}"
: "${KEY:?Missing KEY env var (API key). Example: KEY=... ./scripts/smoke_http_targets.sh}"

# Header commun (API key obligatoire)
hdr=(-H "Content-Type: application/json" -H "X-API-Key: $KEY")

# --- Attente que l'API soit "healthy" ----------------------------------------
echo ">>> Wait for API health at $API/api/v1/health"
ok=false
for _ in {1..60}; do
  if curl -fsS -m 2 "${hdr[@]}" "$API/api/v1/health" >/dev/null; then
    ok=true
    break
  fi
  sleep 2
done
$ok || { echo "❌ API not healthy after waiting"; exit 1; }

# --- Corps de base de la ressource à créer -----------------------------------
# NOTE : on injecte l'URL dans un JSON via jq (pas de substitution string fragile).
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
BODY="$(jq -c --arg url "$U" '.url=$url' <<<"$base")"

# --- Fichiers temporaires + cleanup ------------------------------------------
aj="$(mktemp)"; bj="$(mktemp)"; cj="$(mktemp)"
ac="$(mktemp)"; bc="$(mktemp)"
trap 'rm -f "$aj" "$bj" "$cj" "$ac" "$bc"' EXIT

# --- Deux POST concurrents -> on attend 201 et 409 ----------------------------
echo ">>> Deux POST concurrents vers $U"
(
  curl -s -o "$aj" -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BODY" >"$ac" &
  curl -s -o "$bj" -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BODY" >"$bc" &
  wait
)

ca="$(cat "$ac")"; cb="$(cat "$bc")"
echo "Codes: A=$ca  B=$cb"
if ! { [ "$ca$cb" = "201409" ] || [ "$ca$cb" = "409201" ]; }; then
  echo "❌ Attendu un 201 et un 409"
  echo "Réponse A:"; cat "$aj" || true
  echo "Réponse B:"; cat "$bj" || true
  exit 1
fi

# Récupère l'id créé (201) et l'existing_id (409), qui doivent coïncider.
# On lit les deux fichiers et on prend la première valeur non vide.
new_id="$(jq -r '.id // empty' "$aj" "$bj" | head -n1 || true)"
existing_id="$(jq -r '.detail.existing_id // empty' "$aj" "$bj" | head -n1 || true)"

if [[ -z "$new_id" ]]; then
  echo "❌ Impossible de récupérer l'id (201)."
  echo "Réponse A:"; cat "$aj" || true
  echo "Réponse B:"; cat "$bj" || true
  exit 1
fi

if [[ -z "$existing_id" ]]; then
  echo "❌ Impossible de récupérer detail.existing_id (409)."
  echo "Réponse A:"; cat "$aj" || true
  echo "Réponse B:"; cat "$bj" || true
  exit 1
fi

if [[ "$existing_id" != "$new_id" ]]; then
  echo "❌ existing_id ($existing_id) doit égaler l'id créé ($new_id)"
  echo "Réponse A:"; cat "$aj" || true
  echo "Réponse B:"; cat "$bj" || true
  exit 1
fi
echo "✅ existing_id = $new_id"

# --- Idempotence: re-POST identique => 409 -----------------------------------
echo ">>> Idempotence (re-POST => 409)"
code="$(curl -s -o "$cj" -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BODY")"
if [[ "$code" == "409" ]]; then
  echo "✅ 409 OK"
else
  echo "❌ Re-POST devrait renvoyer 409, obtenu $code"
  echo "Réponse:"; cat "$cj" || true
  exit 1
fi

# --- Validation: mauvais schéma d'URL => 422 ---------------------------------
echo ">>> Validation: URL scheme invalide => 422"
BAD="$(jq -c '.url="ftp://example.com"' <<<"$BODY")"
code="$(curl -s -o /dev/null -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BAD")"
if [[ "$code" == "422" ]]; then
  echo "✅ 422 OK (scheme)"
else
  echo "⚠️ Attendu 422 pour scheme invalide (obtenu $code)"
fi

# --- Validation: méthode invalide => 422 (si Enum côté schéma) ----------------
echo ">>> Validation: méthode invalide => 422 (si Enum côté schema)"
BAD="$(jq -c '.method="FETCH"' <<<"$BODY")"
code="$(curl -s -o /dev/null -w '%{http_code}' "${hdr[@]}" -X POST "$API/api/v1/http-targets" -d "$BAD")"
if [[ "$code" == "422" ]]; then
  echo "✅ 422 OK (method)"
else
  echo "ℹ️ Si pas 422, vérifie l’Enum sur HttpTargetIn.method (ou la validation côté backend)."
fi

echo "✅ Smoke test terminé"
