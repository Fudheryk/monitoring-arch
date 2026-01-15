# README — Provisioning client via INI (DEV & PROD)

## Objectif

Créer automatiquement dans la base :

* 1 client
* 1 admin (email + rôle + mot de passe éventuellement généré)
* N API keys
* des settings client
* des HTTP targets

Le provisioning est **idempotent** :
➡️ relancer le script avec le même INI **ne duplique pas** les données existantes.

---

## Fichiers importants

### INI (1 fichier par client)

📌 À versionner dans Git :

`server/scripts/provisioning/<client>.ini`

Exemple :
`server/scripts/provisioning/smarthack.ini`

### Script

`server/scripts/provision_from_ini.py`

---

## Sécurité (anti-boulette)

Le script **refuse de s’exécuter** si tu ne mets pas explicitement :

`PROVISION_CLIENT=true`

Cela évite de créer des clients “par erreur” en prod.

---

## Workflow recommandé (DEV → PROD)

### 1) DEV : créer/modifier le fichier INI

Sur ta machine dev :

```bash
cd /opt/monitoring-arch

nano server/scripts/provisioning/smarthack.ini
# ou créer un nouveau fichier : server/scripts/provisioning/clientX.ini
```

Puis commit/push :

```bash
git add server/scripts/provisioning/smarthack.ini
git commit -m "chore(provisioning): add/update Smarthack client ini"
git push
```

---

### 2) PROD : récupérer la dernière version (pull)

Sur la prod :

```bash
cd /opt/monitoring-arch
git pull
```

Ensuite tu dois retrouver le INI ici **sur l’hôte** :

```bash
ls -l server/scripts/provisioning/
```

⚠️ Note : même si le fichier est sur l’hôte, le script doit être exécuté **dans le container `api`** (car lui a SQLAlchemy + accès réseau DB via `db:5432`).

---

## Exécution en PROD (la bonne méthode)

📍 Place-toi dans le dossier docker :

```bash
cd /opt/monitoring-arch/docker
```

Lancer le provisioning :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec \
  -e PROVISION_CLIENT=true api \
  sh -lc 'python /app/server/server/scripts/provision_from_ini.py /app/server/server/scripts/provisioning/smarthack.ini'
```

### Résultats attendus

* 1er run : crée tout + génère un fichier secrets si password admin vide
* runs suivants : **“Aucun secret généré”** si tout existe déjà

---

## Où sont les “secrets générés” ?

Si le mot de passe admin est laissé vide, le script écrit un fichier du style :

`/tmp/<client>.ini.generated.secrets.env`

⚠️ Ce fichier est **dans le container**, pas sur l’hôte.

Lire le fichier (si besoin) :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec api \
  sh -lc 'ls -l /tmp/*.generated.secrets.env && echo "----" && cat /tmp/*.generated.secrets.env'
```

Puis supprimer (recommandé) :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec api \
  sh -lc 'rm -f /tmp/*.generated.secrets.env'
```

📌 IMPORTANT :

* **Ne jamais commit** ces fichiers
* **Ne pas les laisser traîner** si tu veux limiter l’exposition

---

## Vérifications après provisioning

### Vérifier les API keys créées

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select id,name,is_active,last_used_at from api_keys order by name;\""
```

### Tester l’ingest avec une clé DB

Récupérer une clé en une ligne :

```bash
API_KEY="$(docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -Atc \"select key from api_keys where name='smarthack-key-01';\"")"
```

Test :

```bash
SENT_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

curl -sk -i -X POST https://neonmonitor.dockl.com/api/v1/ingest/metrics \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"sent_at\":\"$SENT_AT\",\"machine\":{\"hostname\":\"prod-test-01\",\"os\":\"Linux\",\"fingerprint\":\"prod-test-01\"},\"metrics\":[]}" \
| awk 'NR==1 || /^\{/ {print}'
```

Attendu : `HTTP/2 202`

