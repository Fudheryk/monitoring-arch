#!/usr/bin/env bash
# =============================================================================
# Script d'initialisation / mise à jour Let's Encrypt (HTTP-01 webroot) via Docker
# pour : neonmonitor.dockl.com
# avec SAN : michevre1.vps.webdock.cloud
#
# À exécuter pour :
# 1) Première configuration
# 2) Ajouter / retirer des domaines (SAN) sur le certificat existant
#
# Usage:
#   ./scripts/init-letsencrypt.sh
#
# Hypothèses IMPORTANTES (sinon le challenge échouera) :
# - Le service "proxy" (nginx) sert bien :
#     /.well-known/acme-challenge/  =>  /var/www/certbot
# - Le service "certbot" et "proxy" partagent le même volume monté sur /var/www/certbot
# - Les certificats sont persistés dans CERTS_PATH (monté dans le container certbot sur /etc/letsencrypt)
# =============================================================================

set -euo pipefail

# =============================================================================
# CONFIGURATION - MODIFIER ICI
# =============================================================================

# Domaines (le 1er = Common Name, tous = SAN)
DOMAINS=("neonmonitor.dockl.com" "michevre1.vps.webdock.cloud")

# Email pour notifications Let's Encrypt
EMAIL="frederic.gilgarcia@gmail.com"

# Mode staging (0=production, 1=test)
STAGING=0

# =============================================================================
# PATHS (ne pas modifier sauf besoin spécifique)
# =============================================================================
COMPOSE_FILE="docker/docker-compose.prod.yml"
ENV_FILE=".env.production"

# Dossier local qui DOIT correspondre au volume /etc/letsencrypt dans le container certbot
CERTS_PATH="./docker/certs"

DOMAIN_PRIMARY="${DOMAINS[0]}"

# =============================================================================
# COULEURS POUR LA SORTIE
# =============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# =============================================================================
# LOGS
# =============================================================================
log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# =============================================================================
# PRÉREQUIS
# =============================================================================
check_prerequisites() {
  log_info "Vérification des prérequis..."

  # Email
  if [[ -z "${EMAIL}" || "${EMAIL}" == "votre-email@example.com" ]]; then
    log_error "Veuillez définir une adresse EMAIL valide."
    exit 1
  fi

  # Outils
  for bin in docker openssl curl; do
    if ! command -v "$bin" >/dev/null 2>&1; then
      log_error "$bin n'est pas installé"
      exit 1
    fi
  done

  # Docker Compose (plugin v2 ou binaire legacy)
  if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
    log_error "Docker Compose n'est pas installé (docker compose / docker-compose)"
    exit 1
  fi

  log_success "Prérequis vérifiés"
}

# Utilise "docker compose" (v2) ; si tu veux supporter docker-compose legacy, adapte ici.
dc() {
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

# =============================================================================
# OUTILS D'AIDE
# =============================================================================

# Construit les args -d pour certbot
build_domain_args() {
  local args=()
  for d in "${DOMAINS[@]}"; do
    args+=("-d" "$d")
  done
  printf '%s ' "${args[@]}"
}

# Vérifie OpenSSL >= 1.1.1 (nécessaire pour -addext)
openssl_supports_addext() {
  # On tente un "openssl req -help" et on cherche -addext
  openssl req -help 2>&1 | grep -q -- "-addext"
}

# Vérifie les SAN présents dans le certificat (tous les domaines)
verify_sans() {
  local cert="$1"
  local text
  text="$(openssl x509 -in "$cert" -text -noout 2>/dev/null || true)"

  if [[ -z "$text" ]]; then
    log_warning "Impossible de lire le certificat: $cert"
    return 1
  fi

  # Extrait la section SAN (best effort)
  local san_block
  san_block="$(printf "%s\n" "$text" | awk '/Subject Alternative Name/{flag=1;next}/X509v3/{if(flag){exit}}flag{print}' | tr -d '\r')"

  if [[ -z "$san_block" ]]; then
    log_warning "Section SAN introuvable dans le certificat."
    return 1
  fi

  local ok=1
  for d in "${DOMAINS[@]}"; do
    if ! printf "%s" "$san_block" | grep -q "DNS:${d}"; then
      ok=0
      log_warning "SAN manquant: $d"
    fi
  done

  if [[ "$ok" -eq 1 ]]; then
    log_success "Tous les SAN sont présents:"
    printf "%s\n" "$san_block" | sed 's/^/  /'
    return 0
  fi

  return 1
}

# =============================================================================
# DÉBUT
# =============================================================================
echo -e "${GREEN}=== Initialisation Let's Encrypt avec SAN ===${NC}"

# Vérification fichiers
if [[ ! -f "$COMPOSE_FILE" ]]; then
  log_error "Fichier compose introuvable: $COMPOSE_FILE"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  log_error "Fichier env introuvable: $ENV_FILE"
  exit 1
fi

check_prerequisites

echo "Domaines: ${DOMAINS[*]}"
echo "Common Name (CN): $DOMAIN_PRIMARY"
echo "Email: $EMAIL"
echo "Mode staging: $([[ $STAGING -eq 1 ]] && echo "OUI (test)" || echo "NON (production)")"

# =============================================================================
# ÉTAPE 1: Préparation répertoires Let's Encrypt
# =============================================================================
log_info "Préparation des répertoires Let's Encrypt (dans $CERTS_PATH)..."
mkdir -p "$CERTS_PATH"
mkdir -p "$CERTS_PATH/live" "$CERTS_PATH/archive" "$CERTS_PATH/renewal"

# Dossier du live pour le domaine principal (utilisé par nginx)
mkdir -p "$CERTS_PATH/live/$DOMAIN_PRIMARY"

# =============================================================================
# ÉTAPE 2: Téléchargement des paramètres SSL recommandés (si absents)
# =============================================================================
log_info "Téléchargement des paramètres SSL recommandés (si nécessaires)..."

if [[ ! -f "$CERTS_PATH/options-ssl-nginx.conf" ]]; then
  curl -fsSL \
    https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
    > "$CERTS_PATH/options-ssl-nginx.conf"
  log_success "options-ssl-nginx.conf téléchargé"
else
  log_info "options-ssl-nginx.conf existe déjà"
fi

if [[ ! -f "$CERTS_PATH/ssl-dhparams.pem" ]]; then
  curl -fsSL \
    https://raw.githubusercontent.com/certbot/certbot/master/certbot/certbot/ssl-dhparams.pem \
    > "$CERTS_PATH/ssl-dhparams.pem"
  log_success "ssl-dhparams.pem téléchargé"
else
  log_info "ssl-dhparams.pem existe déjà"
fi

# =============================================================================
# ÉTAPE 3: Certificat temporaire auto-signé (si aucun cert Let's Encrypt n'existe)
# =============================================================================
# But : permettre au proxy Nginx de démarrer (pour servir le challenge HTTP-01)
# On NE l'écrase PAS si un vrai certificat est déjà présent.
LE_FULLCHAIN="$CERTS_PATH/live/$DOMAIN_PRIMARY/fullchain.pem"
LE_PRIVKEY="$CERTS_PATH/live/$DOMAIN_PRIMARY/privkey.pem"

if [[ -f "$LE_FULLCHAIN" && -f "$LE_PRIVKEY" ]]; then
  log_info "Un certificat existe déjà dans $CERTS_PATH/live/$DOMAIN_PRIMARY (on ne crée pas de cert temporaire)."
else
  log_info "Création d'un certificat temporaire auto-signé (1 jour) pour démarrer Nginx..."

  if ! openssl_supports_addext; then
    log_warning "Votre OpenSSL ne supporte pas -addext (>= 1.1.1)."
    log_warning "Création d'un certificat temporaire SANS SAN (ça reste OK pour démarrer Nginx)."
    openssl req -x509 -nodes -newkey rsa:2048 \
      -days 1 \
      -keyout "$LE_PRIVKEY" \
      -out "$LE_FULLCHAIN" \
      -subj "/CN=$DOMAIN_PRIMARY"
  else
    # Liste SAN pour le cert temporaire
    SAN_LIST=""
    for domain in "${DOMAINS[@]}"; do
      SAN_LIST="${SAN_LIST}DNS:${domain},"
    done
    SAN_LIST="${SAN_LIST%,}"

    openssl req -x509 -nodes -newkey rsa:2048 \
      -days 1 \
      -keyout "$LE_PRIVKEY" \
      -out "$LE_FULLCHAIN" \
      -subj "/CN=$DOMAIN_PRIMARY" \
      -addext "subjectAltName = $SAN_LIST"
  fi

  log_success "Certificat temporaire prêt."
fi

# =============================================================================
# ÉTAPE 4: Démarrage / (re)démarrage du proxy
# =============================================================================
log_info "Démarrage de Nginx (service: proxy)..."
dc up -d proxy

log_info "Attente que Nginx soit prêt..."
for i in {1..15}; do
  if dc ps proxy | grep -q "Up"; then
    log_success "Nginx démarré"
    break
  fi
  sleep 2
  echo -n "."
done
echo ""
sleep 2

# =============================================================================
# ÉTAPE 5: Obtention / mise à jour du certificat Let's Encrypt via certbot
# =============================================================================
log_info "Obtention / mise à jour du certificat Let's Encrypt..."

DOMAIN_ARGS="$(build_domain_args)"

if [[ "$STAGING" -eq 1 ]]; then
  STAGING_ARG="--staging"
  log_warning "Mode STAGING activé (certificats de test)"
else
  STAGING_ARG=""
  log_info "Mode PRODUCTION"
fi

# IMPORTANT :
# - On NE supprime PAS le dossier live (évite de casser un cert existant si certbot échoue).
# - certbot gère l'upgrade/expand via --expand si nécessaire.
log_info "Exécution de certbot pour: ${DOMAINS[*]}"
dc run --rm certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  $STAGING_ARG \
  $DOMAIN_ARGS \
  --expand

log_success "Certbot terminé."

# =============================================================================
# ÉTAPE 6: Vérification locale du certificat (dans CERTS_PATH)
# =============================================================================
log_info "Vérification du certificat obtenu (dossier local monté)..."

if [[ -f "$LE_FULLCHAIN" ]]; then
  # Vérifie présence des SAN pour tous les domaines
  if verify_sans "$LE_FULLCHAIN"; then
    log_success "Certificat OK (SAN validés)."
  else
    log_warning "Certificat présent mais SAN non vérifiés (ou lecture SAN impossible)."
  fi
else
  log_error "Certificat introuvable dans: $LE_FULLCHAIN"
  log_error "=> Vérifie tes volumes docker (certbot doit écrire dans $CERTS_PATH)."
  exit 1
fi

# =============================================================================
# ÉTAPE 7: Reload Nginx pour prendre en compte le vrai certificat
# =============================================================================
log_info "Rechargement de Nginx avec le certificat Let's Encrypt..."
dc exec proxy nginx -s reload

sleep 2
if dc ps proxy | grep -q "Up"; then
  log_success "Nginx rechargé avec succès"
else
  log_error "Nginx ne tourne pas après reload"
  exit 1
fi

# =============================================================================
# ÉTAPE 8: Tests rapides de validation (best-effort)
# =============================================================================
log_info "Tests rapides (best-effort)..."
echo ""
echo "=== TEST 1: Vérification HTTPS (requête simple) ==="
for domain in "${DOMAINS[@]}"; do
  echo -n "Test https://$domain : "
  if curl -s --max-time 7 "https://$domain" >/dev/null 2>&1; then
    echo -e "${GREEN}OK${NC}"
  else
    echo -e "${YELLOW}KO (DNS / firewall / proxy / propagation)${NC}"
  fi
done

echo ""
echo "=== TEST 2: Vérification du certificat (openssl s_client) ==="
for domain in "${DOMAINS[@]}"; do
  echo -n "Certificat $domain : "
  if timeout 7 openssl s_client -connect "$domain:443" -servername "$domain" </dev/null 2>/dev/null | grep -q "Verify return code"; then
    echo -e "${GREEN}PRÉSENT${NC}"
  else
    echo -e "${YELLOW}NON VÉRIFIÉ (timeout / DNS / accès 443)${NC}"
  fi
done

# =============================================================================
# FIN
# =============================================================================
echo ""
echo -e "${GREEN}=== Configuration SSL terminée ! ===${NC}"
echo ""
echo "RÉSUMÉ:"
echo "  • Certificat Let's Encrypt pour:"
for domain in "${DOMAINS[@]}"; do
  echo "    - $domain"
done
echo "  • Common Name: $DOMAIN_PRIMARY"
echo "  • Email: $EMAIL"
echo "  • Certificats (live) dans: $CERTS_PATH/live/$DOMAIN_PRIMARY/"
echo ""
echo -e "${YELLOW}Note:${NC} Le renouvellement automatique dépend de ton setup (cron/systemd ou container certbot en loop)."
echo "Pour vérifier les SAN localement:"
echo "  openssl x509 -in $CERTS_PATH/live/$DOMAIN_PRIMARY/fullchain.pem -text -noout | grep -A1 'Subject Alternative Name'"
