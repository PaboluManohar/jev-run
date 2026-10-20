# ── Mobile Jev Makefile ─────────────────────────────────────────────────────
# Standard operations: setup, build, running (dev/prod), verification, and cleanup.

.DEFAULT_GOAL := help
.PHONY: help install build dev dev-all dev-backend dev-frontend \
        stream run test lint typecheck format format-fix \
        devices adb-restart clean-ports clean clean-all

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
STREAMING_PORT ?= 8000
STREAMING_DIR  ?= ws-scrcpy-runtime

## help: Show available commands and their descriptions
help:
	@echo "Usage: make [target]"
	@echo ""
	@sed -n 's/^## //p' $(MAKEFILE_LIST) | column -t -s ':' | sed -e 's/^/  /'

# ── Setup & Build ───────────────────────────────────────────────────────────

## install: Install backend virtual environment, Python dependencies, and frontend node_modules
install:
	@echo "==> Setting up Python virtual environment in $(VENV_DIR)..."
	@if [ -n "$(UV)" ]; then \
		[ -d "$(VENV_DIR)" ] || $(UV) venv $(VENV_DIR); \
		$(UV) pip install -e "./backend[dev]" --python $(PYTHON); \
	else \
		[ -d "$(VENV_DIR)" ] || python3 -m venv $(VENV_DIR); \
		$(PYTHON) -m pip install --upgrade pip; \
		$(PYTHON) -m pip install -e "./backend[dev]"; \
	fi
	@echo "==> Installing frontend dependencies with pnpm..."
	@$(PNPM) install
	@echo "==> Installation complete."

## build: Build the Next.js frontend application for production
build:
	@echo "==> Building Next.js frontend..."
	@$(PNPM) build

# ── Development & Running ───────────────────────────────────────────────────

## dev: Run backend (FastAPI reload) and frontend (Next.js) concurrently
dev:
	@echo "==> Starting backend on http://$(BACKEND_HOST):$(BACKEND_PORT) and frontend on http://127.0.0.1:$(STUDIO_PORT)..."
	@trap 'kill 0' EXIT; \
	$(MAKE) dev-backend & \
	$(MAKE) dev-frontend & \
	wait

## dev-all: Run backend (FastAPI), frontend (Next.js), and streaming runtime (ws-scrcpy) concurrently
dev-all:
	@echo "==> Starting backend on http://$(BACKEND_HOST):$(BACKEND_PORT), frontend on http://127.0.0.1:$(STUDIO_PORT), and stream on http://127.0.0.1:$(STREAMING_PORT)..."
	@trap 'kill 0' EXIT; \
	$(MAKE) stream & \
	$(MAKE) dev-backend & \
	$(MAKE) dev-frontend & \
	wait

## dev-backend: Run only the FastAPI development server with auto-reload (port 8005)
dev-backend:
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "==> Python venv missing, running install..."; \
		$(MAKE) install; \
	fi
	@echo "==> Starting FastAPI backend server..."
	@$(UVICORN) backend.main:app --host $(BACKEND_HOST) --port $(BACKEND_PORT) --reload

## dev-frontend: Run only the Next.js studio dev server (port 3040)
dev-frontend:
	@if [ ! -d "node_modules" ] || [ ! -d "frontend/node_modules" ]; then \
		echo "==> Dependencies missing, running install..."; \
		$(MAKE) install; \
	fi
	@echo "==> Starting Next.js studio frontend..."
	@STUDIO_PORT=$(STUDIO_PORT) FASTAPI_URL=http://$(BACKEND_HOST):$(BACKEND_PORT) $(PNPM) dev

## stream: Run prebuilt ws-scrcpy-runtime directly using npm start (port 8000)
stream:
	@cd $(STREAMING_DIR) && npm start

## run: Run both backend and frontend concurrently in production mode (builds if missing)
run:
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "==> Python venv missing, running install..."; \
		$(MAKE) install; \
	fi
	@if [ ! -d "frontend/.next" ]; then \
		echo "==> Production build missing, running build..."; \
		$(MAKE) build; \
	fi
	@echo "==> Starting production backend on http://$(BACKEND_HOST):$(BACKEND_PORT) and frontend on http://127.0.0.1:$(STUDIO_PORT)..."
	@trap 'kill 0' EXIT; \
	$(UVICORN) backend.main:app --host $(BACKEND_HOST) --port $(BACKEND_PORT) --workers 1 --loop uvloop --http httptools --proxy-headers & \
	STUDIO_PORT=$(STUDIO_PORT) FASTAPI_URL=http://$(BACKEND_HOST):$(BACKEND_PORT) $(PNPM) start & \
	wait

# ── Quality & Testing ───────────────────────────────────────────────────────

## test: Run backend pytest test suite
test:
	@echo "==> Running backend tests..."
	@$(PYTEST) backend/tests -v

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

## devices: List connected Android devices via ADB
devices:
	@echo "==> Connected ADB devices:"
	@adb devices -l

## adb-restart: Restart ADB daemon server
adb-restart:
	@echo "==> Restarting ADB server..."
	@adb kill-server
	@adb start-server

## clean-ports: Terminate any processes blocking backend (8005), frontend (3040), or stream (8000)
clean-ports:
	@for port in $(BACKEND_PORT) $(STUDIO_PORT) $(STREAMING_PORT); do \
		pids=$$(lsof -ti tcp:$$port 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "==> Stopping process(es) on port $$port: $$pids"; \
			echo "$$pids" | xargs -r kill; \
		else \
			echo "==> Port $$port is free"; \
		fi; \
	done

# ── Cleanup ─────────────────────────────────────────────────────────────────

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

## clean-all: Deep clean caches, build artifacts, node_modules, and Python virtual environment
clean-all: clean
	@echo "==> Removing node_modules and Python virtual environment..."
	@rm -rf node_modules frontend/node_modules $(VENV_DIR)
	@echo "==> Deep clean complete. Reinstall with 'make install'."