# Monitoring Server — Implémentation prête à l'emploi (v2)

**Stack**: FastAPI, SQLAlchemy 2.x, Alembic, Celery + Redis, PostgreSQL, httpx

## Démarrage rapide
```bash
# 1) Variables d'env locales
cp .env.example .env

# 2) Lancer l’infra
cd docker
docker compose up --build -d

# 3) Appliquer les migrations
docker compose exec api alembic upgrade head
```

Par défaut l’API écoute sur http://localhost:8000
Swagger UI : http://localhost:8000/docs — OpenAPI : /openapi.json

## Quick Rebuild / Restart

### Modifs de code Python seulement → pas de rebuild, juste restart

docker compose -f docker/docker-compose.yml restart api worker beat

### Modifs de deps / pyproject.toml, Dockerfile, entrypoint, etc. → rebuild ciblé

docker compose -f docker/docker-compose.yml build api worker beat
docker compose -f docker/docker-compose.yml up -d api worker beat

(forcé si besoin)

docker compose -f docker/docker-compose.yml build --no-cache api worker beat
docker compose -f docker/docker-compose.yml up -d api worker beat

### Smoke check

make health


## Jeu de données de dév (si besoin)

Le dépôt contient une migration de seed (0002_seed_dev_data.py).
Si vous avez besoin de (ré)injecter rapidement un client + clé API à la main :

```bash
# Dans le conteneur Postgres
docker compose exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c \
"INSERT INTO clients (id, name) VALUES ('00000000-0000-0000-0000-000000000001','Dev') ON CONFLICT DO NOTHING;"

docker compose exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c \
"INSERT INTO api_keys (id, client_id, key, name, is_active)
 VALUES ('00000000-0000-0000-0000-000000000002',
         '00000000-0000-0000-0000-000000000001',
         'dev-apikey-123','dev', true)
 ON CONFLICT DO NOTHING;"

docker compose exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c \
"INSERT INTO client_settings (id, client_id, notification_email, heartbeat_threshold_minutes,
                              consecutive_failures_threshold, alert_grouping_enabled)
 VALUES ('00000000-0000-0000-0000-000000000003',
         '00000000-0000-0000-0000-000000000001',
         'alerts@example.com', 5, 2, true)
 ON CONFLICT (client_id) DO NOTHING;"
 ```

Clé API de dev utilisée dans les exemples : dev-apikey-123

## Endpoints utiles

### Healthcheck

```bash
curl -s http://localhost:8000/api/v1/health
# -> {"status":"ok"}
```

### HTTP targets

- Lister (GET /api/v1/http-targets)

```bash
curl -s -H "X-API-Key: dev-apikey-123" http://localhost:8000/api/v1/http-targets | jq .
```

- Créer (POST /api/v1/http-targets)

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

- Conflit (idempotence) — 409

Si la même URL existe déjà *pour le même client*, le serveur renvoie :

```json
{
  "detail": {
    "message": "An HTTP target with this URL already exists for this client.",
    "existing_id": "<uuid de la cible déjà existante>"
  }
}
```

- Validation (422)

URL non-HTTP(S) :

```bash
curl -s -H "Content-Type: application/json" -H "X-API-Key: dev-apikey-123" \
  -X POST http://localhost:8000/api/v1/http-targets -d '{
    "name":"Bad URL","url":"ftp://example.com"
  }'
# -> 422 avec un détail "URL scheme should be 'http' or 'https'"
```

### Tâches périodiques (Celery)
- Évaluation: toutes les 60s
- Heartbeat: toutes les 120s
- HTTP monitoring: toutes les 300s
Vous pouvez déclencher manuellement une vérification HTTP d’une cible :

```bash
# Remplacez <ID> par l'id de la cible
docker compose exec -T worker \
  celery -A app.workers.celery_app.celery call tasks.http_one --queue http \
  --args '["<ID>"]'
```

### “Smoke tests” HTTP targets

Un script shell est fourni pour valider rapidement la route /http-targets :

```bash
chmod +x scripts/smoke_http_targets.sh
API=http://localhost:8000 KEY=dev-apikey-123 ./scripts/smoke_http_targets.sh
```

Ce script vérifie notamment :
- Deux POST concurrents → un 201 et un 409 (avec existing_id)
- Idempotence d’un POST répété → 409
- Validation d’URL → 422

### Tests d’intégration depuis l’hôte
Les tests d’intégration HTTP (via requests) ciblent l’API qui tourne dans Docker.

```bash
# 1) Créez un venv local et installez les dépendances de test minimales
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip pytest requests

# 2) Exécutez les tests en pointant vers l’API dockerisée
API=http://localhost:8000 KEY=dev-apikey-123 \
pytest -q server/tests/test_http_targets_integration.py
```

Astuce : pour lancer aussi le test de santé :
API=http://localhost:8000 pytest -q server/tests/test_health.py server/tests/test_http_targets_integration.py

### Développement & redémarrages

- Changements côté API (FastAPI) : reconstruire/redémarrer api si l’image ne fait pas de reload automatique.

```bash
docker compose up -d --build api
```

- Changements côté workers (Celery) : redémarrer worker si vous modifiez du code de tâches.

```bash
docker compose restart worker
```

- Migrations : après modification du schéma, exécuter alembic upgrade head.

### Dépannage rapide
- 401 Unauthorized : X-API-Key manquant ou invalide.
- 404 : vérifiez la route (/api/v1/http-targets, avec tiret).
- 422 : erreur de validation Pydantic (ex.: schéma d’URL).
- 500 pendant POST /http-targets :
  - Assurez-vous que les migrations sont exécutées (alembic upgrade head).
  - Inspectez docker compose logs api pour voir la stacktrace.
  - Si vous venez de modifier le code, (re)build l’image api pour embarquer la version avec gestion du 409 (dupliqués sur (client_id, url)).


















# Monitoring Server — Implémentation prête à l'emploi (v2)

**Stack**: FastAPI, SQLAlchemy 2.x, Alembic, Celery+Redis, PostgreSQL, httpx.

## Démarrage rapide
```bash
cp .env.example .env
cd docker
docker compose up --build -d
docker compose exec api alembic upgrade head
```

### Seed rapide (psql dans le conteneur db)
```bash
docker compose exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c "INSERT INTO clients (id, name) VALUES ('00000000-0000-0000-0000-000000000001','Acme');"
docker compose exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c "INSERT INTO api_keys (id, client_id, key, name, is_active) VALUES ('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','dev-key-123','dev', true);"
docker compose exec -e PGPASSWORD=postgres db psql -U postgres -d monitoring -c "INSERT INTO client_settings (id, client_id, notification_email, heartbeat_threshold_minutes, consecutive_failures_threshold, alert_grouping_enabled) VALUES ('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000001','alerts@example.com',5,2,true) ON CONFLICT (client_id) DO NOTHING;"
```

### Test d'ingestion
```bash
curl -X POST http://localhost:8000/api/v1/ingest/metrics       -H 'X-API-Key: dev-key-123'       -H 'X-Ingest-Id: 11111111-1111-1111-1111-111111111111'       -H 'Content-Type: application/json'       -d '{
    "machine": {"hostname":"web-01", "os":"linux"},
    "metrics": [
      {"name":"cpu_load","type":"numeric","value":0.42,"unit":"ratio"},
      {"name":"service_nginx_ok","type":"bool","value":true},
      {"name":"version","type":"string","value":"1.0.0"}
    ],
    "sent_at": null
  }'
```

## Services périodiques
- Évaluation: 60s
- Heartbeat: 120s
- HTTP monitoring: 300s
