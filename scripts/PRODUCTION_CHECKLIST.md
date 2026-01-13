# Checklist Mise en Production

## 🎯 Avant Premier Déploiement

### Configuration Serveur
- [ ] Serveur VPS Webdock provisionné (1.5 GB RAM, 2 CPU, 15 GB disque)
- [ ] Ubuntu 22.04 LTS ou Debian 11+ installé
- [ ] Docker Engine 24.0+ installé
- [ ] Docker Compose v2 installé
- [ ] Git installé
- [ ] Firewall configuré (ports 22, 80, 443)

### DNS & Domaine
- [ ] `neonmonitor.dockl.com` pointe vers IP du serveur
- [ ] DNS propagé (vérifier avec `dig neonmonitor.dockl.com`)

### Secrets Générés
```bash
# À exécuter pour générer les secrets
JWT_SECRET=$(openssl rand -hex 32)
DB_PASSWORD=$(openssl rand -base64 24)
REDIS_PASSWORD=$(openssl rand -base64 24)
API_KEY=$(openssl rand -hex 32)
```

- [ ] `JWT_SECRET` généré (64+ caractères)
- [ ] `DB_PASSWORD` généré et sécurisé
- [ ] `REDIS_PASSWORD` généré et sécurisé
- [ ] `API_KEY` généré
- [ ] Mot de passe GMX `SMTP_PASSWORD` récupéré
- [ ] `.env.production` créé et rempli
- [ ] `.env.production` **JAMAIS** commité dans Git

### Docker Hub
- [ ] Compte Docker Hub créé (gratuit)
- [ ] Repository `monitoring-api` créé (public OK)
- [ ] Repository `monitoring-web` créé (public OK)

### GitHub Secrets
Sur GitHub : Settings > Secrets and variables > Actions

- [ ] `DOCKER_USERNAME` configuré
- [ ] `DOCKER_PASSWORD` configuré (token, pas le password)

### Fichiers Production Créés
- [ ] `server/Dockerfile.prod`
- [ ] `webapp/Dockerfile.prod`
- [ ] `server/.dockerignore`
- [ ] `webapp/.dockerignore`
- [ ] `.env.production`
- [ ] `docker/docker-compose.prod.yml`
- [ ] `docker/nginx.prod.conf`
- [ ] `scripts/init-letsencrypt.sh`
- [ ] `scripts/deploy.sh`
- [ ] `scripts/backup-db.sh`
- [ ] `scripts/health-check.sh`
- [ ] `scripts/rollback.sh`
- [ ] `.github/workflows/build-and-push.yml`
- [ ] `docs/DEPLOYMENT.md`

### Scripts Exécutables
```bash
chmod +x scripts/*.sh
```

- [ ] Tous les scripts `.sh` sont exécutables

---

## 🚀 Déploiement Initial

### 1. Clone sur le Serveur
```bash
cd /opt
git clone https://github.com/votre-username/monitoring-arch.git
cd monitoring-arch
```

- [ ] Repository cloné sur `/opt/monitoring-arch`

### 2. Configuration
```bash
cp .env.production.template .env.production
nano .env.production  # Remplir tous les secrets
```

- [ ] `.env.production` configuré avec vrais secrets

### 3. SSL/TLS Let's Encrypt
```bash
# Éditer l'email
nano scripts/init-letsencrypt.sh

# Exécuter
./scripts/init-letsencrypt.sh
```

- [ ] Email configuré dans `init-letsencrypt.sh`
- [ ] Certificat SSL obtenu avec succès
- [ ] Nginx démarre en HTTPS

### 4. Build et Push des Images (depuis local ou CI)
```bash
# Option A : Push automatique via GitHub Actions
git push origin main

# Option B : Build et push manuel depuis local
docker build -t votre_username/monitoring-api:latest -f server/Dockerfile.prod server/
docker build -t votre_username/monitoring-web:latest -f webapp/Dockerfile.prod webapp/

docker push votre_username/monitoring-api:latest
docker push votre_username/monitoring-web:latest
```

- [ ] Images buildées
- [ ] Images pushées sur Docker Hub
- [ ] Tags `latest` disponibles

### 5. Premier Démarrage
```bash
export VERSION=latest
export DOCKER_USERNAME=votre_username

cd docker
docker compose -f docker-compose.prod.yml up -d

# Suivre les logs
docker compose -f docker-compose.prod.yml logs -f
```

- [ ] Tous les services démarrent
- [ ] Migrations s'exécutent correctement
- [ ] API, worker, beat, web, db, redis, proxy sont "healthy"

### 6. Tests Initiaux
```bash
# Health check automatique
./scripts/health-check.sh

# Tests manuels
curl https://neonmonitor.dockl.com/api/v1/health
curl https://neonmonitor.dockl.com/_health

# Accès web
# Ouvrir https://neonmonitor.dockl.com dans le navigateur
```

- [ ] `/api/v1/health` retourne 200
- [ ] `/_health` retourne 200
- [ ] Interface web accessible
- [ ] Login fonctionnel
- [ ] HTTPS actif (cadenas vert)

---

## ✅ Post-Déploiement

### Vérifications Fonctionnelles
- [ ] Créer un compte utilisateur
- [ ] Ajouter une machine de monitoring
- [ ] Envoyer des métriques de test
- [ ] Vérifier que les alertes se déclenchent
- [ ] Tester notification email (GMX)
- [ ] Tester notification Slack (si configuré)

### Monitoring
- [ ] Backup manuel testé : `./scripts/backup-db.sh`
- [ ] Backup automatique configuré (cron quotidien)
- [ ] Espace disque surveillé : `df -h`
- [ ] Ressources CPU/RAM surveillées : `docker stats`

### Sécurité
- [ ] Firewall activé (ufw)
- [ ] Ports inutiles fermés (seuls 22, 80, 443 ouverts)
- [ ] DB et Redis **non exposés** publiquement
- [ ] Certificat SSL A/A+ rating (vérifier sur ssllabs.com)
- [ ] `.env.production` permissions 600 : `chmod 600 .env.production`

### Documentation
- [ ] Secrets sauvegardés dans gestionnaire de mots de passe
- [ ] Procédure de mise à jour testée
- [ ] Procédure de rollback testée

---

## 🔄 Workflow de Mise à Jour

### Développement Local
```bash
# Développer et tester en local
docker compose -f docker/docker-compose.yml up

# Tests
pytest server/tests/

# Commit et push
git add .
git commit -m "feat: nouvelle fonctionnalité"
git push origin main
```

### CI/CD Automatique
GitHub Actions va automatiquement :
1. Lancer les tests
2. Builder les images
3. Scanner les vulnérabilités (Trivy)
4. Pusher sur Docker Hub avec tag `latest`

### Déploiement Production
```bash
# SSH sur le serveur
ssh user@neonmonitor.dockl.com

cd /opt/monitoring-arch

# Pull du code
git pull origin main

# Déploiement automatique (1-2 minutes)
./scripts/deploy.sh

# Ou avec version spécifique
./scripts/deploy.sh v1.2.3
```

### Vérification
```bash
./scripts/health-check.sh
docker compose -f docker/docker-compose.prod.yml logs -f
```

---

## 📊 Métriques de Production

### Objectifs de Performance
- [ ] Temps de réponse API < 200ms (p95)
- [ ] Temps de réponse Web < 500ms (p95)
- [ ] Uptime > 99.5%
- [ ] Utilisation RAM < 1.2 GB
- [ ] Utilisation disque < 10 GB

### À Surveiller Quotidiennement
```bash
# Espace disque
df -h

# Ressources conteneurs
docker stats --no-stream

# Logs d'erreurs
docker compose -f docker/docker-compose.prod.yml logs --tail=100 | grep ERROR

# Backups
ls -lh backups/postgres/
```

---

## 🆘 Runbook - Incidents Fréquents

### "Service unhealthy"
```bash
# Vérifier les logs
docker compose -f docker/docker-compose.prod.yml logs service_name

# Redémarrer
docker compose -f docker/docker-compose.prod.yml restart service_name
```

### "Out of memory"
```bash
# Vérifier les ressources
docker stats

# Redémarrer les services gourmands
docker compose -f docker/docker-compose.prod.yml restart worker

# Augmenter swap si nécessaire
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### "Disk full"
```bash
# Nettoyer Docker
docker system prune -a -f

# Nettoyer vieux backups
find backups/postgres -name "*.sql.gz" -mtime +14 -delete

# Nettoyer logs système
sudo journalctl --vacuum-time=7d
```

### "SSL certificate expired"
```bash
# Forcer renouvellement
docker compose -f docker/docker-compose.prod.yml run --rm certbot renew

# Recharger Nginx
docker compose -f docker/docker-compose.prod.yml exec proxy nginx -s reload
```

### "Database locked"
```bash
# Vérifier les connexions actives
docker compose -f docker/docker-compose.prod.yml exec db \
  psql -U postgres -d monitoring -c "SELECT * FROM pg_stat_activity;"

# Tuer les connexions idle
docker compose -f docker/docker-compose.prod.yml exec db \
  psql -U postgres -d monitoring -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle';"
```

---

## 📞 Contacts & Escalation

### Niveaux de Gravité

**P0 - Critique** (Service complètement down)
- Action : Rollback immédiat
- Commande : `./scripts/rollback.sh <version_precedente>`

**P1 - Majeur** (Fonctionnalité critique cassée)
- Action : Investigation + Fix rapide ou rollback
- Temps de résolution : < 2h

**P2 - Mineur** (Performance dégradée)
- Action : Investigation + Fix dans prochaine release
- Temps de résolution : < 24h

### Logs à Collecter
```bash
# Pour investigation
docker compose -f docker/docker-compose.prod.yml logs --tail=500 > incident-logs.txt
docker stats --no-stream > incident-stats.txt
df -h > incident-disk.txt
free -h > incident-memory.txt
```

---

## 🎉 Success Metrics

Votre production est un succès si :
- ✅ Déploiements en < 2 minutes
- ✅ Zero-downtime deployments
- ✅ Backups quotidiens automatiques
- ✅ SSL A+ rating
- ✅ Monitoring fonctionnel
- ✅ Rollback en < 5 minutes si besoin
- ✅ Documentation à jour