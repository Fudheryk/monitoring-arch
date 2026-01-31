#!/bin/bash
# =============================================================================
# Script de rollback
# =============================================================================
# Revient à la version précédente en cas de problème
# Usage: ./scripts/rollback.sh <version_precedente>
#        Ex: ./scripts/rollback.sh v1.2.2
# =============================================================================

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <version>"
    echo "Exemple: $0 v1.2.2"
    exit 1
fi

PREVIOUS_VERSION="$1"
COMPOSE_FILE="docker/docker-compose.prod.yml"
BACKUP_DIR="./backups/postgres"

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${RED}   ROLLBACK vers version: $PREVIOUS_VERSION${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

# Confirmation
read -p "Êtes-vous sûr de vouloir rollback ? (yes/no): " -r
if [[ ! $REPLY =~ ^yes$ ]]; then
    echo "Annulé."
    exit 0
fi

# Export de la version
export VERSION="$PREVIOUS_VERSION"

# -----------------------------------------------------------------------------
# 1. Liste des backups disponibles
# -----------------------------------------------------------------------------
echo -e "${YELLOW}Backups disponibles:${NC}"
ls -lh "$BACKUP_DIR"/monitoring_*.sql.gz 2>/dev/null || echo "Aucun backup trouvé"
echo ""
read -p "Voulez-vous restaurer un backup ? (yes/no): " -r
RESTORE_DB=$REPLY

# -----------------------------------------------------------------------------
# 2. Pull de la version précédente
# -----------------------------------------------------------------------------
echo -e "${YELLOW}Téléchargement de la version $PREVIOUS_VERSION...${NC}"
docker compose -f "$COMPOSE_FILE" pull

# -----------------------------------------------------------------------------
# 3. Restauration DB (optionnel)
# -----------------------------------------------------------------------------
if [[ $RESTORE_DB =~ ^yes$ ]]; then
    echo -e "${YELLOW}Entrez le nom du fichier de backup (ex: monitoring_20240115_120000.sql.gz):${NC}"
    read BACKUP_FILE
    
    if [ -f "$BACKUP_DIR/$BACKUP_FILE" ]; then
        echo -e "${YELLOW}Restauration de $BACKUP_FILE...${NC}"
        gunzip -c "$BACKUP_DIR/$BACKUP_FILE" | \
            docker compose -f "$COMPOSE_FILE" exec -T db \
            psql -U postgres -d monitoring
        echo -e "${GREEN}✓ Base de données restaurée${NC}"
    else
        echo -e "${RED}Fichier non trouvé: $BACKUP_DIR/$BACKUP_FILE${NC}"
        exit 1
    fi
fi

# -----------------------------------------------------------------------------
# 4. Redémarrage des services
# -----------------------------------------------------------------------------
echo -e "${YELLOW}Redémarrage des services avec version $PREVIOUS_VERSION...${NC}"

services="beat worker api web"
for service in $services; do
    echo -e "  ${BLUE}→ Rollback de $service...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --no-deps --force-recreate "$service"
    sleep 3
done

# -----------------------------------------------------------------------------
# 5. Vérification
# -----------------------------------------------------------------------------
echo -e "${YELLOW}Vérification...${NC}"
sleep 10
./scripts/health-check.sh

if [ $? -eq 0 ]; then
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}✓ Rollback réussi vers $PREVIOUS_VERSION${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
else
    echo -e "${RED}✗ Le health check a échoué après rollback${NC}"
    echo "Consultez les logs pour plus d'informations"
    exit 1
fi