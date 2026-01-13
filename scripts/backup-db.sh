#!/usr/bin/env bash
# =============================================================================
# Backup PostgreSQL (prod) - Version complète et sécurisée
# =============================================================================
# - Dump PostgreSQL depuis le conteneur "db"
# - Stocke dans ./backups/postgres/
# - Rétention automatique (14 jours par défaut)
# - Vérifications: espace disque, intégrité, lock file
# - Métadonnées et statistiques
#
# Usage:
#   ./scripts/backup-db.sh [RETENTION_DAYS]
# Exemple:
#   ./scripts/backup-db.sh           # 14 jours rétention
#   ./scripts/backup-db.sh 30        # 30 jours rétention
# =============================================================================

set -euo pipefail

# --- Configuration -----------------------------------------------------------
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="docker/docker-compose.prod.yml"
BACKUP_DIR="backups/postgres"
METADATA_DIR="backups/metadata"
LOCK_FILE="/tmp/monitoring-backup.lock"
RETENTION_DAYS="${1:-14}"
MIN_SPACE_MB=500  # 500MB minimum requis
MAX_BACKUP_AGE=30 # Jours max pour alerte ancienneté

# --- Initialisation ----------------------------------------------------------
echo "════════════════════════════════════════════════════════════"
echo "📦 BACKUP POSTGRESQL - $(date)"
echo "════════════════════════════════════════════════════════════"

# --- 1. Vérification lock file (éviter exécutions concurrentes) --------------
if [[ -f "$LOCK_FILE" ]]; then
  PID=$(cat "$LOCK_FILE" 2>/dev/null)
  if kill -0 "$PID" 2>/dev/null; then
    echo "⚠️  Backup déjà en cours (PID: $PID)"
    echo "   Lock file: $LOCK_FILE"
    exit 0
  else
    echo "⚠️  Lock file orphelin détecté, nettoyage..."
    rm -f "$LOCK_FILE"
  fi
fi

echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT INT TERM
echo "✅ Lock file créé: $LOCK_FILE"

# --- 2. Vérification espace disque -------------------------------------------
if [[ -d "$BACKUP_DIR" ]]; then
  AVAILABLE_SPACE=$(df -m "$BACKUP_DIR" 2>/dev/null | awk 'NR==2 {print $4}' || echo "0")
  if [[ -n "$AVAILABLE_SPACE" ]] && [[ "$AVAILABLE_SPACE" -lt $MIN_SPACE_MB ]]; then
    echo "❌ ESPACE DISQUE INSUFFISANT"
    echo "   Disponible: ${AVAILABLE_SPACE}MB"
    echo "   Requis: ${MIN_SPACE_MB}MB"
    exit 1
  fi
  echo "✅ Espace disque: ${AVAILABLE_SPACE}MB disponible"
fi

# --- 3. Création répertoires -------------------------------------------------
mkdir -p "$BACKUP_DIR"
mkdir -p "$METADATA_DIR"
echo "✅ Répertoires créés: $BACKUP_DIR, $METADATA_DIR"

# --- 4. Chargement variables d'environnement ---------------------------------
if [[ ! -f ".env.production" ]]; then
  echo "❌ ERREUR: .env.production manquant à $ROOT_DIR"
  echo "👉 Crée-le depuis .env.production.example"
  exit 1
fi

# Chargement sécurisé des variables
set -a
# shellcheck disable=SC1091
source ".env.production"
set +a

# Vérification variable critique
: "${DB_PASSWORD:?❌ DB_PASSWORD manquant dans .env.production}"
echo "✅ Variables d'environnement chargées"

# --- 5. Préparation backup ---------------------------------------------------
timestamp="$(date +%Y%m%d_%H%M%S)"
backup_file="${BACKUP_DIR}/monitoring_${timestamp}.sql.gz"
metadata_file="${METADATA_DIR}/backup_${timestamp}.json"

echo "→ Fichier backup: $backup_file"
echo "→ Fichier métadonnées: $metadata_file"
echo "→ Rétention: ${RETENTION_DAYS} jours"

# --- 6. Vérification/démarrage service DB ------------------------------------
echo "→ Vérification service PostgreSQL..."
if ! docker compose -f "$COMPOSE_FILE" ps db --format json 2>/dev/null | grep -q '"State":"running"'; then
  echo "⚠️  Service 'db' non démarré, démarrage..."
  docker compose -f "$COMPOSE_FILE" up -d db >/dev/null 2>&1
fi

# --- 7. Attente que PostgreSQL soit prêt -------------------------------------
echo "→ Attente réponse PostgreSQL (max 60s)..."
for i in {1..60}; do
  if docker compose -f "$COMPOSE_FILE" exec -T db \
      pg_isready -U postgres -d monitoring >/dev/null 2>&1; then
    echo "✅ PostgreSQL prêt après ${i}s"
    break
  fi
  
  if [[ $i -eq 60 ]]; then
    echo "❌ PostgreSQL non disponible après 60s"
    echo "📋 Logs PostgreSQL:"
    docker compose -f "$COMPOSE_FILE" logs --tail=50 db 2>/dev/null || true
    exit 1
  fi
  
  sleep 1
done

# --- 8. Collecte métadonnées pré-backup --------------------------------------
echo "→ Collecte métadonnées base de données..."
pre_backup_stats=$(docker compose -f "$COMPOSE_FILE" exec -T \
  -e PGPASSWORD="$DB_PASSWORD" db \
  psql -U postgres -d monitoring --quiet --no-align --tuples-only -c "
    SELECT json_build_object(
      'timestamp', NOW(),
      'database_name', current_database(),
      'database_size', pg_database_size(current_database()),
      'postgres_version', version(),
      'tables_count', (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'),
      'backup_start', '$(date -Iseconds)'
    );
  " 2>/dev/null || echo '{}')

# --- 9. Exécution du backup --------------------------------------------------
echo "→ Début du dump PostgreSQL..."
start_time=$(date +%s)

docker compose -f "$COMPOSE_FILE" exec -T \
  -e PGPASSWORD="$DB_PASSWORD" db \
  pg_dump -U postgres -d monitoring \
    --no-owner \
    --no-acl \
    --verbose \
    --format=p \
    --blobs \
    --encoding=UTF8 \
  | gzip -9 > "$backup_file"

end_time=$(date +%s)
duration=$((end_time - start_time))

# --- 10. Vérification intégrité backup ---------------------------------------
echo "→ Vérification intégrité backup..."
if [[ ! -s "$backup_file" ]]; then
  echo "❌ Backup vide ou échoué: $backup_file"
  rm -f "$backup_file"
  exit 1
fi

if ! gzip -t "$backup_file" 2>/dev/null; then
  echo "❌ Backup corrompu (gzip test échoué)"
  rm -f "$backup_file"
  exit 1
fi

backup_size=$(ls -lh "$backup_file" | awk '{print $5}')
backup_size_bytes=$(stat -c%s "$backup_file")
echo "✅ Backup validé: ${backup_size} (${backup_size_bytes} octets)"

# --- 11. Collecte métadonnées post-backup ------------------------------------
echo "→ Finalisation métadonnées..."
post_backup_stats=$(docker compose -f "$COMPOSE_FILE" exec -T \
  -e PGPASSWORD="$DB_PASSWORD" db \
  psql -U postgres -d monitoring --quiet --no-align --tuples-only -c "
    SELECT json_build_object(
      'backup_end', '$(date -Iseconds)',
      'backup_duration_seconds', $duration,
      'backup_size_bytes', $backup_size_bytes,
      'backup_file', '$backup_file'
    );
  " 2>/dev/null || echo '{}')

# --- 12. Création fichier métadonnées complet --------------------------------
final_metadata=$(echo "$pre_backup_stats" "$post_backup_stats" | jq -s 'add' 2>/dev/null || \
  echo "{\"pre_backup\": $pre_backup_stats, \"post_backup\": $post_backup_stats, \"timestamp\": \"$(date -Iseconds)\"}")

echo "$final_metadata" > "$metadata_file"
echo "✅ Métadonnées sauvegardées: $metadata_file"

# --- 13. Gestion rétention ---------------------------------------------------
echo "→ Nettoyage anciens backups (>${RETENTION_DAYS} jours)..."
deleted_count=0
while IFS= read -r -d '' old_file; do
  echo "   Suppression: $(basename "$old_file")"
  rm -f "$old_file"
  ((deleted_count++))
done < <(find "$BACKUP_DIR" -name "monitoring_*.sql.gz" -type f -mtime "+$RETENTION_DAYS" -print0 2>/dev/null)

# Nettoyage métadonnées correspondantes
find "$METADATA_DIR" -name "*.json" -type f -mtime "+$RETENTION_DAYS" -delete 2>/dev/null

echo "✅ ${deleted_count} ancien(s) backup(s) supprimé(s)"

# --- 14. Vérification ancienneté dernier backup ------------------------------
echo "→ Vérification fraîcheur backups..."
recent_backups=$(find "$BACKUP_DIR" -name "monitoring_*.sql.gz" -type f -mtime "-1" 2>/dev/null | wc -l)
if [[ $recent_backups -eq 0 ]]; then
  echo "⚠️  ATTENTION: Aucun backup créé dans les dernières 24h"
fi

oldest_backup=$(find "$BACKUP_DIR" -name "monitoring_*.sql.gz" -type f -printf '%T@ %p\n' 2>/dev/null | \
  sort -n | head -1 | cut -d' ' -f2-)

if [[ -n "$oldest_backup" ]]; then
  backup_age=$(( ( $(date +%s) - $(stat -c %Y "$oldest_backup") ) / 86400 ))
  if [[ $backup_age -gt $MAX_BACKUP_AGE ]]; then
    echo "⚠️  ATTENTION: Plus ancien backup a ${backup_age} jours"
  fi
fi

# --- 15. Statistiques finales ------------------------------------------------
total_backups=$(find "$BACKUP_DIR" -name "monitoring_*.sql.gz" -type f 2>/dev/null | wc -l)
total_size_mb=$(find "$BACKUP_DIR" -name "*.sql.gz" -type f -exec stat -c%s {} \; 2>/dev/null | \
  awk '{sum+=$1} END {print int(sum/1048576)}' || echo "0")

echo "════════════════════════════════════════════════════════════"
echo "📊 RAPPORT BACKUP COMPLET"
echo "════════════════════════════════════════════════════════════"
echo "   ✅ Durée: ${duration} secondes"
echo "   ✅ Taille: ${backup_size} (${backup_size_bytes} octets)"
echo "   ✅ Métadonnées: $(basename "$metadata_file")"
echo "   📁 Backups stockés: ${total_backups}"
echo "   💾 Espace total: ${total_size_mb} MB"
echo "   🗑️  Rétention: ${RETENTION_DAYS} jours"
echo "   🔄 Prochain nettoyage: $(date -d "+${RETENTION_DAYS} days" '+%Y-%m-%d')"
echo "════════════════════════════════════════════════════════════"

# --- 16. Nettoyage final -----------------------------------------------------
rm -f "$LOCK_FILE"
echo "✅ Backup terminé avec succès à $(date '+%H:%M:%S')"
exit 0