# ---------------------------
# Variables
# ---------------------------
SHELL := /bin/bash
API ?= http://localhost:8000
KEY ?= dev-apikey-123
PYTEST ?= pytest
COMPOSE := docker compose -f docker/docker-compose.yml
COVERAGE := python -m coverage
COV ?= 70   # seuil de couverture combinée (host + API)

# ---------------------------
# Cibles "phony"
# ---------------------------
.PHONY: test test-unit test-int test-integ test-e2e \
        lint fmt \
        stack-up stack-down migrate \
        restart rebuild rebuild-nocache \
        ps logs shell-api shell-worker health smoke-http-targets \
        cov-all cov-host cov-api-up cov-api-down cov-api-pull cov-combine cov-clean cov-html verify

# ---------------------------
# Tests
# ---------------------------

# Alias pratique : tests unitaires
test: test-unit

# Tests unitaires
test-unit:
	@$(PYTEST) -m unit -vv -ra

# Tests d'intégration (variables d'env passées à pytest)
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
stack-up:
	$(COMPOSE) up -d db redis api worker beat

# Arrêter la stack
stack-down:
	$(COMPOSE) down

# Redémarrage rapide (recharge le code Python monté)
restart:
	$(COMPOSE) restart api worker beat

# Rebuild images (si pyproject/Dockerfile/entrypoint changés)
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
	@curl -sf $(API)/api/v1/health && echo "health OK" || (echo "health FAIL" && exit 1)

smoke-http-targets:
	@API?=http://localhost:8000 KEY?=dev-apikey-123 ./scripts/smoke_http_targets.sh

# ---------------------------
# Coverage (host + API Docker [+ worker])
# ---------------------------

# Démarrage optionnel du worker sous coverage : WITH_WORKER=1 make cov-all
WITH_WORKER ?= 0
# Timeouts SQL défensifs côté host durant les tests
PGOPTIONS ?= -c lock_timeout=5s -c statement_timeout=60000

# Nettoyage des artefacts de coverage
cov-clean:
	rm -f .coverage .coverage.host coverage.xml coverage-combined.xml
	rm -rf htmlcov
	# Important : supprimer aussi les anciennes data côté conteneurs montées dans /app/server
	rm -f server/.coverage server/.coverage.api server/.coverage.worker

# Monte DB/Redis/API (et worker) SOUS COVERAGE et attend l'API
# - API_COVERAGE est lu par l'image/entrypoint : si =1, l'API démarre sous coverage et écrit /app/server/.coverage.api
# - WORKER_COVERAGE idem pour le worker, qui écrit /app/server/.coverage.worker*
# - COVERAGE_FILE permet de nommer les fichiers émis par chaque service (utile pour les distinguer)
cov-api-up:
	API_COVERAGE=1 COVERAGE_FILE=/app/server/.coverage.api $(COMPOSE) up -d db redis api
	@echo "⏳ Attente de l'API (healthcheck Docker)…"
	@for i in $$(seq 1 30); do \
		docker inspect --format='{{json .State.Health.Status}}' $$($(COMPOSE) ps -q api) 2>/dev/null | grep -q healthy && exit 0; \
		sleep 1; \
	done; \
	echo "❌ API unhealthy"; exit 1
	@if [ "$(WITH_WORKER)" = "1" ]; then \
		echo "▶️  Lancement du worker sous coverage…"; \
		WORKER_COVERAGE=1 COVERAGE_FILE=/app/server/.coverage.worker $(COMPOSE) up -d worker; \
	fi

# (Optionnel) migrations pour assurer que l'API a le schéma attendu
cov-migrate:
	$(COMPOSE) exec -T api alembic upgrade head

# Tests côté hôte (produit ./.coverage.host)
cov-host:
	INTEG_STACK_UP=1 API=$(API) KEY=$(KEY) \
	PGOPTIONS="$(PGOPTIONS)" \
	PYTEST_ADDOPTS="--timeout=120 --timeout-method=thread" \
	COVERAGE_FILE=.coverage.host COVERAGE_RCFILE=.coveragerc \
	$(PYTEST) -vv -rA -m "unit or integration" \
	  --cov=server/app --cov-branch --cov-report=term-missing \
	  --cov-fail-under=0

# Stoppe les services qui écrivent du coverage pour flusher les fichiers
cov-api-down:
	$(COMPOSE) stop api || true
	@if [ "$(WITH_WORKER)" = "1" ]; then \
		$(COMPOSE) stop worker || true; \
	fi

# Combine HOST + API (+ WORKER si présent) puis génère rapport + XML
# - On tolère l'absence de certains fichiers (selon ce qui a tourné)
# - On applique un seuil global (--fail-under)
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
	COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc \
	  $(COVERAGE) combine -q $$files; \
	COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc \
	  $(COVERAGE) report -m --fail-under=$(FAIL_UNDER); \
	COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc \
	  $(COVERAGE) xml -o coverage.xml

# Pipeline complet (local) : clean → up (API+worker) → migrate → tests host → stop → combine
cov-all: cov-clean cov-api-up cov-migrate cov-host cov-api-down cov-combine

# Full verification
verify:
	BUILD=$(BUILD) THRESHOLD=$(THRESHOLD) bash scripts/verify_all.sh