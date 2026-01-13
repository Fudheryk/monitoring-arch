#!/bin/bash
# =============================================================================
# Script d'initialisation Let's Encrypt pour neonmonitor.dockl.com
# =============================================================================
# À exécuter UNE SEULE FOIS lors du premier déploiement
# Usage: ./scripts/init-letsencrypt.sh
# =============================================================================

set -e

DOMAIN="neonmonitor.dockl.com"
EMAIL="frederic.gilgarcia@gmail.com"
STAGING=0  # Mettre à 1 pour tester avec staging Let's Encrypt

COMPOSE_FILE="docker/docker-compose.prod.yml"
CERTS_PATH="./docker/certs"

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Initialisation Let's Encrypt ===${NC}"
echo "Domaine: $DOMAIN"
echo "Email: $EMAIL"

# Vérification email
if [[ "$EMAIL" == "votre-email@example.com" ]]; then
    echo -e "${RED}ERREUR: Veuillez modifier l'email dans le script${NC}"
    exit 1
fi

# Création des répertoires
echo -e "${YELLOW}Création des répertoires...${NC}"
mkdir -p "$CERTS_PATH/live/$DOMAIN"

# Téléchargement des paramètres recommandés
echo -e "${YELLOW}Téléchargement des paramètres SSL recommandés...${NC}"
if [ ! -f "$CERTS_PATH/options-ssl-nginx.conf" ]; then
    curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
        > "$CERTS_PATH/options-ssl-nginx.conf"
fi

if [ ! -f "$CERTS_PATH/ssl-dhparams.pem" ]; then
    curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot/certbot/ssl-dhparams.pem \
        > "$CERTS_PATH/ssl-dhparams.pem"
fi

# Création certificat temporaire auto-signé pour démarrer Nginx
echo -e "${YELLOW}Création d'un certificat temporaire...${NC}"
openssl req -x509 -nodes -newkey rsa:2048 \
    -days 1 \
    -keyout "$CERTS_PATH/live/$DOMAIN/privkey.pem" \
    -out "$CERTS_PATH/live/$DOMAIN/fullchain.pem" \
    -subj "/CN=$DOMAIN"

# Démarrage de Nginx avec le certificat temporaire
echo -e "${YELLOW}Démarrage de Nginx...${NC}"
docker compose -f "$COMPOSE_FILE" up -d proxy

# Attente que Nginx soit prêt
echo -e "${YELLOW}Attente de Nginx...${NC}"
sleep 5

# Suppression du certificat temporaire
echo -e "${YELLOW}Suppression du certificat temporaire...${NC}"
rm -rf "$CERTS_PATH/live/$DOMAIN"

# Obtention du vrai certificat Let's Encrypt
echo -e "${YELLOW}Obtention du certificat Let's Encrypt...${NC}"
if [ $STAGING -eq 1 ]; then
    STAGING_ARG="--staging"
    echo -e "${YELLOW}Mode STAGING activé (test)${NC}"
else
    STAGING_ARG=""
fi

docker compose -f "$COMPOSE_FILE" run --rm certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    $STAGING_ARG \
    -d "$DOMAIN"

# Rechargement de Nginx avec le vrai certificat
echo -e "${YELLOW}Rechargement de Nginx...${NC}"
docker compose -f "$COMPOSE_FILE" exec proxy nginx -s reload

echo -e "${GREEN}=== Configuration SSL terminée avec succès ! ===${NC}"
echo -e "${GREEN}Le certificat sera automatiquement renouvelé tous les 12h${NC}"