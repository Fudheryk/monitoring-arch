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
        ps logs shell-api shell-worker health \
        cov-all cov-host cov-api-up cov-api-down cov-api-pull cov-combine cov-clean cov-html

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

# ---------------------------
# Coverage (host + API Docker)
# ---------------------------

cov-clean:
	rm -f .coverage coverage.xml coverage-combined.xml
	rm -rf htmlcov
	# important: supprimer aussi l’ancienne data API s’il y en a
	rm -f server/.coverage

# Démarre l’API sous coverage et attend qu’elle soit healthy
cov-api-up:
	API_COVERAGE=1 $(COMPOSE) up -d db redis api
	@echo "⏳ Attente de l'API (healthcheck Docker)…"
	@for i in $$(seq 1 30); do \
		docker inspect --format='{{json .State.Health.Status}}' $$($(COMPOSE) ps -q api) 2>/dev/null | grep -q healthy && exit 0; \
		sleep 1; \
	done; \
	echo "❌ API unhealthy"; exit 1

# Tests côté hôte (produit ./.coverage)
cov-host:
	INTEG_STACK_UP=1 API=$(API) KEY=$(KEY) \
		$(PYTEST) -m "unit or integration" -vv -rA \
		--cov=server/app --cov-branch --cov-report=term-missing

# Stoppe uniquement l’API (SIGINT → coverage écrit server/.coverage)
cov-api-down:
	$(COMPOSE) stop api

# Combine HOST + API si disponibles ; sinon fallback HOST seul
cov-combine:
	@set -euo pipefail; \
	test -s .coverage || { echo "❌ .coverage hôte manquant ou vide"; exit 1; }; \
	if test -s server/.coverage; then \
	  echo "⏳ Combine coverage (host + API)…"; \
	  cp -f .coverage .coverage.host; \
	  COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc \
	    $(COVERAGE) combine -q .coverage.host server/.coverage; \
	else \
	  echo "⚠️  Pas de server/.coverage → rapport host seul"; \
	fi; \
	COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc \
	  $(COVERAGE) report -m --fail-under=70


# Pipeline complet
cov-all: cov-clean cov-api-up cov-host cov-api-down cov-combine
