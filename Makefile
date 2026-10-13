# ── Mobile Jev Makefile ─────────────────────────────────────────────────────
# Standard operations: creating, preparing, starting apps, verification, and cleanup.

.DEFAULT_GOAL := help
.PHONY: help install install-backend install-frontend dev dev-backend dev-frontend \
        test test-backend lint typecheck format format-fix check build clean clean-all \
        clean-ports devices adb-restart export-backend

SHELL := /bin/bash

# Configuration variables
VENV_DIR       ?= backend/.venv
PYTHON         ?= $(VENV_DIR)/bin/python
UVICORN        ?= $(VENV_DIR)/bin/uvicorn
PYTEST         ?= $(VENV_DIR)/bin/pytest
UV             ?= $(shell which uv 2>/dev/null || echo "")
PNPM           ?= $(shell which pnpm 2>/dev/null || echo "corepack pnpm")
BACKEND_HOST   ?= 127.0.0.1
BACKEND_PORT   ?= 8000
STUDIO_PORT    ?= 3040

## help: Show this help message
help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@sed -n 's/^## //p' $(MAKEFILE_LIST) | column -t -s ':' | sed -e 's/^/  /'

# ── Setup & Installation ────────────────────────────────────────────────────

## install: Install all backend (Python) and frontend (Node/pnpm) dependencies
install: install-backend install-frontend

## install-backend: Create Python virtual environment and install backend dependencies
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

## install-frontend: Install frontend packages using pnpm
install-frontend:
	@echo "==> Installing frontend dependencies with pnpm..."
	@$(PNPM) install
	@echo "==> Frontend setup complete."

# ── Development & Running Apps ──────────────────────────────────────────────

## dev: Run both backend (FastAPI) and frontend (Next.js) concurrently
dev:
	@echo "==> Starting backend on http://$(BACKEND_HOST):$(BACKEND_PORT) and frontend on http://127.0.0.1:$(STUDIO_PORT)..."
	@trap 'kill 0' EXIT; \
	$(MAKE) dev-backend & \
	$(MAKE) dev-frontend & \
	wait

## dev-backend: Run the FastAPI development server with auto-reload
dev-backend:
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "==> Python venv missing, running install-backend..."; \
		$(MAKE) install-backend; \
	fi
	@echo "==> Starting FastAPI backend server..."
	@$(UVICORN) backend.main:app --host $(BACKEND_HOST) --port $(BACKEND_PORT) --reload

## dev-frontend: Run the Next.js studio dev server
dev-frontend:
	@if [ ! -d "node_modules" ] || [ ! -d "frontend/node_modules" ]; then \
		echo "==> node_modules missing, installing frontend dependencies..."; \
		$(PNPM) install; \
	fi
	@echo "==> Starting Next.js studio frontend..."
	@STUDIO_PORT=$(STUDIO_PORT) FASTAPI_URL=http://$(BACKEND_HOST):$(BACKEND_PORT) $(PNPM) dev

# ── Testing & Code Quality ──────────────────────────────────────────────────

## test: Run backend pytest test suite
test: test-backend

## test-backend: Run backend unit and integration tests with pytest
test-backend:
	@echo "==> Running backend tests..."
	@$(PYTEST) backend/tests -v

## lint: Run ESLint across frontend and repository code
lint:
	@echo "==> Running ESLint..."
	@$(PNPM) lint

## typecheck: Run TypeScript type checking on Next.js frontend
typecheck:
	@echo "==> Running TypeScript typecheck..."
	@$(PNPM) typecheck

## format: Check code formatting with Prettier
format:
	@echo "==> Checking code format with Prettier..."
	@$(PNPM) format

## format-fix: Format codebase in-place using Prettier
format-fix:
	@echo "==> Formatting code with Prettier..."
	@$(PNPM) format:fix

## check: Run full verification pipeline (lint, typecheck, format, build, test)
check: lint typecheck format build test-backend
	@echo "==> All checks passed successfully."

# ── Build ───────────────────────────────────────────────────────────────────

## build: Build Next.js frontend for production
build:
	@echo "==> Building Next.js frontend..."
	@$(PNPM) build

# ── Device & ADB Infrastructure ─────────────────────────────────────────────

## devices: List connected Android devices via ADB
devices:
	@echo "==> Connected ADB devices:"
	@adb devices -l

## adb-restart: Restart ADB daemon server
adb-restart:
	@echo "==> Restarting ADB server..."
	@adb kill-server
	@adb start-server

# ── Cleanup & Port Management ───────────────────────────────────────────────

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
	@echo "==> Deep clean complete. Reinstall with 'make install'."

## export-backend: Export backend source files into a single text file
export-backend:
	@echo "==> Exporting backend source files..."
	@find backend -type d \( -name ".venv" -o -name "__pycache__" -o -name ".pytest_cache" \) -prune -o -type f \( -name "*.py" -o -name "*.toml" \) -print | while IFS= read -r file; do printf '\n===== %s =====\n' "$$file"; cat "$$file"; done > backend_contents.txt
	@echo "==> Export complete: backend_contents.txt"