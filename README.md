# Monitoring Server — Documentation complète

> **Version téléchargeable** — enregistrez ce fichier sous `monitoring-server-docs.md`

---

## Sommaire

- [Aperçu](#aperçu)
- [Démarrage rapide](#démarrage-rapide)
- [Fichiers d’environnement](#fichiers-denvironnement)
- [Cycle dev: rebuild / restart](#cycle-dev-rebuild--restart)
- [Jeu de données de dev (seed)](#jeu-de-données-de-dev-seed)
- [Endpoints utiles](#endpoints-utiles)
- [Tâches périodiques (Celery)](#tâches-périodiques-celery)
- [Smoke tests HTTP targets](#smoke-tests-http-targets)
- [Tests — organisation & alias Makefile](#tests--organisation--alias-makefile)
- [Couverture de code](#couverture-de-code)
- [Développement & redémarrages](#développement--redémarrages)
- [Dépannage rapide](#dépannage-rapide)
- [Notes CI (résumé)](#notes-ci-résumé)
- [Annexes — Exemples rapides](#annexes--exemples-rapides)

---

## Aperçu

**Stack :** FastAPI • SQLAlchemy 2.x • Alembic • Celery + Redis • PostgreSQL • httpx

⚠️ **Séparation des environnements**

- **`.env.docker`** : lu par Docker/CI uniquement. Les services parlent à `db:5432` via le réseau interne Compose.
- **`.env.integration.local`** : overrides côté **hôte** quand vous lancez `pytest` depuis l’hôte (les tests parlent à `localhost:5432`).
- **`.env`** : réservé à un éventuel usage host-only (app sans Docker). À éviter pour l’intégration.

---

## Démarrage rapide

```bash
# 1) Préparer les variables d'env pour Docker/CI
cp .env.example .env.docker

# 2) Lancer l'infra (API, worker, beat, Redis, Postgres)
docker compose -f docker/docker-compose.yml up --build -d
# (compose lit ../.env.docker déclaré dans docker-compose.yml)

# 3) Migrations
# L'entrypoint API applique déjà les migrations au démarrage.
# Vous pouvez forcer manuellement si besoin :
docker compose -f docker/docker-compose.yml exec api alembic upgrade head

# 4) Vérifier la santé
curl -fsS http://localhost:8000/api/v1/health
````

* **API :** [http://localhost:8000](http://localhost:8000)
* **Swagger UI :** [http://localhost:8000/docs](http://localhost:8000/docs)
* **OpenAPI :** [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

---

## Fichiers d’environnement

### `.env.docker` (source de vérité Docker/CI)

Chargé par `docker/docker-compose.yml` via `env_file: ../.env.docker`.
✨ **Ne pas** le charger côté hôte (sinon vous tenterez de joindre `db:5432` depuis l’hôte → échec DNS).

### `.env.integration.local` (overrides host pour pytest)

Utile quand vous lancez des tests **depuis l’hôte**. Exemple minimal :

```bash
# .env.integration.local
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/monitoring
```

Puis :

```bash
ENV_FILE=.env.integration.local pytest -m integration
```

> Le chargement a lieu **très tôt** (pydantic-settings) pour éviter que l’app fige une mauvaise `DATABASE_URL` lors des imports.

### `.env` (optionnel, host-only)

À garder pour un usage local "non Docker". Évitez-le dans les workflows d’intégration pour ne pas polluer `DATABASE_URL`.

**Astuce :** `Settings()` (pydantic-settings) lit `ENV_FILE` si présent, sinon `.env` par défaut.
Dans Docker, ne définissez pas `ENV_FILE` côté services pour éviter la lecture d’un `.env` de l’hôte monté par erreur.

---

## Cycle dev: rebuild / restart

Modifs Python uniquement → **pas besoin** de rebuild d’image :

```bash
# Redémarrages ciblés
docker compose -f docker/docker-compose.yml restart api worker beat

# via Makefile
make restart
```

Modifs de deps / Dockerfile / entrypoint :

```bash
# Rebuild + up
docker compose -f docker/docker-compose.yml build api worker beat
docker compose -f docker/docker-compose.yml up -d api worker beat

# via Makefile
make rebuild

# Rebuild forcé (nocache)
docker compose -f docker/docker-compose.yml build --no-cache api worker beat
docker compose -f docker/docker-compose.yml up -d api worker beat

# via Makefile
make rebuild-nocache
```

Smoke check rapide :

```bash
make health
# ou : curl -fsS http://localhost:8000/api/v1/health
```

---

## Jeu de données de dev (seed)

Le dépôt inclut une migration de seed (`0002_seed_dev_data.py`).

### Seed minimal (exemple)

Injection minimale avec `psql` dans le conteneur `db` :

```bash
# Client
docker compose -f docker/docker-compose.yml exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c \
"INSERT INTO clients (id, name) VALUES ('00000000-0000-0000-0000-000000000001','Dev') ON CONFLICT DO NOTHING;"

# Clé API
# Remplacez <YOUR_API_KEY> par une vraie clé (générée et stockée côté env/secret manager).
docker compose -f docker/docker-compose.yml exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c \
"INSERT INTO api_keys (id, client_id, key, name, is_active) VALUES \
('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','<YOUR_API_KEY>','dev', true) \
ON CONFLICT DO NOTHING;"

# Settings client
docker compose -f docker/docker-compose.yml exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c \
\"INSERT INTO client_settings (id, client_id, notification_email, heartbeat_threshold_minutes, \
consecutive_failures_threshold, alert_grouping_enabled) VALUES \
('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000001', \
'alerts@example.com', 5, 2, true) \
ON CONFLICT (client_id) DO NOTHING;\"
```

🛈 **Clé API utilisée dans les exemples :** **`<YOUR_API_KEY>`**

---

## Endpoints utiles

**Healthcheck**

```bash
curl -s http://localhost:8000/api/v1/health
# {"status":"ok"}
```

**HTTP targets — lister**

```bash
curl -s -H "X-API-Key: <YOUR_API_KEY>" \
  http://localhost:8000/api/v1/http-targets | jq .
```

**HTTP targets — créer**

```bash
curl -s -H "Content-Type: application/json" -H "X-API-Key: <YOUR_API_KEY>" \
  -X POST http://localhost:8000/api/v1/http-targets -d '{
    "name": "Httpbin 500 (via API)",
    "url": "https://httpbin.org/status/500",
    "method": "GET",
    "accepted_status_codes": null,
    "timeout_seconds": 10,
    "check_interval_seconds": 60,
    "is_active": true
  }'
# 201 -> {"id":"<uuid>"}
```

* **Conflit/idempotence** `409` : pour une URL déjà existante (même client), le serveur renvoie `409` avec `detail.existing_id`.
* **Validation** `422` : URL non-HTTP(S) → `422` avec message explicite (schéma attendu, validation Pydantic).

---

## Tâches périodiques (Celery)

* **Évaluation :** 60s
* **Heartbeat :** 120s
* **HTTP monitoring :** 300s

Déclencher manuellement une vérification HTTP d’une cible :

```bash
# Remplacez <ID> par l'UUID de la cible
docker compose -f docker/docker-compose.yml exec -T worker \
  celery -A app.workers.celery_app.celery call tasks.http_one --queue http \
  --args '["<ID>"]'
```

---

## Smoke tests HTTP targets

Un script est fourni :

```bash
chmod +x scripts/smoke_http_targets.sh
API=http://localhost:8000 KEY=<YOUR_API_KEY> ./scripts/smoke_http_targets.sh
```

Il valide notamment :

* Deux POST concurrents → un `201` et un `409` (avec `existing_id`)
* Idempotence d’un POST répété → `409`
* Validation d’URL → `422`

🛈 À lancer après que l’API réponde sur `/api/v1/health`. En CI, il est optionnel (non exécuté par défaut).

---

## Tests — organisation & alias Makefile

Les tests sont organisés en **unit**, **contract**, **integration** et **e2e**.
Par défaut, quand la stack est DOWN, `pytest -q` n’exécute que **unit** (et **contract** traité “unit-like” en SQLite).
Les dossiers **integration/e2e** sont **skippés** tant que `INTEG_STACK_UP`/`E2E_STACK_UP` ≠ `"1"`.

### Alias Makefile (ajoutés)

* **Rapides (stack down)**

  ```bash
  make test-fast
  # équiv. : pytest -q
  ```

* **Intégration**

  ```bash
  make test-integ
  # équiv. : INTEG_STACK_UP=1 pytest -q -m integration
  ```

* **E2E**

  ```bash
  make test-e2e
  # équiv. : E2E_STACK_UP=1 pytest -q -m e2e
  ```

* **Tout (unit + contract + integ + e2e)**

  ```bash
  make test-all
  # équiv. : INTEG_STACK_UP=1 E2E_STACK_UP=1 pytest -q -m "unit or contract or integration or e2e"
  ```

### Recettes utiles

* **Boucle dev rapide (stack down)**

  ```bash
  make test-fast
  ```

* **Intégration locale (stack up + DB locale)**

  ```bash
  # Démarrer la stack
  make stack-up
  # Lancer les tests d'intégration
  make test-integ
  ```

* **E2E**

  ```bash
  make stack-up
  make test-e2e
  ```

> Les tests d’intégration/E2E supposent une base seedée (voir section seed) et une API healthy.

---

## Couverture de code

Pipeline complet local (host + containers) :

```bash
# Combine host + API (+ worker) et génère coverage.xml + HTML
make cov-all
# Variante permissive (pas de gate)
make cov-loose
# Inclure e2e
E2E_STACK_UP=1 make cov-all
```

Sorties :

* `coverage.xml` (CI, Sonar, etc.)
* `htmlcov/index.html` (rapport HTML)

---

## Développement & redémarrages

* **API (FastAPI)** : rebuild/redémarrage si pas de reload auto (`make restart` ou `make rebuild`).
* **Workers (Celery)** : redémarrer worker si vous modifiez des tâches (`make restart`).
* **Migrations** : après changement de schéma (`make migrate`).

---

## Dépannage rapide

`psycopg OperationalError: Errno -2 Name or service not known`
→ Vous utilisez `@db:5432` depuis l’hôte : `db` n’existe que dans le réseau Docker.
✅ Pour les tests hôte, utilisez `@localhost:5432` via `.env.integration.local` et `ENV_FILE=.env.integration.local`.

Vérifs utiles :

```bash
# Port Postgres exposé
nc -zv localhost 5432

# API health
curl -fsS http://localhost:8000/api/v1/health
```

* `401 Unauthorized` : header `X-API-Key` manquant/incorrect (**vous devez fournir une vraie clé**, ex: `X-API-Key: <YOUR_API_KEY>`).
* `404` : vérifier la route (`/api/v1/http-targets` avec tiret).
* `422` : données invalides (Pydantic) → lire le détail JSON.
* Slack non configuré : en dev vous pouvez activer `STUB_SLACK=1` ou pointer vers `http://httpbin:80/status/204`.
* Compose ne démarre pas : vérifier `.env.docker` à la racine (copie depuis `.env.example`).

---

## Notes CI (résumé)

La CI :

* prépare `.env.docker`
* démarre la stack via `docker compose` (avec `--env-file ../.env.docker` si besoin)
* attend la DB, applique les migrations Alembic dans le conteneur `api`
* exécute : unit (host-only) → integration (cov-all) → e2e

🛈 Le script `scripts/smoke_http_targets.sh` est optionnel et non exécuté par défaut (peut être ajouté après les E2E si besoin).

---

## Annexes — Exemples rapides

**Ingestion de métriques**

```bash
curl -X POST http://localhost:8000/api/v1/ingest/metrics \
  -H 'X-API-Key: <YOUR_API_KEY>' \
  -H 'X-Ingest-Id: 11111111-1111-1111-1111-111111111111' \
  -H 'Content-Type: application/json' \
  -d '{
    "machine": {"hostname":"web-01", "os":"linux"},
    "metrics": [
      {"name":"cpu_load","type":"numeric","value":0.42,"unit":"ratio"},
      {"name":"service_nginx_ok","type":"boolean","value":true},
      {"name":"version","type":"string","value":"1.0.0"}
    ],
    "sent_at": "2025-01-01T00:00:00Z"
  }'
```

**Périodicités par défaut**

* **Évaluation :** 60s
* **Heartbeat :** 120s
* **HTTP monitoring :** 300s

---

## HTTPS en développement (local)

Le projet est conçu pour être utilisé **exclusivement en HTTPS**, y compris en environnement de développement.

Nous utilisons :

* **Nginx** comme reverse-proxy
* **mkcert** pour générer des certificats TLS locaux
* un domaine local : `monitoring.local`

## Protection des routes

python - <<'PY'
import re, pathlib

base = pathlib.Path("server/app/api/v1/endpoints")
dep_re = re.compile(r"Depends\(\s*(api_key_auth|get_current_user)\b")
route_re = re.compile(r'@router\.(get|post|put|patch|delete|head|options)\("([^"]*)"')
def_re = re.compile(r"(?:async\s+def|def)\s+([a-zA-Z0-9_]+)\(")

for path in sorted(base.glob("*.py")):
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        m = route_re.search(lines[i])
        if not m:
            i += 1
            continue
        method, route = m.group(1).upper(), m.group(2)
        prot = None
        fn = None
        j = i + 1
        while j < len(lines) and j < i + 120:
            if fn is None:
                dm = def_re.search(lines[j])
                if dm:
                    fn = dm.group(1)
PY      i = jrint(f"{method:6} /api/v1{route:40} {prot:10} {path.name}:{fn or '?'}")"

### Résultat 

GET    /api/v1                                         JWT_COOKIE alerts.py:list_alerts
GET    /api/v1/me                                      JWT_COOKIE auth.py:me
GET    /api/v1/summary                                 JWT_COOKIE dashboard.py:summary
GET    /api/v1                                         JWT_COOKIE http_targets.py:list_targets
POST   /api/v1                                         JWT_COOKIE http_targets.py:create_target
PUT    /api/v1/{target_id}                             JWT_COOKIE http_targets.py:update_target
DELETE /api/v1/{target_id}                             JWT_COOKIE http_targets.py:delete_target
GET    /api/v1                                         JWT_COOKIE incidents.py:list_incidents
POST   /api/v1/ingest/metrics                          API_KEY    ingest.py:post_metrics
GET    /api/v1                                         JWT_COOKIE machines.py:list_machines
GET    /api/v1/{machine_id}/detail                     JWT_COOKIE machines.py:get_machine_detail
GET    /api/v1                                         JWT_COOKIE metrics.py:list_metrics_root
GET    /api/v1/{machine_id}                            JWT_COOKIE metrics.py:list_metrics_by_machine
POST   /api/v1/{metric_instance_id}/thresholds/default JWT_COOKIE metrics.py:upsert_default_threshold
PATCH  /api/v1/{metric_instance_id}/alerting           JWT_COOKIE metrics.py:toggle_alerting
PATCH  /api/v1/{metric_instance_id}/pause              JWT_COOKIE metrics.py:toggle_pause_metric
GET    /api/v1                                         JWT_COOKIE notifications.py:list_notifications
GET    /api/v1                                         JWT_COOKIE settings.py:get_settings
PUT    /api/v1                                         JWT_COOKIE settings.py:update_settings


➡️ Voir la documentation complète :
📄 `docs/dev-https.md`

Bon run ! 🎯

