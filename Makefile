# ---------------------------------------------------------------------------
# SOWSprint.ai — developer entry points
# ---------------------------------------------------------------------------
# `make help` lists everything. The first three targets are the ones you want:
#
#   make install   create the virtualenv and install dependencies
#   make demo      run the whole pipeline headlessly, no credentials needed
#   make dev       start the Chainlit UI on 0.0.0.0:8000
# ---------------------------------------------------------------------------

SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON      ?= python3
VENV        ?= .venv
VENV_PY     := $(VENV)/bin/python
VENV_PIP    := $(VENV)/bin/pip
SRC         := src
export PYTHONPATH := $(CURDIR)/$(SRC)
export PIP_CACHE_DIR := $(CURDIR)/.pip-cache
export TMPDIR := $(CURDIR)/.tmp

HOST        ?= 0.0.0.0
PORT        ?= 8000
JURISDICTION ?= EU

# Docker needs a writable config directory; keep it inside the project so the
# build works in sandboxed environments that deny writes to ~/.docker.
export DOCKER_CONFIG := $(CURDIR)/.docker

.PHONY: help
help: ## Show this help
	@echo "SOWSprint.ai — available targets:"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------------- setup
.PHONY: install
install: ## Create the virtualenv and install runtime + dev dependencies
	$(PYTHON) -m venv $(VENV)
	$(VENV_PIP) install --upgrade pip
	$(VENV_PIP) install -r requirements-dev.txt
	$(VENV_PIP) install -e . --no-deps
	@echo "✅ Installed. Run 'make demo' to see the pipeline end to end."

.PHONY: install-runtime
install-runtime: ## Install runtime dependencies only (used by CI images)
	$(VENV_PIP) install -r requirements.txt

# --------------------------------------------------------------------------- run
.PHONY: dev
dev: ## Start the Chainlit UI on $(HOST):$(PORT)
	$(VENV_PY) -m chainlit run app.py --host $(HOST) --port $(PORT)

.PHONY: dev-reload
dev-reload: ## Start the UI with auto-reload on file changes
	$(VENV_PY) -m chainlit run app.py --host $(HOST) --port $(PORT) -w

.PHONY: demo
demo: ## Run the full pipeline headlessly (offline engine, no credentials)
	$(VENV_PY) scripts/demo_e2e.py --jurisdiction $(JURISDICTION)

.PHONY: demo-vague
demo-vague: ## Demo the bounded clarification loop with a deliberately vague brief
	$(VENV_PY) scripts/demo_e2e.py --vague --auto-deploy

.PHONY: demo-us
demo-us: ## Demo the US compliance regime (SEC / Delaware / CCPA / HIPAA)
	$(VENV_PY) scripts/demo_e2e.py --us-scenario --jurisdiction US --auto-deploy

# --------------------------------------------------------------------------- data
.PHONY: ingest
ingest: ## Ingest the compliance corpus into the configured vector store
	$(VENV_PY) -m sowsprint.rag.ingest

.PHONY: reingest
reingest: ## Drop and rebuild the vector collection from scratch
	$(VENV_PY) -m sowsprint.rag.ingest --recreate

.PHONY: corpus-stats
corpus-stats: ## Report corpus size and the EU/US chunk split
	$(VENV_PY) -m sowsprint.rag.ingest --stats-only

# --------------------------------------------------------------------------- quality
.PHONY: test
test: ## Run the test suite
	$(VENV_PY) -m pytest -q

.PHONY: test-cov
test-cov: ## Run tests with a coverage report
	$(VENV_PY) -m pytest --cov=sowsprint --cov-report=term-missing --cov-report=html

.PHONY: test-fast
test-fast: ## Run tests, stopping at the first failure
	$(VENV_PY) -m pytest -q -x

.PHONY: lint
lint: ## Lint with ruff
	$(VENV_PY) -m ruff check $(SRC) tests app.py scripts

.PHONY: format
format: ## Auto-fix lint findings and format
	$(VENV_PY) -m ruff check --fix $(SRC) tests app.py scripts
	$(VENV_PY) -m ruff format $(SRC) tests app.py scripts

.PHONY: typecheck
typecheck: ## Type-check the package
	$(VENV_PY) -m mypy $(SRC)/sowsprint

.PHONY: check
check: lint test ## Lint and test — the CI gate

# --------------------------------------------------------------------------- docker
.PHONY: up
up: ## Build and start the full stack in the background
	docker compose up --build -d
	@echo "✅ UI: http://localhost:$${SOWSPRINT_HOST_PORT:-8000}"

.PHONY: down
down: ## Stop the stack
	docker compose down

.PHONY: clean-docker
clean-docker: ## Stop the stack and delete its volumes (destroys the vector index)
	docker compose down -v

.PHONY: logs
logs: ## Tail application logs
	docker compose logs -f sowsprint-app

.PHONY: ps
ps: ## Show container and health status
	docker compose ps

.PHONY: env-check
env-check: ## Report which credentials are missing (never prints values)
	$(VENV_PY) scripts/setenv.py --show

.PHONY: set-env
set-env: ## Set one .env value safely: make set-env KEY=SOWSPRINT_... VALUE=...
	$(VENV_PY) scripts/setenv.py $(KEY) $(VALUE)

.PHONY: health
health: ## Probe the running container's liveness and readiness
	docker compose exec sowsprint-app python scripts/healthcheck.py --deep

.PHONY: verify-ui
verify-ui: ## Drive the running UI over its real socket.io protocol
	$(VENV_PY) scripts/verify_ui.py --url http://127.0.0.1:$${SOWSPRINT_HOST_PORT:-8000}

.PHONY: verify-voice
verify-voice: ## Stream audio through the live voice hooks and assert a transcript lands
	$(VENV_PY) scripts/verify_voice.py --url http://127.0.0.1:$${SOWSPRINT_HOST_PORT:-8000} --expect-transcript

.PHONY: whisper-stub
whisper-stub: ## Run the self-hosted Whisper test double on :9000
	$(VENV_PY) scripts/whisper_stub.py --port 9000

# --------------------------------------------------------------------------- misc
.PHONY: lan-ip
lan-ip: ## Print the LAN URL to open on an iPhone on the same Wi-Fi
	@ip=$$(hostname -I 2>/dev/null | awk '{print $$1}'); \
	 echo "Open on your phone: http://$${ip:-<your-lan-ip>}:$${PORT}"

.PHONY: clean
clean: ## Remove caches and rendered artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf data/artifacts/*
