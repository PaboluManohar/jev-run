# ── Mobile Jev Makefile ─────────────────────────────────────────────────────
# Standard operations: setup, build, running (dev/prod), verification, and cleanup.

.DEFAULT_GOAL := help
.PHONY: help prerequisites prequisites install install-backend install-frontend \
        build dev-run dev dev-backend dev-frontend \
        run start start-backend start-frontend \
        stream \
        check test test-backend lint typecheck format format-fix \
        clean clean-all clean-ports devices adb-restart export-backend

SHELL := /bin/bash

# ── Configuration Variables ─────────────────────────────────────────────────
VENV_DIR       ?= backend/.venv
PYTHON         ?= $(VENV_DIR)/bin/python
UVICORN        ?= $(VENV_DIR)/bin/uvicorn
PYTEST         ?= $(VENV_DIR)/bin/pytest
UV             ?= $(shell which uv 2>/dev/null || echo "")
PNPM           ?= $(shell which pnpm 2>/dev/null || echo "corepack pnpm")
BACKEND_HOST   ?= 127.0.0.1
BACKEND_PORT   ?= 8005
STUDIO_PORT    ?= 3040
STREAMING_DIR  ?= ws-scrcpy-runtime

## help: Show this help message
help:
	@echo "Usage: make [target]"
	@echo ""
	@sed -n 's/^## //p' $(MAKEFILE_LIST) | column -t -s ':' | sed -e 's/^/  /'

# ── Core Workflow ───────────────────────────────────────────────────────────

## ── Core Workflow ──
## prerequisites: Install venv, all Python backend dependencies, and frontend node_modules
prerequisites: install-backend install-frontend

# Aliases for prerequisites
prequisites: prerequisites
install: prerequisites

## build: Build the Next.js frontend application for production
build:
	@echo "==> Building Next.js frontend..."
	@$(PNPM) build

## dev-run: Run both backend (FastAPI reload) and frontend (Next.js) in dev mode
dev-run:
	@echo "==> Starting backend on http://$(BACKEND_HOST):$(BACKEND_PORT) and frontend on http://127.0.0.1:$(STUDIO_PORT)..."
	@trap 'kill 0' EXIT; \
	$(MAKE) dev-backend & \
	$(MAKE) dev-frontend & \
	wait

# Alias for dev-run
dev: dev-run

## run: Run both backend and frontend concurrently in production mode
run:
	@echo "==> Starting production backend on http://$(BACKEND_HOST):$(BACKEND_PORT) and frontend on http://127.0.0.1:$(STUDIO_PORT)..."
	@trap 'kill 0' EXIT; \
	$(MAKE) start-backend & \
	$(MAKE) start-frontend & \
	wait

# Alias for run
start: run

## stream: Run prebuilt ws-scrcpy-runtime directly using npm start
stream:
	@cd $(STREAMING_DIR) && npm start

# ── Granular Services ───────────────────────────────────────────────────────

## ── Granular Services ──
## dev-backend: Run only the FastAPI development server with auto-reload (port 8000)
dev-backend:
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "==> Python venv missing, running install-backend..."; \
		$(MAKE) install-backend; \
	fi
	@echo "==> Starting FastAPI backend server..."
	@$(UVICORN) backend.main:app --host $(BACKEND_HOST) --port $(BACKEND_PORT) --reload

## dev-frontend: Run only the Next.js studio dev server (port 3040)
dev-frontend:
	@if [ ! -d "node_modules" ] || [ ! -d "frontend/node_modules" ]; then \
		echo "==> node_modules missing, installing frontend dependencies..."; \
		$(PNPM) install; \
	fi
	@echo "==> Starting Next.js studio frontend..."
	@STUDIO_PORT=$(STUDIO_PORT) FASTAPI_URL=http://$(BACKEND_HOST):$(BACKEND_PORT) $(PNPM) dev

## start-backend: Run only the FastAPI backend in production mode (port 8000)
start-backend:
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "==> Python venv missing, running install-backend..."; \
		$(MAKE) install-backend; \
	fi
	@echo "==> Starting production FastAPI backend server..."
	@$(UVICORN) backend.main:app --host $(BACKEND_HOST) --port $(BACKEND_PORT) --workers 1 --loop uvloop --http httptools --proxy-headers --access-log

## start-frontend: Run only Next.js frontend in production mode (port 3040, builds if missing)
start-frontend:
	@if [ ! -d "frontend/.next" ]; then \
		echo "==> Production build missing, running build..."; \
		$(MAKE) build; \
	fi
	@echo "==> Starting production Next.js studio frontend..."
	@STUDIO_PORT=$(STUDIO_PORT) FASTAPI_URL=http://$(BACKEND_HOST):$(BACKEND_PORT) $(PNPM) start

install-backend:
	@echo "==> Setting up Python virtual environment in $(VENV_DIR)..."
	@if [ -n "$(UV)" ]; then \
		[ -d "$(VENV_DIR)" ] || $(UV) venv $(VENV_DIR); \
		$(UV) pip install -e "./backend[dev]" --python $(PYTHON); \
	else \
		[ -d "$(VENV_DIR)" ] || python3 -m venv $(VENV_DIR); \
		$(PYTHON) -m pip install --upgrade pip; \
		$(PYTHON) -m pip install -e "./backend[dev]"; \
	fi
	@echo "==> Backend setup complete."

install-frontend:
	@echo "==> Installing frontend dependencies with pnpm..."
	@$(PNPM) install
	@echo "==> Frontend setup complete."

# ── Code Quality & Verification ─────────────────────────────────────────────

## ── Quality & Testing ──
## check: Run full verification pipeline (lint, typecheck, format, build, test)
check: lint typecheck format build test
	@echo "==> All checks passed successfully."

## test: Run backend pytest test suite
test:
	@echo "==> Running backend tests..."
	@$(PYTEST) backend/tests -v

test-backend: test

## lint: Run ESLint across frontend and repository code
lint:
	@echo "==> Running ESLint..."
	@$(PNPM) lint

## typecheck: Run TypeScript type check on frontend
typecheck:
	@echo "==> Running TypeScript typecheck..."
	@$(PNPM) typecheck

## format: Check code formatting with Prettier
format:
	@echo "==> Checking code format with Prettier..."
	@$(PNPM) format

## format-fix: Auto-format codebase using Prettier
format-fix:
	@echo "==> Formatting code with Prettier..."
	@$(PNPM) format:fix

# ── Device & Ports ──────────────────────────────────────────────────────────

## ── Device & Ports ──
## devices: List connected Android devices via ADB
devices:
	@echo "==> Connected ADB devices:"
	@adb devices -l

## adb-restart: Restart ADB daemon server
adb-restart:
	@echo "==> Restarting ADB server..."
	@adb kill-server
	@adb start-server

## clean-ports: Terminate any processes blocking backend (8000) or frontend (3040) ports
clean-ports:
	@for port in $(BACKEND_PORT) $(STUDIO_PORT); do \
		pids=$$(lsof -ti tcp:$$port 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "==> Stopping process(es) on port $$port: $$pids"; \
			echo "$$pids" | xargs -r kill; \
		else \
			echo "==> Port $$port is free"; \
		fi; \
	done

# ── Cleanup ─────────────────────────────────────────────────────────────────

## ── Cleanup ──
## clean: Remove caches, build artifacts, and compiled bytecode
clean:
	@echo "==> Cleaning caches and artifacts..."
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@rm -rf .pytest_cache
	@rm -rf frontend/.next
	@rm -rf frontend/node_modules/.cache
	@rm -rf coverage
	@echo "==> Clean complete."

## clean-all: Deep clean caches, build artifacts, node_modules, and Python venv
clean-all: clean
	@echo "==> Removing node_modules and Python venv..."
	@rm -rf node_modules frontend/node_modules $(VENV_DIR)
	@echo "==> Deep clean complete. Reinstall with 'make prerequisites'."

# Utility target (kept for maintenance, hidden from primary help)
export-backend:
	@echo "==> Exporting backend source files..."
	@find backend -type d \( -name ".venv" -o -name "__pycache__" -o -name ".pytest_cache" \) -prune -o -type f \( -name "*.py" -o -name "*.toml" \) -print | while IFS= read -r file; do printf '\n===== %s =====\n' "$$file"; cat "$$file"; done > backend_contents.txt
	@echo "==> Export complete: backend_contents.txt"