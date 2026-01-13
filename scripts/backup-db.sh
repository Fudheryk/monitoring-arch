#!/usr/bin/env bash
# =============================================================================
# Backup PostgreSQL (prod)
# =============================================================================
# - Dump PostgreSQL depuis le conteneur "db"
# - Stocke dans ./backups/postgres/
# - Rétention automatique (14 jours par défaut)
#
# Usage:
#   ./scripts/backup-db.sh
# =============================================================================

set -euo pipefail

# --- Aller à la racine du repo, peu importe d'où on lance le script
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="docker/docker-compose.prod.yml"
BACKUP_DIR="backups/postgres"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

# --- Charger les variables de prod dans l'environnement du script
# (utile pour DB_PASSWORD, DOCKER_USERNAME, etc.)
if [[ ! -f ".env.production" ]]; then
  echo "❌ ERREUR: .env.production manquant à la racine du projet ($ROOT_DIR)"
  echo "👉 Crée-le depuis .env.production.example"
  exit 1
fi

set -a
# shellcheck disable=SC1091
source ".env.production"
set +a

# --- Vérifs minimales
: "${DB_PASSWORD:?❌ DB_PASSWORD manquant dans .env.production}"

mkdir -p "$BACKUP_DIR"

timestamp="$(date +%Y%m%d_%H%M%S)"
outfile="${BACKUP_DIR}/monitoring_${timestamp}.sql.gz"

echo "════════════════════════════════════════════════════════════"
echo "📦 Backup PostgreSQL → $outfile"
echo "════════════════════════════════════════════════════════════"

# --- S'assurer que le service db est up (sinon backup impossible)
# (si la stack n'est pas lancée, ça démarre la DB seulement)
echo "→ Vérification / démarrage du service db si nécessaire…"
docker compose -f "$COMPOSE_FILE" up -d db >/dev/null

# --- Attendre que Postgres réponde
echo "→ Attente DB (pg_isready)…"
for i in {1..30}; do
  if docker compose -f "$COMPOSE_FILE" exec -T db \
      pg_isready -U postgres -d monitoring >/dev/null 2>&1; then
    echo "✅ DB prête"
    break
  fi
  if [[ $i -eq 30 ]]; then
    echo "❌ DB non prête après 30s"
    docker compose -f "$COMPOSE_FILE" logs --tail=80 db || true
    exit 1
  fi
  sleep 1
done

# --- Dump
# Important: on passe le mot de passe via PGPASSWORD dans l'environnement de exec
# et on utilise -T pour éviter les problèmes de TTY dans un script.
echo "→ Dump en cours…"
docker compose -f "$COMPOSE_FILE" exec -T \
  -e PGPASSWORD="$DB_PASSWORD" db \
  pg_dump -U postgres -d monitoring --no-owner --no-acl \
  | gzip -9 > "$outfile"

# --- Vérif taille
if [[ ! -s "$outfile" ]]; then
  echo "❌ Backup vide ou échoué: $outfile"
  exit 1
fi

echo "✅ Backup OK : $(ls -lh "$outfile" | awk '{print $5}')"

# --- Rétention
echo "→ Nettoyage des backups > ${RETENTION_DAYS} jours…"
find "$BACKUP_DIR" -name "monitoring_*.sql.gz" -type f -mtime +"$RETENTION_DAYS" -print -delete || true

echo "✅ Terminé"
