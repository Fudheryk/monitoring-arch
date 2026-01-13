# =============================================================================
# 👋 Guide rapide (dev quotidien)
# -----------------------------------------------------------------------------
# Variables utiles :
#   USE_DEV_OVERRIDE=1 (défaut)  → charge docker/docker-compose.override.yml
#                                  (STUB_SLACK=1, SLACK_WEBHOOK vide).
#   WITH_COVERAGE=1              → lance la stack avec l’override coverage.
#   E2E_STACK_UP=1               → autorise les tests e2e (voir MARKS ci-dessous).
#   MARKS="unit or integration"  → filtre pytest (défaut). Ajouter "or e2e" pour inclure les e2e.
#
# Commandes les plus utilisées :
#   make stack-up      → démarre db/redis/api/worker/beat (avec override dev).
#   make restart       → redémarre api/worker/beat (après modif de code Python).
#   make logs          → suit les logs (api + worker + beat).
#   make ps            → affiche l’état des services.
#   make migrate       → applique les migrations Alembic (API).
#   make db-reset      → reset complet de la BDD (drop/create + migrations).
#   make stack-down    → stoppe la stack (garde les volumes).
#
# Tests rapides (alias demandés) :
#   make test-fast     → pytest -q (unit + contract, stack down)
#   make test-integ    → INTEG_STACK_UP=1 pytest -q -m integration
#   make test-e2e      → E2E_STACK_UP=1 pytest -q -m e2e
#   make test-all      → INTEG_STACK_UP=1 E2E_STACK_UP=1 pytest -q -m "unit or contract or integration or e2e"
#
# Quand rebuild ?
#   - Changement Dockerfile/requirements/entrypoint → make rebuild (ou rebuild-api / rebuild-worker / rebuild-beat).
#   - Changement de code Python seulement → PAS besoin de rebuild → make restart suffit.
#   - Redis/Postgres → jamais besoin de rebuild (images officielles), restart si besoin.
#
# STUB ?
#   STUB_SLACK=1 = mode simulé Slack (pas d’appel réseau). Actif par défaut via l’override dev.
#   Pour tester un vrai webhook Slack : USE_DEV_OVERRIDE=0 make stack-up
#   (ou fournis SLACK_WEBHOOK et enlève STUB_SLACK dans ton override).
#
# Astuces :
#   - make compose-config  → affiche la config compose résolue (vérifier env effectives).
#   - make env-worker      → montre les variables Slack côté worker (STUB_SLACK/SLACK_WEBHOOK).
#   - make stack-nuke      → stop + supprime volumes (BDD vierge).
#
# Couverture :
#   - En local : STRICT=0 make cov-all         (rapport sans gate bloquant)
#   - Avec e2e : E2E_STACK_UP=1 make cov-all   (inclut e2e → % plus haut)
#   - En CI : garder STRICT=1 (défaut) et un seuil COV (défaut 70)
# =============================================================================


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

# --- Compose files selection --------------------------------------------------
# USE_DEV_OVERRIDE=1 -> ajoute docker/docker-compose.override.yml (stub Slack) par défaut
# WITH_COVERAGE=1    -> ajoute docker/docker-compose.coverage.yml (couverture dans containers)
USE_DEV_OVERRIDE ?= 1
WITH_COVERAGE ?= 0

# Compose "bases"
COMPOSE_BASE := docker compose -f docker/docker-compose.yml
COMPOSE_DEV  := $(COMPOSE_BASE) -f docker/docker-compose.override.yml
# NB: on garde l'override dev aussi pendant la couverture si USE_DEV_OVERRIDE==1
COMPOSE_COV  := $(COMPOSE_BASE) -f docker/docker-compose.coverage.yml $(if $(filter 1,$(USE_DEV_OVERRIDE)),-f docker/docker-compose.override.yml,)

# Sélecteur principal : normal/dev/coverage
ifeq ($(WITH_COVERAGE),1)
  COMPOSE := $(COMPOSE_COV)
else
  ifeq ($(USE_DEV_OVERRIDE),1)
    COMPOSE := $(COMPOSE_DEV)
  else
    COMPOSE := $(COMPOSE_BASE)
  endif
endif

# Seuil de couverture combinée (utilisé par cov-combine)
COV ?= 70

# Sélecteur des marques pytest (unit / integration / e2e)
E2E_STACK_UP ?= 0
MARKS ?= unit or integration
ifeq ($(E2E_STACK_UP),1)
  MARKS := unit or integration or e2e
endif

# ---------------------------
# Cibles "phony"
# ---------------------------
.PHONY: test test-unit test-int test-integ test-e2e test-fast test-all \
        lint fmt \
        stack-up stack-up-prod stack-down stack-nuke migrate migrate-reset migrate-stamp-base \
        restart rebuild rebuild-nocache rebuild-api rebuild-worker rebuild-beat \
        ps logs shell-api shell-worker health smoke-http-targets compose-config env-worker \
        db-reset db-wipe \
        cov-all cov-clean cov-api-up cov-worker-up cov-migrate cov-host cov-api-down cov-combine cov-html \
        cov-all-e2e cov-loose \
        verify

# ---------------------------
# Tests
# ---------------------------

# Alias : lance les tests unitaires (mode verbeux)
test: test-unit

# Tests unitaires (rapides, sans Docker)
test-unit:
	@$(PYTEST) -m unit -vv -ra

# 🔹 Alias demandés
# Tests rapides locaux : stack down → n'exécute que unit (+ contract selon config des tests)
test-fast:
	@INTEG_STACK_UP=0 E2E_STACK_UP=0 $(PYTEST) -q

# Tests d'intégration (stack up côté DB/API/Redis)
# Deux alias : test-int et test-integ
test-int test-integ:
	@INTEG_STACK_UP=1 API=$(API) KEY=$(KEY) $(PYTEST) -q -m integration

# Tests E2E (stack complète requise)
test-e2e:
	@E2E_STACK_UP=1 API=$(API) KEY=$(KEY) $(PYTEST) -q -m e2e

# Tout exécuter (unit/contract/integration/e2e)
test-all:
	@INTEG_STACK_UP=1 E2E_STACK_UP=1 API=$(API) KEY=$(KEY) \
	$(PYTEST) -q -m "unit or contract or integration or e2e"

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
# ⚙️ Respecte USE_DEV_OVERRIDE et WITH_COVERAGE
stack-up:
	$(COMPOSE) up -d db redis api worker beat web proxy

# Variante prod = sans override dev (pas de stub Slack)
stack-up-prod:
	$(COMPOSE_BASE) up -d db redis api worker beat web proxy

# Arrêter la stack (sans -v pour garder les volumes en dev)
stack-down:
	$(COMPOSE) down

# Nuke total (stop + suppression volumes) → BDD vierge
stack-nuke:
	$(COMPOSE) down -v

# Redémarrage rapide (recharge les process Python)
# ⚠️ Les workers Celery et uvicorn ne hot-reloadent pas → restart nécessaire à chaque changement de code
restart:
	$(COMPOSE) restart api worker beat web proxy

# Rebuild images (si Dockerfile/requirements/entrypoint changent) puis relance
rebuild:
	$(COMPOSE) build api worker beat web proxy
	$(COMPOSE) up -d api worker beat web proxy

# Rebuild sans cache
rebuild-nocache:
	$(COMPOSE) build --no-cache api worker beat web proxy
	$(COMPOSE) up -d api worker beat web proxy

# Rebuild ciblés (utile si seule une image a changé)
rebuild-api:
	$(COMPOSE) build api
	$(COMPOSE) up -d api

rebuild-worker:
	$(COMPOSE) build worker
	$(COMPOSE) up -d worker

rebuild-beat:
	$(COMPOSE) build beat
	$(COMPOSE) up -d beat

# Appliquer les migrations Alembic dans le conteneur api
migrate:
	$(COMPOSE) exec -T api alembic upgrade head

# Reset migrations applicatives (downgrade base → upgrade head)
migrate-reset:
	$(COMPOSE) exec -T api bash -lc 'alembic downgrade base && alembic upgrade head'

# Stamp direct sur base (utile si on veut “oublier” l’historique sans toucher aux tables)
migrate-stamp-base:
	$(COMPOSE) exec -T api alembic stamp base

# ---------------------------
# Outils confort
# ---------------------------

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=200 api worker beat web proxy

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

# Affiche la config docker-compose résolue (utile pour vérifier SLACK_WEBHOOK/STUB_SLACK)
compose-config:
	$(COMPOSE) config | sed -n '/services:/,$$p'

# Vérifie les env Slack côté worker
env-worker:
	$(COMPOSE) exec -T worker env | grep -E 'SLACK_WEBHOOK|STUB_SLACK|ALERT_REMINDER_MINUTES' || true

# ---------------------------
# BDD (Postgres)
# ---------------------------

## Supprime et recrée la base 'monitoring' proprement (coupe connexions avant DROP)
db-wipe:
	$(COMPOSE) up -d db
	$(COMPOSE) exec -T db psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
	  -c "UPDATE pg_database SET datallowconn = false WHERE datname='monitoring';" \
	  -c "REVOKE CONNECT ON DATABASE monitoring FROM PUBLIC;" \
	  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='monitoring';" \
	  -c "DROP DATABASE IF EXISTS monitoring;" \
	  -c "CREATE DATABASE monitoring;"

## Reset complet: stop -> wipe -> migrations via service 'migrate' -> up services
db-reset:
	$(COMPOSE) stop api worker beat || true
	$(MAKE) db-wipe
	# migrations dans un conteneur éphémère (pas besoin que 'api' soit up)
	$(COMPOSE) run --rm migrate alembic upgrade head
	# relance des services applicatifs
	$(COMPOSE) up -d api worker beat


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
# - Force l’override coverage (COMPOSE_COV)
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
# - utilise MARKS (unit/integration[/e2e]) et propage E2E_STACK_UP
cov-host:
	@INTEG_STACK_UP=1 E2E_STACK_UP=$(E2E_STACK_UP) API="$(API)" KEY="$(KEY)" \
	PYTEST_ADDOPTS="--timeout=120 --timeout-method=thread" \
	COVERAGE_FILE=.coverage.host COVERAGE_RCFILE=.coveragerc \
	$(PYTEST) -vv -rA -m "$(MARKS)" \
	  --cov=server/app --cov-branch --cov-report=term-missing \
	  --cov-fail-under=0

# Stoppe les services qui écrivent du coverage pour flusher les fichiers
cov-api-down:
	@$(COMPOSE_COV) stop api || true
	@$(COMPOSE_COV) stop worker || true
	@$(COMPOSE_COV) stop beat || true
	@$(COMPOSE_COV) stop web || true
	@$(COMPOSE_COV) stop proxy || true

# Combine HOST + API (+ WORKER/BEAT si présents) puis génère rapport + XML.
# - Tolère l’absence de certains fragments (selon ce qui a tourné).
# Gate strict par défaut, désactivable : STRICT=0 make cov-combine
cov-combine:
	@set -euo pipefail; \
	files=""; \
	host_file="$$(find . -maxdepth 1 -type f -name '.coverage.host' -size +0c -printf ' %p' 2>/dev/null || true)"; \
	files="$$files$$host_file"; \
	api_worker_files="$$(find server -maxdepth 1 -type f -name '.coverage*' ! -name '.coveragerc' -size +0c -printf ' %p' 2>/dev/null || true)"; \
	files="$$files$$api_worker_files"; \
	if [ -z "$$files" ]; then echo "❌ Aucun fichier coverage trouvé à combiner"; exit 1; fi; \
	echo "⏳ Combine coverage: $$files"; \
	COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc $(COVERAGE) combine -q $$files; \
	if [ "$${STRICT:-1}" = "1" ]; then \
	  COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc $(COVERAGE) report -m --fail-under=$(COV); \
	else \
	  COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc $(COVERAGE) report -m || true; \
	fi; \
	COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc $(COVERAGE) xml -o coverage.xml

# Rapport HTML local (après cov-combine)
cov-html:
	@COVERAGE_FILE=.coverage COVERAGE_RCFILE=.coveragerc $(COVERAGE) html
	@echo "📂 Rapport HTML: ./htmlcov/index.html"

# Pipeline complet (local) : clean → up (API) → (worker opt.) → migrate → tests host → stop → combine
cov-all: cov-clean cov-api-up cov-worker-up cov-migrate cov-host cov-api-down cov-combine

# Raccourcis pratiques
cov-all-e2e:
	@$(MAKE) E2E_STACK_UP=1 cov-all

cov-loose:
	@$(MAKE) STRICT=0 cov-all

# Full verification (déporte sur le script existant si vous l’utilisez encore)
verify:
	@BUILD=$(BUILD) THRESHOLD=$(COV) bash scripts/verify_all.sh
