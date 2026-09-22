.DEFAULT_GOAL := help
SHELL := /bin/bash
COMPOSE := docker compose
BACKEND := $(COMPOSE) exec -T api

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
	 awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Environment -------------------------------------------------------------
.PHONY: env
env: ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "Created .env — review the secrets before deploying")

# --- Lifecycle ---------------------------------------------------------------
.PHONY: up
up: env ## Start the full local stack
	$(COMPOSE) up -d --build
	@echo "API      http://localhost:8000/docs"
	@echo "Web      http://localhost:5173"
	@echo "MinIO    http://localhost:9001"
	@echo "Mailpit  http://localhost:8025"

.PHONY: down
down: ## Stop the stack (keeps volumes)
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and DELETE all local data
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail all service logs
	$(COMPOSE) logs -f --tail=100

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

.PHONY: shell
shell: ## Shell into the api container
	$(COMPOSE) exec api /bin/bash

.PHONY: psql
psql: ## Open a psql session
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-krb} -d $${POSTGRES_DB:-krb_erp}

# --- Database ----------------------------------------------------------------
.PHONY: migrate
migrate: ## Apply all migrations
	$(BACKEND) alembic upgrade head

.PHONY: downgrade
downgrade: ## Roll back one migration
	$(BACKEND) alembic downgrade -1

.PHONY: revision
revision: ## Autogenerate a migration: make revision m="add vendors"
	$(BACKEND) alembic revision --autogenerate -m "$(m)"
	@echo "Review the generated file by hand before committing."

.PHONY: migration-check
migration-check: ## Fail if models and migrations have drifted
	$(BACKEND) alembic check

.PHONY: seed
seed: ## Load demo data (idempotent)
	$(BACKEND) python -m app.seeds

.PHONY: reset-db
reset-db: ## Drop, recreate, migrate and seed
	$(COMPOSE) exec -T db psql -U $${POSTGRES_USER:-krb} -d postgres -c "DROP DATABASE IF EXISTS $${POSTGRES_DB:-krb_erp} WITH (FORCE);"
	$(COMPOSE) exec -T db psql -U $${POSTGRES_USER:-krb} -d postgres -c "CREATE DATABASE $${POSTGRES_DB:-krb_erp};"
	$(MAKE) migrate seed

# --- Quality -----------------------------------------------------------------
.PHONY: lint
lint: ## Lint backend and frontend
	$(BACKEND) ruff check app tests
	$(BACKEND) ruff format --check app tests
	-cd web && npm run lint

.PHONY: format
format: ## Auto-format
	$(BACKEND) ruff check --fix app tests
	$(BACKEND) ruff format app tests

.PHONY: typecheck
typecheck: ## Static type checks
	$(BACKEND) mypy app
	-cd web && npx tsc --noEmit

.PHONY: arch
arch: ## Enforce module dependency rules
	$(BACKEND) lint-imports

.PHONY: test
test: ## Backend test suite
	$(BACKEND) pytest

.PHONY: test-cov
test-cov: ## Backend tests with coverage gate
	$(BACKEND) pytest --cov=app --cov-report=term-missing --cov-fail-under=80

.PHONY: test-web
test-web: ## Frontend tests
	cd web && npm run test

.PHONY: e2e
e2e: ## Playwright end-to-end suite
	cd e2e && npx playwright test

.PHONY: check
check: lint typecheck arch test ## Everything CI runs
