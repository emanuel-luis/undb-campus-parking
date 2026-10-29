SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

.PHONY: help install lint format test test-fast migrate up-db down test-integration openapi \
	openapi-check warehouse warehouse-full d2-install diagrams diagrams-check diagrams-watch backend-lint backend-test \
	generator-install generator-lint generator-test gen-generator-models generator-models-check

help: ## list the targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# Diagrams: docs/diagrams/<name>.d2 rendered to <name>.svg (committed). One pinned D2 everywhere.
# Layout engine, themes and padding live in docs/diagrams/_style.d2 (d2-config), so d2 runs in a
# clean environment: D2_* and SCALE variables would override that config. --scale 1 is not a
# d2-config key; it writes the natural width and height, so GitHub never upscales a small diagram.
D2_VERSION := 0.9.0
# sha256 of d2-v$(D2_VERSION)-linux-amd64.tar.gz, from the release's SHA256SUMS
D2_SHA256 := 5669ddc46b99e942cc96078f4a4e36d5e62103348f4c05179ede27802fdd87a9
D2_HOME ?= $(HOME)/.local/share/d2/v$(D2_VERSION)
D2 ?= $(D2_HOME)/bin/d2
D2_RUN = env -i PATH="$$PATH" HOME="$$HOME" "$(D2)" --scale 1
DIAGRAMS := $(filter-out docs/diagrams/_%,$(wildcard docs/diagrams/*.d2))

d2-install: ## install the pinned D2 (Linux x86_64) into D2_HOME, checksum verified
	@if [ -x "$(D2_HOME)/bin/d2" ]; then echo "D2 v$(D2_VERSION) already in $(D2_HOME)"; exit 0; fi; \
	[ "$$(uname -sm)" = "Linux x86_64" ] || { echo "d2-install covers Linux x86_64 only;" \
		"install D2 v$(D2_VERSION) yourself and pass D2=/path/to/d2" >&2; exit 1; }; \
	tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
	curl -fsSLo "$$tmp/d2.tar.gz" \
		"https://github.com/terrastruct/d2/releases/download/v$(D2_VERSION)/d2-v$(D2_VERSION)-linux-amd64.tar.gz"; \
	echo "$(D2_SHA256)  $$tmp/d2.tar.gz" | sha256sum --check --strict; \
	tar -xzf "$$tmp/d2.tar.gz" -C "$$tmp"; \
	mkdir -p "$(D2_HOME)/bin"; \
	install -m 0755 "$$tmp/d2-v$(D2_VERSION)/bin/d2" "$(D2_HOME)/bin/d2"; \
	"$(D2_HOME)/bin/d2" --version

diagrams: ## render docs/diagrams/*.d2 to SVG with the pinned D2 (run make d2-install once)
	@v="$$($(D2_RUN) --version 2>/dev/null || true)"; [ "$$v" = "v$(D2_VERSION)" ] || { \
		echo "need D2 v$(D2_VERSION) at $(D2), found '$${v:-nothing}': run make d2-install" >&2; exit 1; }
	@for f in $(DIAGRAMS); do $(D2_RUN) "$$f" "$${f%.d2}.svg"; done

diagrams-check: diagrams ## render, then fail if a committed SVG is stale, missing or has no source
	@shopt -s nullglob; for s in docs/diagrams/*.svg; do \
		[ -f "$${s%.svg}.d2" ] || { echo "$$s has no .d2 source" >&2; exit 1; }; done
	@changed="$$(git status --porcelain --untracked-files=all -- 'docs/diagrams/*.svg')"; \
	if [ -n "$$changed" ]; then echo "$$changed" >&2; \
		echo "rendered SVGs differ from the committed ones: run make diagrams and commit" >&2; exit 1; fi

diagrams-watch: ## live preview in the browser while editing: make diagrams-watch NAME=architecture
	@# NAME only from the command line: WSL exports NAME (the hostname) into every shell.
	@[ "$(origin NAME)" = "command line" ] && [ -f "docs/diagrams/$(NAME).d2" ] || { \
		echo "usage: make diagrams-watch NAME=<one of: $(basename $(notdir $(DIAGRAMS)))>" >&2; exit 1; }
	$(D2_RUN) --watch --browser "$${BROWSER:-0}" docs/diagrams/$(NAME).d2 docs/diagrams/$(NAME).svg

# Backend (uv project in backend/) and generator (uv project in generator/). `lint` and `test` run
# both; CI runs the backend-* and generator-* targets as separate jobs.
BACKEND := uv --project backend
GENERATOR := uv --project generator

install: ## install the backend (with the dbt group) and generator environments from their uv.lock files
	$(BACKEND) sync --locked --group dbt
	$(GENERATOR) sync --locked

lint: backend-lint generator-lint ## everything CI lints: backend and generator

backend-lint: ## ruff check, ruff format --check, mypy and the OpenAPI drift check (CI backend lint)
	cd backend && uv run ruff check .
	cd backend && uv run ruff format --check .
	cd backend && uv run mypy
	$(MAKE) --no-print-directory openapi-check

generator-install: ## install the generator environment from generator/uv.lock
	$(GENERATOR) sync --locked

generator-lint: ## generator ruff, mypy and the generated-models drift check (CI generator lint)
	cd generator && uv run ruff check .
	cd generator && uv run ruff format --check .
	cd generator && uv run mypy
	$(MAKE) --no-print-directory generator-models-check

# Pydantic models of the API, generated from the committed contract. Never edit the output.
GEN_MODELS_CMD = cd generator && uv run datamodel-codegen --input ../backend/openapi.json \
	--input-file-type openapi --output-model-type pydantic_v2.BaseModel --use-annotated \
	--field-constraints --target-python-version 3.12 --disable-timestamp \
	--enum-field-as-literal all --use-union-operator --use-standard-collections \
	--formatters black isort
GEN_MODELS_OUT := src/turnstile_generator/api_models.py

gen-generator-models: ## regenerate generator/src/turnstile_generator/api_models.py from backend/openapi.json
	$(GEN_MODELS_CMD) --output $(GEN_MODELS_OUT)

generator-models-check: ## regenerate the models into a temp file and diff them with the committed ones
	@tmp="$$(mktemp)"; trap 'rm -f "$$tmp"' EXIT; \
	$(GEN_MODELS_CMD) --output "$$tmp" && diff -u $(GEN_MODELS_OUT) "$$tmp"

format: ## apply ruff fixes and formatting
	cd backend && uv run ruff check --fix .
	cd backend && uv run ruff format .
	cd generator && uv run ruff check --fix .
	cd generator && uv run ruff format .

test: backend-test generator-test ## pytest with the coverage gate (85%) in both projects, no Postgres needed

backend-test: ## backend pytest with the coverage gate (CI backend test)
	cd backend && uv run pytest

generator-test: ## generator pytest with the coverage gate (CI generator test)
	cd generator && uv run pytest

test-fast: ## pytest -x -q without coverage
	cd backend && uv run pytest -x -q --no-cov

migrate: ## apply the migrations to PARKING_DATABASE_URL (alembic upgrade head)
	$(BACKEND) run campus-parking migrate

warehouse: ## incremental dbt build of the warehouse as a recorded run (needs PARKING_DATABASE_URL)
	$(BACKEND) run --group dbt campus-parking warehouse

warehouse-full: ## source freshness and a full-refresh dbt build as a recorded run
	$(BACKEND) run --group dbt campus-parking warehouse --full

# Local Postgres from compose.yaml (service `postgres`). Integration tests use their own database
# (parking_test, created on demand), never the application database `parking`.
TEST_DATABASE_URL ?= postgresql+asyncpg://parking:parking@localhost:5432/parking_test
export TEST_DATABASE_URL

up-db: ## start the local Postgres and wait until it is healthy
	docker compose up -d --wait postgres

down: ## stop the local stack and delete its volumes
	docker compose down -v

test-integration: ## pytest -m integration against TEST_DATABASE_URL (run make up-db first)
	cd backend && uv run pytest -m integration --no-cov -q

openapi: ## write backend/openapi.json from the app
	$(BACKEND) run campus-parking openapi --output backend/openapi.json

openapi-check: ## regenerate the OpenAPI document into a temp file and diff it with backend/openapi.json
	@tmp="$$(mktemp)"; trap 'rm -f "$$tmp"' EXIT; \
	$(BACKEND) run campus-parking openapi --output "$$tmp" && diff -u backend/openapi.json "$$tmp"
