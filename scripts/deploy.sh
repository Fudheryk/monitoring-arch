#!/bin/bash
# =============================================================================
# Script de déploiement production
# =============================================================================
# Déploie la dernière version depuis Docker Hub avec zero-downtime
# Usage: ./scripts/deploy.sh [version]
#        version (optionnel): latest (défaut) ou v1.2.3
# =============================================================================

set -e

VERSION="${1:-latest}"
COMPOSE_FILE="docker/docker-compose.prod.yml"
BACKUP_DIR="./backups"

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   Déploiement Production - Version: $VERSION${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

# Vérification du fichier .env.production
if [ ! -f ".env.production" ]; then
    echo -e "${RED}ERREUR: Fichier .env.production manquant${NC}"
    echo "Créez-le à partir de .env.production.template"
    exit 1
fi

# Export de la version pour docker-compose
export VERSION="$VERSION"

# -----------------------------------------------------------------------------
# 1. Backup de la base de données
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[1/6] Backup de la base de données...${NC}"
./scripts/backup-db.sh

# -----------------------------------------------------------------------------
# 2. Pull des nouvelles images
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[2/6] Téléchargement des nouvelles images...${NC}"
docker compose -f "$COMPOSE_FILE" pull

# -----------------------------------------------------------------------------
# 3. Arrêt et démarrage du job de migration
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[3/6] Exécution des migrations...${NC}"
docker compose -f "$COMPOSE_FILE" up migrate

if [ $? -ne 0 ]; then
    echo -e "${RED}ERREUR: Migration échouée. Déploiement annulé.${NC}"
    exit 1
fi

# -----------------------------------------------------------------------------
# 4. Mise à jour des services (zero-downtime)
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[4/6] Mise à jour des services...${NC}"

# Ordre de mise à jour : worker/beat (pas de downtime), puis api, puis web
services="beat worker api web"

for service in $services; do
    echo -e "  ${BLUE}→ Mise à jour de $service...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --no-deps --force-recreate "$service"
    
    # Attente que le service soit healthy
    if [[ "$service" == "api" ]] || [[ "$service" == "web" ]]; then
        echo -e "    Attente du healthcheck..."
        for i in {1..30}; do
            if docker compose -f "$COMPOSE_FILE" ps "$service" | grep -q "healthy"; then
                echo -e "    ${GREEN}✓ $service est healthy${NC}"
                break
            fi
            if [ $i -eq 30 ]; then
                echo -e "${RED}ERREUR: $service n'est pas passé healthy${NC}"
                exit 1
            fi
            sleep 2
        done
    else
        sleep 5
    fi
done

# -----------------------------------------------------------------------------
# 5. Nettoyage des anciennes images
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[5/6] Nettoyage des anciennes images...${NC}"
docker image prune -f

# -----------------------------------------------------------------------------
# 6. Vérification finale
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[6/6] Vérification finale...${NC}"
./scripts/health-check.sh

if [ $? -eq 0 ]; then
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}✓ Déploiement réussi !${NC}"
    echo -e "${GREEN}  Version: $VERSION${NC}"
    echo -e "${GREEN}  Date: $(date)${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
else
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}✗ ERREUR: Le health check a échoué${NC}"
    echo -e "${RED}  Consultez les logs: docker compose -f $COMPOSE_FILE logs${NC}"
    echo -e "${RED}  Pour rollback: ./scripts/rollback.sh${NC}"
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
    exit 1
fi