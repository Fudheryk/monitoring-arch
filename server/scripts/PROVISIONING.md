# PROVISIONING.md — Provisioning client via INI (DEV & PROD)

## Objectif

Créer automatiquement dans la base :

- **1 client**
- **1 admin** (email + rôle + mot de passe éventuellement généré)
- **N API keys**
- des **settings client**
- des **HTTP targets**

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

> ⚠️ Attention : selon l’environnement (DEV vs PROD), le chemin **dans le container** peut être différent.  
> En DEV on a confirmé :
>
> - script : `/app/server/scripts/provision_from_ini.py`
> - ini : `/app/server/scripts/provisioning/<client>.ini`

---

## Sécurité (anti-boulette)

Le script **refuse de s’exécuter** si tu ne mets pas explicitement :

`PROVISION_CLIENT=true`

Cela évite de créer des clients “par erreur” en prod.

⚠️ Selon le code/config, en prod il peut aussi refuser si :

- `APP_ENV=production`
- et que `ALLOW_PROD_PROVISIONING=true` n’est pas fourni

---

## Workflow recommandé (DEV → PROD)

### 1) DEV : créer/modifier le fichier INI

Sur ta machine dev :

```bash
cd /opt/monitoring-arch

nano server/scripts/provisioning/smarthack.ini
# ou créer un nouveau fichier : server/scripts/provisioning/clientX.ini
````

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

⚠️ Note : même si le fichier est sur l’hôte, le script doit être exécuté **dans le container `api`**
(car lui a SQLAlchemy + accès réseau DB via `db:5432`).

---

# Exécution en PROD (la bonne méthode)

📍 Place-toi dans le dossier docker :

```bash
cd /opt/monitoring-arch/docker
```

Lancer le provisioning :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec \
  -e PROVISION_CLIENT=true api \
  sh -lc 'python /app/server/scripts/provision_from_ini.py /app/server/scripts/provisioning/smarthack.ini'
```

### Résultats attendus

* 1er run : crée tout + génère un fichier secrets si password admin vide
* runs suivants : **“Aucun secret généré”** si tout existe déjà

---

# Exécution en DEV (Docker compose local)

📍 Place-toi dans le dossier docker :

```bash
cd /opt/monitoring-arch/docker
docker compose up -d
```

Lancer le provisioning :

```bash
docker compose exec -e PROVISION_CLIENT=true api \
  sh -lc 'python /app/server/scripts/provision_from_ini.py /app/server/scripts/provisioning/demo.ini'
```

> ✅ En DEV, on a confirmé que le script est ici :
> `/app/server/scripts/provision_from_ini.py`
> (et non `/app/server/server/scripts/...`)

---

# Où sont les “secrets générés” ?

Si le mot de passe admin est laissé vide, le script écrit un fichier du style :

`<ini>.generated.secrets.env`

Exemple (DEV ou PROD selon le container) :

`/app/server/scripts/provisioning/demo.ini.generated.secrets.env`

⚠️ Ce fichier est **dans le container**, pas sur l’hôte.

Lire le fichier (si besoin) :

```bash
docker compose exec api \
  sh -lc 'ls -l /app/server/scripts/provisioning/*.generated.secrets.env 2>/dev/null && echo "----" && cat /app/server/scripts/provisioning/*.generated.secrets.env || true'
```

Puis supprimer (recommandé) :

```bash
docker compose exec api \
  sh -lc 'rm -f /app/server/scripts/provisioning/*.generated.secrets.env'
```

📌 IMPORTANT :

* **Ne jamais commit** ces fichiers
* **Ne pas les laisser traîner** si tu veux limiter l’exposition

---

# Vérifications après provisioning (audit accès)

## Vérifier les users (admin / actifs)

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select email,role,is_active,updated_at from users order by updated_at desc limit 50;\""
```

Pour un email précis :

```bash
EMAIL="client@exemple.com"

docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select email,role,is_active,updated_at from users where email='${EMAIL}';\""
```

---

## Vérifier les API keys (créées / actives / associées à une machine)

Lister les clés :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select id,name,is_active,machine_id,last_used_at from api_keys order by name;\""
```

Lister les clés **associées à une machine** (non “état initial”) :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select id,name,is_active,machine_id,last_used_at from api_keys where machine_id is not null order by name;\""
```

---

## Vérifier les machines enregistrées (preuve d’une ingestion)

Pour un client donné, récupérer son `client_id` (ex via une clé connue) :

```bash
CLIENT_ID="$(docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -Atc \"select distinct client_id from api_keys where name like 'smarthack-key-%' limit 1;\"")"
echo "CLIENT_ID=$CLIENT_ID"
```

Lister les machines du client :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select id,hostname,fingerprint,is_active,registered_at,last_seen from machines where client_id='${CLIENT_ID}' order by registered_at desc;\""
```

Attendu “état initial” : **0 row**

---

## Vérifier les ingest_events (preuve d’ingestions)

Compter :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select count(*) as ingest_count from ingest_events where client_id='${CLIENT_ID}';\""
```

Détail :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select created_at, ingest_id, machine_id, sent_at from ingest_events where client_id='${CLIENT_ID}' order by created_at desc limit 50;\""
```

Attendu “état initial” : `ingest_count = 0`

---

# Revenir à un état initial (reset client beta)

🎯 Objectif : repartir comme un client “neuf” :

* aucune machine enregistrée
* aucune ingestion
* aucune association clé ↔ machine (`api_keys.machine_id = NULL`)
* reset `last_used_at`

⚠️ Attention : ces commandes suppriment des données runtime.

```bash
CLIENT_ID="***REDACTED_CLIENT_UUID***"

docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"
begin;

-- purge ingest
delete from ingest_events where client_id='${CLIENT_ID}';

-- purge machines (ON DELETE CASCADE => purge metric_instances/samples/alerts associés si existants)
delete from machines where client_id='${CLIENT_ID}';

-- remettre last_used_at à NULL
update api_keys set last_used_at=null where client_id='${CLIENT_ID}';

-- dissocier les clés de toute machine
update api_keys set machine_id=null where client_id='${CLIENT_ID}';

commit;
\""
```

Vérification post-reset :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"
select name,is_active,machine_id,last_used_at
from api_keys
where client_id='${CLIENT_ID}'
order by name;
\""

docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select count(*) as machines from machines where client_id='${CLIENT_ID}';\""

docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select count(*) as ingest_events from ingest_events where client_id='${CLIENT_ID}';\""
```

---

# Récupération des clés API (livrable client)

Afficher toutes les clés + statut :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select name,key,is_active from api_keys where name like 'smarthack-key-%' order by name;\""
```

Export “clé = valeur” :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -Atc \"select name || ' = ' || key from api_keys where name like 'smarthack-key-%' order by name;\""
```

> 🔒 Attention : ce genre d’export doit être fait **uniquement** pour livraison client, et **jamais loggé** dans un terminal partagé.

---

# Activer / désactiver les clés API

Activer toutes les clés Smarthack :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"update api_keys set is_active=true where name like 'smarthack-key-%';\""
```

Désactiver toutes les clés Smarthack :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"update api_keys set is_active=false where name like 'smarthack-key-%';\""
```

Vérifier :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select name,is_active from api_keys where name like 'smarthack-key-%' order by name;\""
```

---

# Reset du mot de passe admin (cas user déjà existant)

⚠️ Le provisioning est idempotent : si le user existe déjà, il **ne modifie pas le mot de passe**.

Comme `users.password_hash` est hashé, il est **impossible de récupérer un password en clair** depuis la DB.

---

## ⚠️ Cas réel rencontré : hash tronqué → login impossible

On a observé en prod un cas où :

* `users.password_hash` était **tronqué** (ex: longueur 20)
* ce qui déclenchait :
  `passlib.exc.UnknownHashError: hash could not be identified`

➡️ Cause probable : update SQL mal échappé / mauvaise interpolation shell.

### Vérifier l’intégrité du hash

```bash
EMAIL="client@exemple.com"

docker compose --env-file ../.env.production -f docker-compose.prod.yml exec -T db \
  sh -lc "psql -U postgres -d monitoring -c \"select length(password_hash) as len, left(password_hash, 4) as prefix from users where email='${EMAIL}';\""
```

Attendu :

* `len ≈ 60`
* `prefix = $2b$` (bcrypt)

---

## Méthode safe (recommandée) : reset depuis le container `api`

👉 Cette méthode utilise **le même contexte bcrypt que l’app**, donc aucun risque d’incompatibilité.

⚠️ **Correction sécurité** (objectif "0 traces") :

* ne pas imprimer un mot de passe en clair dans les logs,
* générer le secret, mais ne l’afficher que si vous êtes dans un canal sécurisé,
* idéalement : écrire le secret dans un fichier éphémère dans le container (puis le supprimer).

Exemple (ne print PAS le password) :

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec api sh -lc '
python - << "PY"
import os, secrets
from sqlalchemy import create_engine, text
from app.core.security import hash_password

EMAIL="client@exemple.com"

# Génère un password fort (ne pas l'afficher ici par défaut)
pwd = secrets.token_urlsafe(24)
h = hash_password(pwd)

db = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")
engine = create_engine(db, future=True)

with engine.begin() as c:
    c.execute(
        text("update users set password_hash=:h, updated_at=now() where email=:e"),
        {"h": h, "e": EMAIL},
    )
    row = c.execute(
        text("select length(password_hash), left(password_hash,4) from users where email=:e"),
        {"e": EMAIL},
    ).fetchone()

print("HASH_LEN=", row[0])
print("HASH_PREFIX=", row[1])

# Si vous devez récupérer le password, faites-le dans un canal sécurisé :
# print("NEW_PASSWORD=", pwd)
PY'
```

> 🔐 Recommandation : si tu dois afficher `NEW_PASSWORD`, fais-le **uniquement** en session privée / canal sécurisé.

---

## Vérifier que le password match bien en DB

```bash
docker compose --env-file ../.env.production -f docker-compose.prod.yml exec api sh -lc '
python - << "PY"
import os
from sqlalchemy import create_engine, text
from app.core.security import verify_password

EMAIL="client@exemple.com"
PWD="***REDACTED_CLEAR_PASSWORD***"

db = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")
engine = create_engine(db, future=True)

with engine.begin() as c:
    h = c.execute(text("select password_hash from users where email=:e"), {"e": EMAIL}).scalar()

print("HASH_PREFIX=", h[:4])
print("HASH_LEN=", len(h))
print("VERIFY_AGAINST_DB=", verify_password(PWD, h))
PY'
```

---

# Tester le login API (DEV / PROD)

## En local (DEV)

⚠️ `http://127.0.0.1` redirige vers HTTPS (`301`), donc il faut soit :

### Option A — appeler directement en HTTPS

```bash
curl -sk -i -X POST https://127.0.0.1/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@dockl.com","password":"***REDACTED***"}' | head -n 30
```

### Option B — suivre la redirection en conservant le body (curl)

```bash
curl -sk -i -L --post301 --post302 --post303 \
  -X POST http://127.0.0.1/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@dockl.com","password":"***REDACTED***"}' | head -n 30
```

> 💡 Sans `--post301`, `curl -L` peut perdre le body → erreur `422 Unprocessable Entity`.

---

# Tester l’ingest avec une clé API

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
| awk "NR==1 || /^\{/ {print}"
```

Attendu : `HTTP/2 202`

---

# Template “Livrable client bêta” (anonymisé)

## Livrable client bêta — <CLIENT_NAME>

### Accès interface (compte admin)

* **URL** : `https://<DOMAIN>`
* **Email** : `<ADMIN_EMAIL>`
* **Mot de passe** : `***REDACTED***`
* **Rôle** : `admin_client`

> Merci de changer le mot de passe dès la première connexion.

---

### Accès API (ingestion)

* **Endpoint** : `POST https://<DOMAIN>/api/v1/ingest/metrics`
* **Header** : `X-API-Key: <API_KEY>`

#### Clé active (à utiliser)

* `<key-name-01>` = `***REDACTED***`

#### Clés de réserve (inactives)

* `<key-name-02>` = `***REDACTED***`
* `<key-name-03>` = `***REDACTED***`
* `<key-name-04>` = `***REDACTED***`

---

### Exemple de test ingestion (curl)

```bash
API_KEY="***REDACTED***"
SENT_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

curl -sk -i -X POST "https://<DOMAIN>/api/v1/ingest/metrics" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"sent_at\":\"$SENT_AT\",\"machine\":{\"hostname\":\"client-test-01\",\"os\":\"Linux\",\"fingerprint\":\"client-test-01\"},\"metrics\":[]}" \
| awk "NR==1 || /^\{/ {print}"
```

**Attendu** : `HTTP/2 202`

> À la première ingestion, la machine est enregistrée à partir de `hostname` + `fingerprint`.



### EXECUTION 

docker exec -it monitoring-api sh -lc '
  set -e
  cd /app

  export PROVISION_CLIENT=true
  export DATABASE_URL="postgresql+psycopg://postgres:${DB_PASSWORD}@db:5432/monitoring"

  for f in server/scripts/provisioning/*.ini; do
    [ -f "$f" ] || continue
    echo "==> Provision: $f"
    python server/scripts/provision_from_ini.py "$f"
  done
'
