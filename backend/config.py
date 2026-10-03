"""Application settings loaded from environment variables and .env files."""

from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All configuration consumed by the FastAPI backend.

    Values come from environment variables (or a ``.env`` / ``.env.local`` file
    located in the repository root).  Prefix-less names keep parity with the
    original TypeScript env-var contract where sensible; new variables specific
    to the Python backend use a descriptive prefix.
    """

    # ── ADB / device ────────────────────────────────────────────────────
    adb_device_serial: str = ""
    """Serial number passed to ``adb -s``.  Empty means *use the sole
    connected device* (equivalent to the bare ``adb`` default)."""

    adb_path: str = "adb"
    """Absolute path to the ``adb`` binary, if it is not on ``$PATH``."""

    # ── TypeSafe AI ─────────────────────────────────────────────────────
    typesafe_api_key: str = ""
    typesafe_model: str = "jev-latest"
    typesafe_api_url: str = "https://api.typesafe.ai/v1/systemone"

    # ── Text input ──────────────────────────────────────────────────────
    text_completion_mode: str = "accepted"
    """``accepted`` verifies input locally; ``committed`` waits server-side."""

    # ── Server ──────────────────────────────────────────────────────────
    fastapi_port: int = 8000
    studio_port: int = 3040

    # ── Limits ──────────────────────────────────────────────────────────
    max_runs: int = 30
    max_goal_length: int = 4000
    max_steps_limit: int = 50

    model_config = {
        "env_file": (
            str(Path(__file__).resolve().parent.parent / ".env"),
            str(Path(__file__).resolve().parent.parent / ".env.local"),
        ),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — call this from dependency injection."""
    return Settings()
