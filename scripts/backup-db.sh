#!/bin/bash
# =============================================================================
# Script de backup PostgreSQL
# =============================================================================
# Crée un dump SQL compressé de la base de données
# Conserve les 14 dernières sauvegardes (2 semaines)
# Usage: ./scripts/backup-db.sh
# =============================================================================

set -e

COMPOSE_FILE="docker/docker-compose.prod.yml"
BACKUP_DIR="./backups/postgres"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/monitoring_$TIMESTAMP.sql.gz"
RETENTION_DAYS=14

# Couleurs
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}=== Backup PostgreSQL ===${NC}"

# Création du répertoire de backup
mkdir -p "$BACKUP_DIR"

# Chargement des variables d'environnement
if [ -f ".env.production" ]; then
    export $(cat .env.production | grep -v '^#' | xargs)
fi

# Extraction du password depuis DATABASE_URL
DB_PASSWORD=$(echo "$DATABASE_URL" | sed -n 's/.*:\/\/.*:\([^@]*\)@.*/\1/p')

if [ -z "$DB_PASSWORD" ]; then
    echo "ERREUR: Impossible d'extraire le mot de passe de la DATABASE_URL"
    exit 1
fi

# Dump de la base
echo "Création du backup..."
docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U postgres -d monitoring \
    | gzip > "$BACKUP_FILE"

# Vérification
if [ -f "$BACKUP_FILE" ] && [ -s "$BACKUP_FILE" ]; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo -e "${GREEN}✓ Backup créé: $BACKUP_FILE ($SIZE)${NC}"
else
    echo "ERREUR: Le backup a échoué"
    exit 1
fi

# Nettoyage des anciens backups
echo "Nettoyage des backups de plus de $RETENTION_DAYS jours..."
find "$BACKUP_DIR" -name "monitoring_*.sql.gz" -mtime +$RETENTION_DAYS -delete

# Liste des backups restants
echo "Backups disponibles:"
ls -lh "$BACKUP_DIR"/monitoring_*.sql.gz 2>/dev/null || echo "  (aucun)"

echo -e "${GREEN}=== Backup terminé ===${NC}"

# TODO: Upload vers Google Drive (à implémenter)
# ./scripts/upload-to-gdrive.sh "$BACKUP_FILE"