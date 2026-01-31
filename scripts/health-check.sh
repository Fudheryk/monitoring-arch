#!/bin/bash
# =============================================================================
# Script de vérification de santé
# =============================================================================
# Vérifie que tous les services critiques sont opérationnels
# Usage: ./scripts/health-check.sh
# Exit 0 si tout est OK, 1 si erreur
# =============================================================================

set -e

DOMAIN="https://neonmonitor.dockl.com"
COMPOSE_FILE="docker/docker-compose.prod.yml"

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}=== Health Check ===${NC}"

FAILED=0

# -----------------------------------------------------------------------------
# 1. Vérifier que tous les conteneurs sont running
# -----------------------------------------------------------------------------
echo -n "Conteneurs running... "
EXPECTED_CONTAINERS="monitoring-api monitoring-worker monitoring-beat monitoring-web monitoring-db monitoring-redis monitoring-proxy"
RUNNING=$(docker ps --format "{{.Names}}" | tr '\n' ' ')

for container in $EXPECTED_CONTAINERS; do
    if ! echo "$RUNNING" | grep -q "$container"; then
        echo -e "${RED}✗${NC}"
        echo "  Conteneur manquant: $container"
        FAILED=1
    fi
done

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓${NC}"
fi

# -----------------------------------------------------------------------------
# 2. Vérifier les healthchecks Docker
# -----------------------------------------------------------------------------
echo -n "Docker healthchecks... "
UNHEALTHY=$(docker ps --filter "health=unhealthy" --format "{{.Names}}" | tr '\n' ' ')

if [ -n "$UNHEALTHY" ]; then
    echo -e "${RED}✗${NC}"
    echo "  Conteneurs unhealthy: $UNHEALTHY"
    FAILED=1
else
    echo -e "${GREEN}✓${NC}"
fi

# -----------------------------------------------------------------------------
# 3. Vérifier API endpoint
# -----------------------------------------------------------------------------
echo -n "API Health endpoint... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$DOMAIN/api/v1/health" --max-time 10)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ (HTTP $HTTP_CODE)${NC}"
else
    echo -e "${RED}✗ (HTTP $HTTP_CODE)${NC}"
    FAILED=1
fi

# -----------------------------------------------------------------------------
# 4. Vérifier Web frontend
# -----------------------------------------------------------------------------
echo -n "Web Health endpoint... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$DOMAIN/_health" --max-time 10)

if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✓ (HTTP $HTTP_CODE)${NC}"
else
    echo -e "${RED}✗ (HTTP $HTTP_CODE)${NC}"
    FAILED=1
fi

# -----------------------------------------------------------------------------
# 5. Vérifier connexion PostgreSQL
# -----------------------------------------------------------------------------
echo -n "PostgreSQL... "
if docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U postgres -d monitoring > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
    FAILED=1
fi

# -----------------------------------------------------------------------------
# 6. Vérifier connexion Redis
# -----------------------------------------------------------------------------
echo -n "Redis... "
# Charger le password depuis .env.production
if [ -f ".env.production" ]; then
    REDIS_PASS=$(grep REDIS_PASSWORD .env.production | cut -d '=' -f2)
fi

if docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli --pass "$REDIS_PASS" ping > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
    FAILED=1
fi

# -----------------------------------------------------------------------------
# 7. Vérifier les workers Celery
# -----------------------------------------------------------------------------
echo -n "Celery workers... "
CELERY_STATUS=$(docker compose -f "$COMPOSE_FILE" exec -T worker celery -A app.workers.celery_app.celery inspect active 2>&1)

if echo "$CELERY_STATUS" | grep -q "OK"; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${YELLOW}⚠ (pas de tâches actives)${NC}"
fi

# -----------------------------------------------------------------------------
# Résultat final
# -----------------------------------------------------------------------------
echo -e "${YELLOW}==============================${NC}"
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ Tous les checks sont passés${NC}"
    exit 0
else
    echo -e "${RED}✗ Certains checks ont échoué${NC}"
    echo "Consultez les logs: docker compose -f $COMPOSE_FILE logs --tail=100"
    exit 1
fi