#!/bin/bash
# =============================================================================
# Script d'initialisation Let's Encrypt pour neonmonitor.dockl.com
# AVEC michevre1.vps.webdock.cloud comme SAN (Subject Alternative Name)
# =============================================================================
# À exécuter pour :
# 1. Première configuration
# 2. Ajout d'un nouveau domaine au certificat existant
# Usage: ./scripts/init-letsencrypt.sh
# =============================================================================

set -e

# =============================================================================
# CONFIGURATION - MODIFIER ICI
# =============================================================================

# DOMAINS PRINCIPAUX (séparés par des espaces)
# Le premier domaine sera le Common Name (CN)
# Tous les domaines seront ajoutés comme Subject Alternative Names (SAN)
DOMAINS=("neonmonitor.dockl.com" "michevre1.vps.webdock.cloud")

# Email pour notifications Let's Encrypt
EMAIL="frederic.gilgarcia@gmail.com"

# Mode staging (0=production, 1=test) - utiliser 1 pour les tests
STAGING=0

# =============================================================================
# PATHS (ne pas modifier sauf besoin spécifique)
# =============================================================================
COMPOSE_FILE="docker/docker-compose.prod.yml"
CERTS_PATH="./docker/certs"
DOMAIN_PRIMARY="${DOMAINS[0]}"  # Premier domaine = Common Name

# =============================================================================
# COULEURS POUR LA SORTIE
# =============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# =============================================================================
# FONCTIONS
# =============================================================================
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_prerequisites() {
    log_info "Vérification des prérequis..."
    
    # Vérifier email
    if [[ "$EMAIL" == "votre-email@example.com" ]]; then
        log_error "Veuillez modifier l'email dans le script (ligne 17)"
        exit 1
    fi
    
    # Vérifier docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker n'est pas installé"
        exit 1
    fi
    
    # Vérifier docker-compose
    if ! docker compose version &> /dev/null && ! command -v docker-compose &> /dev/null; then
        log_error "Docker Compose n'est pas installé"
        exit 1
    fi
    
    # Vérifier openssl
    if ! command -v openssl &> /dev/null; then
        log_error "OpenSSL n'est pas installé"
        exit 1
    fi
    
    log_success "Prérequis vérifiés"
}

# =============================================================================
# DÉBUT DU SCRIPT
# =============================================================================

echo -e "${GREEN}=== Initialisation Let's Encrypt avec SAN ===${NC}"
echo "Domaines: ${DOMAINS[*]}"
echo "Common Name (CN): $DOMAIN_PRIMARY"
echo "Email: $EMAIL"
echo "Mode staging: $([ $STAGING -eq 1 ] && echo "OUI (test)" || echo "NON (production)")"

# Vérification des prérequis
check_prerequisites

# =============================================================================
# ÉTAPE 1: Création des répertoires
# =============================================================================
log_info "Création des répertoires..."
mkdir -p "$CERTS_PATH/live/$DOMAIN_PRIMARY"

# =============================================================================
# ÉTAPE 2: Téléchargement des paramètres SSL recommandés
# =============================================================================
log_info "Téléchargement des paramètres SSL recommandés..."
if [ ! -f "$CERTS_PATH/options-ssl-nginx.conf" ]; then
    curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
        > "$CERTS_PATH/options-ssl-nginx.conf"
    log_success "options-ssl-nginx.conf téléchargé"
else
    log_info "options-ssl-nginx.conf existe déjà"
fi

if [ ! -f "$CERTS_PATH/ssl-dhparams.pem" ]; then
    curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot/certbot/ssl-dhparams.pem \
        > "$CERTS_PATH/ssl-dhparams.pem"
    log_success "ssl-dhparams.pem téléchargé"
else
    log_info "ssl-dhparams.pem existe déjà"
fi

# =============================================================================
# ÉTAPE 3: Création certificat temporaire auto-signé (pour démarrer Nginx)
# =============================================================================
log_info "Création d'un certificat temporaire auto-signé..."
# Crée une liste SAN pour le certificat temporaire
SAN_LIST=""
for domain in "${DOMAINS[@]}"; do
    SAN_LIST="${SAN_LIST}DNS:${domain},"
done
SAN_LIST="${SAN_LIST%,}"  # Enlever la dernière virgule

openssl req -x509 -nodes -newkey rsa:2048 \
    -days 1 \
    -keyout "$CERTS_PATH/live/$DOMAIN_PRIMARY/privkey.pem" \
    -out "$CERTS_PATH/live/$DOMAIN_PRIMARY/fullchain.pem" \
    -subj "/CN=$DOMAIN_PRIMARY" \
    -addext "subjectAltName = $SAN_LIST"

log_success "Certificat temporaire créé avec SAN: ${DOMAINS[*]}"

# =============================================================================
# ÉTAPE 4: Démarrage de Nginx avec le certificat temporaire
# =============================================================================
log_info "Démarrage de Nginx avec certificat temporaire..."
docker compose -f "$COMPOSE_FILE" up -d proxy

# Attente que Nginx soit prêt
log_info "Attente que Nginx soit prêt..."
for i in {1..10}; do
    if docker compose -f "$COMPOSE_FILE" ps proxy | grep -q "Up"; then
        log_success "Nginx démarré"
        break
    fi
    sleep 2
    echo -n "."
done
echo ""
sleep 3  # Attente supplémentaire pour être sûr

# =============================================================================
# ÉTAPE 5: Obtention du vrai certificat Let's Encrypt
# =============================================================================
log_info "Obtention du certificat Let's Encrypt..."

# Construction de la liste des domaines pour certbot
DOMAIN_ARGS=""
for domain in "${DOMAINS[@]}"; do
    DOMAIN_ARGS="$DOMAIN_ARGS -d $domain"
done

# Mode staging ou production
if [ $STAGING -eq 1 ]; then
    STAGING_ARG="--staging"
    log_warning "Mode STAGING activé (pour les tests)"
else
    STAGING_ARG=""
    log_info "Mode PRODUCTION"
fi

# Suppression du certificat temporaire (certbot va créer les vrais)
log_info "Suppression du certificat temporaire..."
rm -rf "$CERTS_PATH/live/$DOMAIN_PRIMARY"

# Exécution de certbot pour obtenir le vrai certificat
log_info "Exécution de certbot pour les domaines: ${DOMAINS[*]}"
docker compose -f "$COMPOSE_FILE" run --rm certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    $STAGING_ARG \
    $DOMAIN_ARGS \
    --expand  # Important: ajoute les domaines au certificat existant

# =============================================================================
# ÉTAPE 6: Vérification du certificat obtenu
# =============================================================================
log_info "Vérification du certificat obtenu..."
if [ -f "/etc/letsencrypt/live/$DOMAIN_PRIMARY/fullchain.pem" ]; then
    # Copie les certificats dans le répertoire local si certbot était exécuté en local
    sudo cp -r /etc/letsencrypt/live/$DOMAIN_PRIMARY/* "$CERTS_PATH/live/$DOMAIN_PRIMARY/" 2>/dev/null || true
fi

if [ -f "$CERTS_PATH/live/$DOMAIN_PRIMARY/fullchain.pem" ]; then
    # Vérifie que les SAN sont présents
    SAN_CHECK=$(openssl x509 -in "$CERTS_PATH/live/$DOMAIN_PRIMARY/fullchain.pem" -text -noout | grep -A1 "Subject Alternative Name" || echo "")
    
    if [[ "$SAN_CHECK" == *"DNS:$DOMAIN_PRIMARY"* ]] && [[ "$SAN_CHECK" == *"DNS:michevre1.vps.webdock.cloud"* ]]; then
        log_success "Certificat valide avec tous les SAN"
        echo "$SAN_CHECK"
    else
        log_warning "SAN non vérifiés dans le certificat"
    fi
else
    log_warning "Certificat non trouvé localement, vérifiez dans /etc/letsencrypt/live/"
fi

# =============================================================================
# ÉTAPE 7: Rechargement de Nginx avec le vrai certificat
# =============================================================================
log_info "Rechargement de Nginx avec le vrai certificat..."
docker compose -f "$COMPOSE_FILE" exec proxy nginx -s reload

# Vérification que Nginx tourne toujours
sleep 2
if docker compose -f "$COMPOSE_FILE" ps proxy | grep -q "Up"; then
    log_success "Nginx rechargé avec succès"
else
    log_error "Nginx ne tourne pas après rechargement"
    exit 1
fi

# =============================================================================
# ÉTAPE 8: Tests de validation
# =============================================================================
log_info "Tests de validation..."
echo ""
echo "=== TEST 1: Vérification HTTPS ==="
for domain in "${DOMAINS[@]}"; do
    echo -n "Test $domain: "
    # Test avec timeout et ignore les erreurs SSL pour le test
    if curl -s -k --max-time 5 "https://$domain" > /dev/null 2>&1; then
        echo -e "${GREEN}OK${NC}"
    else
        echo -e "${YELLOW}Attention: impossible de contacter $domain${NC}"
        echo "  Cela peut être normal si le DNS n'est pas encore propagé"
    fi
done

echo ""
echo "=== TEST 2: Vérification certificat ==="
for domain in "${DOMAINS[@]}"; do
    echo -n "Certificat $domain: "
    # Utilise openssl pour vérifier le certificat
    if timeout 5 openssl s_client -connect "$domain:443" -servername "$domain" < /dev/null 2>/dev/null | grep -q "Verify return code"; then
        echo -e "${GREEN}PRÉSENT${NC}"
    else
        echo -e "${YELLOW}NON VÉRIFIÉ (DNS ou timeout)${NC}"
    fi
done

# =============================================================================
# FIN DU SCRIPT
# =============================================================================
echo ""
echo -e "${GREEN}=== Configuration SSL terminée avec succès ! ===${NC}"
echo ""
echo "RÉSUMÉ:"
echo "  • Certificat Let's Encrypt obtenu pour:"
for domain in "${DOMAINS[@]}"; do
    echo "    - $domain"
done
echo "  • Common Name: $DOMAIN_PRIMARY"
echo "  • Email: $EMAIL"
echo "  • Certificats stockés dans: $CERTS_PATH/live/$DOMAIN_PRIMARY/"
echo ""
echo -e "${GREEN}Le certificat sera automatiquement renouvelé tous les 90 jours${NC}"
echo -e "${YELLOW}Remarque: Le renouvellement automatique est géré par le container certbot${NC}"
echo ""
echo -e "${BLUE}Pour ajouter un nouveau domaine plus tard:${NC}"
echo "  1. Ajoutez le domaine dans la variable DOMAINS du script"
echo "  2. Relancez ce script (il ajoutera le domaine au certificat existant)"
echo "  3. Modifiez la configuration Nginx pour inclure le nouveau domaine"
echo ""
echo -e "${BLUE}Pour vérifier manuellement le certificat:${NC}"
echo "  openssl x509 -in $CERTS_PATH/live/$DOMAIN_PRIMARY/fullchain.pem -text -noout | grep -A1 'Subject Alternative Name'"