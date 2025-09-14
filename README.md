# Monitoring Server — Documentation complète

> **Version téléchargeable** - Sauvegardez ce fichier sous `monitoring-server-docs.md`

# Monitoring Server — Implémentation prête à l'emploi (v2)

**Stack :** FastAPI • SQLAlchemy 2.x • Alembic • Celery + Redis • PostgreSQL • httpx

⚠️ **Séparation des environnements**

- **`.env.docker`** : lu par Docker/CI uniquement. Les services parlent à `db:5432` via le réseau interne Compose.
- **`.env.integration.local`** : overrides côté hôte quand vous lancez `pytest` depuis l'hôte (les tests parlent à `localhost:5432`).
- **`.env`** : réservé à un éventuel usage host-only (app sans Docker). À éviter pour l'intégration.

---

# Démarrage rapide

```bash
# 1) Préparer les variables d'env pour Docker/CI
cp .env.example .env.docker

# 2) Lancer l'infra (API, worker, beat, Redis, Postgres)
cd docker
docker compose up --build -d
# (compose lit ../.env.docker déclaré dans docker-compose.yml)

# 3) Migrations
# L'entrypoint API applique déjà les migrations au démarrage.
# Vous pouvez forcer manuellement si besoin :
docker compose exec api alembic upgrade head

# 4) Vérifier la santé
curl -fsS http://localhost:8000/api/v1/health
```

- **API :** http://localhost:8000
- **Swagger UI :** http://localhost:8000/docs
- **OpenAPI :** http://localhost:8000/openapi.json

---

# Fichiers d'environnement — qui fait quoi (important)

- **`.env.docker`** (source de vérité Docker/CI)
Chargé par `docker/docker-compose.yml` via `env_file: ../.env.docker`.
✨ Ne pas le charger côté hôte (sinon vous tenterez de joindre `db:5432` depuis l'hôte → échec DNS).

- **`.env.integration.local`** (host overrides pour pytest)
Utile quand vous lancez des tests depuis l'hôte. Exemple minimal :

```bash
# .env.integration.local
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/monitoring
```

Puis :

```bash
ENV_FILE=.env.integration.local pytest -m integration
```

Le conftest d'intégration détecte `ENV_FILE` très tôt pour éviter que l'app fige une mauvaise `DATABASE_URL` au moment de l'import.

- **`.env`** (optionnel, host-only)
À garder pour un usage local "non Docker". Évitez-le dans les workflows d'intégration pour ne pas polluer `DATABASE_URL`.

**Astuce :** dans le code, `Settings()` (pydantic-settings) lit `ENV_FILE` si présent, sinon `.env` par défaut.
Dans Docker, ne définissez pas `ENV_FILE` côté services pour éviter toute lecture d'un `.env` de l'hôte monté par erreur.

---

# Quick rebuild / restart

Modifs Python uniquement → pas besoin de rebuild d'image :

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

# Jeu de données de dev (seed rapide)

Le dépôt inclut une migration de seed (`0002_seed_dev_data.py`).
Injection minimale avec `psql` dans le conteneur `db` :

```bash
# Client
docker compose exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c
"INSERT INTO clients (id, name) VALUES ('00000000-0000-0000-0000-000000000001','Dev') ON CONFLICT DO NOTHING;"

# Clé API
docker compose exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c
"INSERT INTO api_keys (id, client_id, key, name, is_active) VALUES
('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','dev-apikey-123','dev', true)
ON CONFLICT DO NOTHING;"

# Settings client
docker compose exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c
"INSERT INTO client_settings (id, client_id, notification_email, heartbeat_threshold_minutes,
consecutive_failures_threshold, alert_grouping_enabled) VALUES
('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000001',
'alerts@example.com', 5, 2, true)
ON CONFLICT (client_id) DO NOTHING;"
```

Clé API de dev utilisée dans les exemples : `dev-apikey-123`

---

# Endpoints utiles

**Healthcheck**

```bash
curl -s http://localhost:8000/api/v1/health
# {"status":"ok"}
```

**HTTP targets – lister**

```bash
curl -s -H "X-API-Key: dev-apikey-123" \
  http://localhost:8000/api/v1/http-targets | jq .
```

**HTTP targets – créer**

```bash
curl -s -H "Content-Type: application/json" -H "X-API-Key: dev-apikey-123" \
  -X POST http://localhost:8000/api/v1/http-targets -d '{
    "name": "Httpbin 500 (via API)",
    "url": "https://httpbin.org/status/500",
    "method": "GET",
    "expected_status_code": 200,
    "timeout_seconds": 10,
    "check_interval_seconds": 60,
    "is_active": true
  }'
# 201 -> {"id":"<uuid>"}
```

**Conflit/idempotence** `409`
Pour une URL déjà existante pour le même client, le serveur renvoie un `409` avec `detail.existing_id`.

**Validation** `422`
URL non-HTTP(S) → `422` avec message explicite (schéma attendu, validation Pydantic).

---

# Tâches périodiques (Celery)

- **Évaluation :** 60s
- **Heartbeat :** 120s
- **HTTP monitoring :** 300s

Déclencher manuellement une vérification HTTP d'une cible :

```bash
# Remplacez <ID> par l'UUID de la cible
docker compose exec -T worker \
  celery -A app.workers.celery_app.celery call tasks.http_one --queue http \
  --args '["<ID>"]'
```

---

# "Smoke tests" HTTP targets

Un script est fourni :

```bash
chmod +x scripts/smoke_http_targets.sh
API=http://localhost:8000 KEY=dev-apikey-123 ./scripts/smoke_http_targets.sh
```

Il valide notamment :

- Deux POST concurrents → un `201` et un `409` (avec `existing_id`)
- Idempotence d'un POST répété → `409`
- Validation d'URL → `422`

🛈 À lancer après que l'API réponde sur `/api/v1/health`. En CI, il est optionnel (non exécuté par défaut).

---

# Tests — nouvelle organisation

- **Unit** (sans Docker)
```bash
pytest -m unit -q --maxfail=1
```

- **Integration** (host → API dockerisée)
```bash
# 1) s'assurer que la stack tourne (voir "Démarrage rapide")
# 2) forcer la DATABASE_URL "host"
echo 'DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/monitoring' > .env.integration.local

# 3) lancer les tests d'intégration
ENV_FILE=.env.integration.local \
API=http://localhost:8000 \
KEY=dev-apikey-123 \
pytest -m integration -q --maxfail=1
```

- **E2E** (stack démarrée par le script)
```bash
chmod +x scripts/test_e2e.sh
./scripts/test_e2e.sh
```

- **Vérification globale locale** (tout-en-un)
```bash
chmod +x scripts/verify_all.sh
./scripts/verify_all.sh
# => enchaîne unit → integration (cov-all) → e2e
```

- **Couverture agrégée** (integration)
```bash
# Combine host + API + worker et génère coverage.xml
WITH_WORKER=1 make cov-all
```

---

# Développement & redémarrages

**API (FastAPI) :** rebuild/redémarrage si pas de reload auto.

```bash
docker compose up -d --build api
```

**Workers (Celery) :** redémarrer worker si vous modifiez des tâches.

```bash
docker compose restart worker
```

**Migrations :** après changement de schéma.

```bash
docker compose exec api alembic upgrade head
```

---

# Dépannage rapide

`psycopg OperationalError: Errno -2 Name or service not known`
→ Vous utilisez `@db:5432` depuis l'hôte : `db` n'existe que dans le réseau Docker.
✅ Pour les tests hôte, utilisez `@localhost:5432` via `.env.integration.local` et `ENV_FILE=.env.integration.local`.

Vérifs utiles :

```bash
# Port Postgres exposé
nc -zv localhost 5432

# API health
curl -fsS http://localhost:8000/api/v1/health
```

- `401 Unauthorized` : header `X-API-Key` manquant/incorrect (utiliser `dev-apikey-123` si seedé).
- `404` : vérifier la route (`/api/v1/http-targets` avec tiret).
- `422` : données invalides (Pydantic) → lire le détail JSON.
- Slack non configuré : en dev vous pouvez activer `STUB_SLACK=1` ou pointer vers `http://httpbin:80/status/204`.
- Compose ne démarre pas : vérifier `.env.docker` à la racine (copie depuis `.env.example`).

---

# Notes CI (résumé)

La CI :

- prépare `.env.docker`
- démarre la stack via `docker compose --env-file ../.env.docker up -d --build`
- attend la DB, applique les migrations Alembic dans le conteneur `api`
- exécute : unit (host-only) → integration (cov-all) → e2e

🛈 Le script `scripts/smoke_http_targets.sh` est optionnel et non exécuté par défaut en CI (peut être ajouté après les E2E si besoin).

---

# Annexes — Exemples rapides

**Ingestion de métriques** (exemple)

```bash
curl -X POST http://localhost:8000/api/v1/ingest/metrics \
  -H 'X-API-Key: dev-apikey-123' \
  -H 'X-Ingest-Id: 11111111-1111-1111-1111-111111111111' \
  -H 'Content-Type: application/json' \
  -d '{
    "machine": {"hostname":"web-01", "os":"linux"},
    "metrics": [
      {"name":"cpu_load","type":"numeric","value":0.42,"unit":"ratio"},
      {"name":"service_nginx_ok","type":"bool","value":true},
      {"name":"version","type":"string","value":"1.0.0"}
    ],
    "sent_at": null
  }'
```

**Périodicités par défaut**
- **Évaluation :** 60s
- **Heartbeat :** 120s
- **HTTP monitoring :** 300s

Bon run ! 🎯