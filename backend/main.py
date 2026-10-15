"""FastAPI application — entry point for the Jev backend.

Replaces the Node.js server-side logic (``route.ts`` + ``store.mjs`` +
``web-runner.mjs``) with a single FastAPI application.

Start with::

    cd backend
    uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import get_settings
from backend.routers import device, runs, health, decision
from backend.services.store import StudioError, get_store
from backend.services.decision_model import MissingConfigError, init_client, close_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialise services and clean up on shutdown."""
    # Initialise the TypeSafe decision-model client at startup.
    # If TYPESAFE_API_KEY is absent we log a warning and continue; the /decision
    # endpoint will return 503 until the key is provided.
    try:
        await init_client()
    except MissingConfigError as exc:
        import logging
        logging.getLogger(__name__).warning(
            "TypeSafe client not initialised: %s  "
            "Set TYPESAFE_API_KEY to enable the /decision endpoint.",
            exc,
        )

    yield

    # Tear down the TypeSafe client.
    await close_client()

    # Stop any running tasks on shutdown.
    store = get_store()
    for run in store.list():
        if run.get("status") in ("running", "stopping"):
            try:
                store.stop(run["id"])
            except Exception:
                pass


app = FastAPI(
    title="Jev Mobile Agent Backend",
    description="FastAPI backend controlling Android devices via ADB — replaces MobileRun.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow the Next.js frontend on localhost.
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{settings.studio_port}",
        f"http://127.0.0.1:{settings.studio_port}",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Exception handlers ──────────────────────────────────────────────────


@app.exception_handler(StudioError)
async def studio_error_handler(request: Request, exc: StudioError):
    return JSONResponse(
        status_code=exc.status,
        content={"error": str(exc)},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"error": str(exc)},
    )


# ── Routers ──────────────────────────────────────────────────────────────

# Mount under /api/studio to match the Next.js proxy paths the frontend uses.
# This means the frontend's fetch('/api/studio/device') hits our /api/studio/device.
app.include_router(device.router, prefix="/api/studio")
app.include_router(runs.router, prefix="/api/studio")
app.include_router(health.router, prefix="/api/studio")
app.include_router(decision.router, prefix="/api/studio")

# Also mount without prefix for direct access.
app.include_router(device.router)
app.include_router(runs.router)
app.include_router(health.router)
app.include_router(decision.router)


@app.get("/")
async def root():
    return {
        "service": "jev-backend",
        "version": "0.1.0",
        "docs": "/docs",
    }
