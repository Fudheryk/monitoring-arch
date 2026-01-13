# Guide de Déploiement Production

## 📋 Prérequis Serveur

### Configuration Minimale
- **OS** : Ubuntu 22.04 LTS ou Debian 11+
- **RAM** : 1.5 GB minimum (votre config actuelle)
- **CPU** : 2 cores
- **Disque** : 15 GB
- **Swap** : 1 GB configuré

### Logiciels Requis
```bash
# Docker Engine 24.0+
curl -fsSL https://get.docker.com | sh

# Docker Compose v2
sudo apt-get install docker-compose-plugin

# Git
sudo apt-get install git

# Optionnel : monitoring outils
sudo apt-get install htop ncdu
```

---

## 🚀 Première Installation

### 1. Configuration DNS

Assurez-vous que `neonmonitor.dockl.com` pointe vers l'IP de votre serveur :

```bash
# Vérification
dig neonmonitor.dockl.com
```

### 2. Clone du Repository

```bash
cd /opt
git clone https://github.com/votre-username/monitoring-arch.git
cd monitoring-arch
```

### 3. Configuration des Variables d'Environnement

```bash
# Copier le template
cp .env.production.template .env.production

# Éditer avec vos secrets
nano .env.production
```

**Variables OBLIGATOIRES à modifier :**

```bash
# Générer JWT_SECRET (64+ caractères)
openssl rand -hex 32

# Générer DB_PASSWORD
openssl rand -base64 24

# Générer REDIS_PASSWORD
openssl rand -base64 24

# Ajouter votre mot de passe GMX
SMTP_PASSWORD=VotreMotDePasseGMX

# Ajouter votre API_KEY
API_KEY=$(openssl rand -hex 32)
```

### 4. Configuration des Secrets Docker Hub

Pour le déploiement automatisé depuis GitHub Actions :

```bash
# Sur GitHub : Settings > Secrets and variables > Actions
DOCKER_USERNAME=votre_username_dockerhub
DOCKER_PASSWORD=votre_token_dockerhub
```

### 5. Initialisation SSL/TLS (Let's Encrypt)

```bash
# Éditer l'email dans le script
nano scripts/init-letsencrypt.sh
# Modifier : EMAIL="votre-email@example.com"

# Rendre exécutable
chmod +x scripts/*.sh

# Exécuter l'initialisation SSL
./scripts/init-letsencrypt.sh
```

Ce script va :
- Créer un certificat temporaire
- Démarrer Nginx
- Obtenir le vrai certificat Let's Encrypt
- Configurer le renouvellement automatique

### 6. Premier Déploiement

```bash
# Export des variables pour docker-compose
export VERSION=latest
export DOCKER_USERNAME=votre_username

# Démarrage de tous les services
cd docker
docker compose -f docker-compose.prod.yml up -d

# Vérifier les logs
docker compose -f docker-compose.prod.yml logs -f

# Attendre que tous les services soient healthy (~2 minutes)
```

### 7. Vérification

```bash
# Health check automatisé
./scripts/health-check.sh

# Ou manuellement
curl https://neonmonitor.dockl.com/api/v1/health
curl https://neonmonitor.dockl.com/_health
```

---

## 🔄 Procédure de Mise à Jour

### Mise à jour automatique (recommandée)

Une fois votre code poussé sur GitHub et les images buildées par CI/CD :

```bash
cd /opt/monitoring-arch

# Pull du code
git pull origin main

# Déploiement automatique avec backup
./scripts/deploy.sh

# Ou spécifier une version
./scripts/deploy.sh v1.2.3
```

Le script `deploy.sh` effectue automatiquement :
1. ✅ Backup de la base de données
2. ✅ Pull des nouvelles images Docker Hub
3. ✅ Exécution des migrations
4. ✅ Mise à jour rolling des services (zero-downtime)
5. ✅ Health check final
6. ✅ Nettoyage des anciennes images

**Temps de mise à jour : ~1-2 minutes**

### Mise à jour manuelle

```bash
export VERSION=v1.2.3
export DOCKER_USERNAME=votre_username

cd docker
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

---

## 🔙 Rollback

En cas de problème après une mise à jour :

```bash
# Revenir à la version précédente
./scripts/rollback.sh v1.2.2

# Le script vous demandera si vous voulez restaurer un backup DB
```

---

## 💾 Backup & Restore

### Backup Manuel

```bash
# Backup de la base de données (automatique lors du deploy)
./scripts/backup-db.sh

# Les backups sont stockés dans ./backups/postgres/
# Format : monitoring_YYYYMMDD_HHMMSS.sql.gz
# Rétention : 14 jours (2 semaines)
```

### Backup Automatisé

Le backup est automatique lors de chaque `deploy.sh`, mais vous pouvez configurer un cron :

```bash
# Éditer le crontab
crontab -e

# Ajouter : backup quotidien à 3h du matin
0 3 * * * cd /opt/monitoring-arch && ./scripts/backup-db.sh >> /var/log/monitoring-backup.log 2>&1
```

### Restore depuis Backup

```bash
# Lister les backups disponibles
ls -lh ./backups/postgres/

# Restaurer un backup spécifique
gunzip -c ./backups/postgres/monitoring_20240115_120000.sql.gz | \
  docker compose -f docker/docker-compose.prod.yml exec -T db \
  psql -U postgres -d monitoring

# Redémarrer les services
docker compose -f docker/docker-compose.prod.yml restart api worker beat
```

### Upload Google Drive (TODO)

À implémenter : script pour uploader automatiquement les backups vers Google Drive.

---

## 📊 Monitoring & Logs

### Consulter les Logs

```bash
# Tous les services
docker compose -f docker/docker-compose.prod.yml logs -f

# Service spécifique
docker compose -f docker/docker-compose.prod.yml logs -f api
docker compose -f docker/docker-compose.prod.yml logs -f worker

# Dernières 100 lignes
docker compose -f docker/docker-compose.prod.yml logs --tail=100 api
```

### Rotation des Logs

Les logs sont automatiquement limités :
- **Max size** : 10 MB par fichier
- **Max files** : 3 fichiers conservés
- **Format** : JSON pour parsing facile

### Espace Disque

```bash
# Vérifier l'espace utilisé
df -h

# Espace par conteneur
docker system df

# Nettoyer les images inutilisées
docker image prune -a -f

# Nettoyer tout (ATTENTION : supprime volumes non utilisés)
# docker system prune -a --volumes
```

### Ressources en Temps Réel

```bash
# CPU/RAM par conteneur
docker stats

# Ou avec htop
htop
```

---

## 🔒 Sécurité

### Firewall

```bash
# Installer ufw
sudo apt-get install ufw

# Autoriser SSH, HTTP, HTTPS
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Activer
sudo ufw enable

# Vérifier
sudo ufw status
```

### Mise à Jour du Système

```bash
# Mises à jour de sécurité Ubuntu/Debian
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get autoremove -y

# Redémarrage si nécessaire (kernel updates)
sudo reboot
```

### Rotation des Secrets

Périodiquement, régénérer les secrets :

```bash
# Générer de nouveaux secrets
NEW_JWT_SECRET=$(openssl rand -hex 32)
NEW_DB_PASSWORD=$(openssl rand -base64 24)
NEW_REDIS_PASSWORD=$(openssl rand -base64 24)

# Mettre à jour .env.production
nano .env.production

# Redémarrer les services
docker compose -f docker/docker-compose.prod.yml down
docker compose -f docker/docker-compose.prod.yml up -d
```

---

## 🆘 Troubleshooting

### Service ne démarre pas

```bash
# Vérifier les logs
docker compose -f docker/docker-compose.prod.yml logs service_name

# Vérifier le statut
docker compose -f docker/docker-compose.prod.yml ps

# Redémarrer un service
docker compose -f docker/docker-compose.prod.yml restart service_name
```

### Migration échoue

```bash
# Lancer manuellement la migration
docker compose -f docker/docker-compose.prod.yml run --rm migrate

# Si problème, se connecter à la DB
docker compose -f docker/docker-compose.prod.yml exec db \
  psql -U postgres -d monitoring
```

### Espace disque plein

```bash
# Vérifier les plus gros répertoires
ncdu /

# Nettoyer Docker
docker system prune -a -f

# Nettoyer logs anciens
find /var/log -type f -name "*.log" -mtime +30 -delete

# Nettoyer backups anciens (garder 14 jours)
find ./backups/postgres -name "*.sql.gz" -mtime +14 -delete
```

### Certificat SSL expiré

Let's Encrypt renouvelle automatiquement, mais si problème :

```bash
# Forcer le renouvellement
docker compose -f docker/docker-compose.prod.yml run --rm certbot renew

# Recharger Nginx
docker compose -f docker/docker-compose.prod.yml exec proxy nginx -s reload
```

### Performance lente

```bash
# Vérifier les ressources
docker stats

# Si DB slow, vérifier les connexions
docker compose -f docker/docker-compose.prod.yml exec db \
  psql -U postgres -d monitoring -c "SELECT count(*) FROM pg_stat_activity;"

# Vérifier Redis memory
docker compose -f docker/docker-compose.prod.yml exec redis \
  redis-cli --pass "$REDIS_PASSWORD" INFO memory
```

---

## 📈 Optimisations Futures

### Scaling Horizontal

Pour gérer plus de charge :

```bash
# Augmenter le nombre de workers
docker compose -f docker/docker-compose.prod.yml up -d --scale worker=3
```

### Monitoring Avancé

- [ ] Implémenter Prometheus + Grafana
- [ ] Configurer Sentry pour tracking erreurs
- [ ] Alerting système via Slack/Email

### Backup Cloud

- [ ] Implémenter upload Google Drive automatique
- [ ] Configurer backup incrémental

---

## 📞 Support

En cas de problème non résolu :

1. Consulter les logs : `docker compose logs -f`
2. Vérifier le health check : `./scripts/health-check.sh`
3. Tester le rollback : `./scripts/rollback.sh <version>`
4. Contacter le support ou ouvrir une issue GitHub