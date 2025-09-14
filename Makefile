# ---------------------------
# Variables
# ---------------------------
SHELL := /bin/bash

# Endpoint/API key pour les tests "host"
API ?= http://localhost:8000
KEY ?= dev-apikey-123

# Pytest & coverage CLIs
PYTEST ?= pytest
COVERAGE := python -m coverage

# Fichiers docker-compose
COMPOSE_BASE := docker compose -f docker/docker-compose.yml
COMPOSE_COV  := docker compose -f docker/docker-compose.yml -f docker/docker-compose.coverage.yml

# ✅ Sélecteur global : démarrer/manager la stack AVEC l'override coverage ?
#   - Utilisation ponctuelle : WITH_COVERAGE=1 make stack-up
#   - Par défaut (=0), on reste sur le compose "normal" (plus rapide en dev)
WITH_COVERAGE ?= 0
ifeq ($(WITH_COVERAGE),1)
  COMPOSE := $(COMPOSE_COV)
else
  COMPOSE := $(COMPOSE_BASE)
endif

# Seuil de couverture combinée (utilisé par cov-combine)
COV ?= 70

# ---------------------------
# Cibles "phony"
# ---------------------------
.PHONY: test test-unit test-int test-integ test-e2e \
        lint fmt \
        stack-up stack-down migrate \
        restart rebuild rebuild-nocache \
        ps logs shell-api shell-worker health smoke-http-targets \
        cov-all cov-clean cov-api-up cov-migrate cov-host cov-api-down cov-combine cov-html \
        verify

# ---------------------------
# Tests
# ---------------------------

# Alias : lance les tests unitaires
test: test-unit

# Tests unitaires (rapides, sans Docker)
test-unit:
	@$(PYTEST) -m unit -vv -ra

# Tests d'intégration (env passées à pytest)
# Deux alias : test-int et test-integ
test-int test-integ:
	@INTEG_STACK_UP=1 API=$(API) KEY=$(KEY) $(PYTEST) -m integration -vv -ra

# Tests E2E (stack complète requise)
test-e2e:
	@E2E_STACK_UP=1 API=$(API) KEY=$(KEY) $(PYTEST) -m e2e -vv -ra

# ---------------------------
# Qualité / formatage
# ---------------------------

# Lint en lecture seule
lint:
	@ruff check .
	@black --check .

# Formatage auto (ruff + black)
fmt:
	@ruff check --fix .
	@black .

# ---------------------------
# Stack Docker
# ---------------------------

# Démarrer la stack (db, redis, api, worker, beat)
# ⚙️ Respecte WITH_COVERAGE : ajoute/désactive l’override coverage.
stack-up:
	$(COMPOSE) up -d db redis api worker beat

# Arrêter la stack (sans -v pour garder les volumes en dev)
stack-down:
	$(COMPOSE) down

# Redémarrage rapide (recharge le code Python monté)
restart:
	$(COMPOSE) restart api worker beat

# Rebuild images (si Dockerfile/entrypoint changent)
rebuild:
	$(COMPOSE) build api worker beat
	$(COMPOSE) up -d api worker beat

# Rebuild sans cache
rebuild-nocache:
	$(COMPOSE) build --no-cache api worker beat
	$(COMPOSE) up -d api worker beat

# Appliquer les migrations Alembic dans le conteneur api
migrate:
	$(COMPOSE) exec -T api alembic upgrade head

# Outils confort
ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=200 api worker beat

shell-api:
	$(COMPOSE) exec api bash

shell-worker:
	$(COMPOSE) exec worker bash

# Health check rapide (endpoint public)
health:
	@curl -sf "$(API)/api/v1/health" && echo "health OK" || (echo "health FAIL" && exit 1)

# Petit smoke-test HTTP targets (utilise API/KEY courants)
smoke-http-targets:
	@API="$(API)" KEY="$(KEY)" ./scripts/smoke_http_targets.sh

# ---------------------------
# Coverage (host + containers)
# ---------------------------

# Nettoyage des artefacts de coverage
cov-clean:
	@rm -f .coverage .coverage.host coverage.xml coverage-combined.xml || true
	@rm -rf htmlcov || true
	# NB: supprimer aussi les data écrites depuis les conteneurs (montées dans ./server)
	@rm -f server/.coverage server/.coverage.api server/.coverage.worker server/.coverage.beat || true

# Monte DB/Redis/API SOUS COVERAGE et attend l'API healthy.
# - Force l’override coverage, indépendamment de WITH_COVERAGE (pour un pipeline reproductible).
cov-api-up:
	@echo "▶️  Bringing up stack WITH coverage override (db, redis, api)…"
	@API_COVERAGE=1 COVERAGE_FILE=/app/server/.coverage.api $(COMPOSE_COV) up -d db redis api
	@echo "⏳ Waiting for API health (Docker healthcheck)…"
	@for i in $$(seq 1 30); do \
	  cid="$$( $(COMPOSE_COV) ps -q api )"; \
	  if [ -n "$$cid" ] && docker inspect --format='{{json .State.Health.Status}}' "$$cid" 2>/dev/null | grep -q healthy; then \
	    echo "✅ API is healthy"; \
	    exit 0; \
	  fi; \
	  sleep 1; \
	done; \
	echo "❌ API unhealthy"; exit 1

# (Optionnel) lancer le worker sous coverage (utile si vos tests déclenchent des tâches async)
cov-worker-up:
	@echo "▶️  Starting worker WITH coverage override…"
	@WORKER_COVERAGE=1 COVERAGE_FILE=/app/server/.coverage.worker $(COMPOSE_COV) up -d worker

# (Optionnel) migrations pour assurer que l'API a le schéma attendu
cov-migrate:
	$(COMPOSE_COV) exec -T api alembic upgrade head

# Tests côté hôte (produit ./.coverage.host)
# - combine unicast: unit + integration (modulable via -m)
cov-host:
	@INTEG_STACK_UP=1 API="$(API)" KEY="$(KEY)" \
	PYTEST_ADDOPTS="--timeout=120 --timeout-method=thread" \
	COVERAGE_FILE=.coverage.host COVERAGE_RCFILE=.coveragerc \
	$(PYTEST) -vv -rA -m "unit or integration" \
	  --cov=server/app --cov-branch --cov-report=term-missing \
	  --cov-fail-under=0

# Stoppe les services qui écrivent du coverage pour flusher les fichiers
cov-api-down:
	@$(COMPOSE_COV) stop api || true
	@$(COMPOSE_COV) stop worker || true
	@$(COMPOSE_COV) stop beat || true

# Combine HOST + API (+ WORKER/BEAT si présents) puis génère rapport + XML.
# - Tolère l’absence de certains fragments (selon ce qui a tourné).
cov-combine:
	@set -euo pipefail; \
	files=""; \
	host_file="$$(find . -maxdepth 1 -type f -name '.coverage.host' -size +0c -printf ' %p' 2>/dev/null || true)"; \
	files="$$files$$host_file"; \
	api_worker_files="$$(find server -maxdepth 1 -type f -name '.coverage*' ! -name '.coveragerc' -size +0c -printf ' %p' 2>/dev/null || true)"; \
	files="$$files$$api_worker_files"; \
	if [ -z "$$files" ]; then \
	  echo "❌ Aucun fichier coverage trouvé à combiner"; exit 1; \
	fi; \
	echo "⏳ Combine coverage: $$files"; \
	COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc $(COVERAGE) combine -q $$files; \
	COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc $(COVERAGE) report -m --fail-under=$(COV); \
	COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc $(COVERAGE) xml -o coverage.xml

# Rapport HTML local (après cov-combine)
cov-html:
	@COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc $(COVERAGE) html
	@echo "📂 Rapport HTML: ./htmlcov/index.html"

# Pipeline complet (local) : clean → up (API) → (worker opt.) → migrate → tests host → stop → combine
# - Activer le worker si nécessaire : make cov-all cov-worker-up=1   (ou directement `make cov-worker-up` avant)
cov-all: cov-clean cov-api-up cov-migrate cov-host cov-api-down cov-combine

# Full verification (déporte sur le script existant si vous l’utilisez encore)
verify:
	@BUILD=$(BUILD) THRESHOLD=$(COV) bash scripts/verify_all.sh
