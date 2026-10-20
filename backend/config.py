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
    """API key for TypeSafe AI.  Set via ``TYPESAFE_API_KEY``."""

    typesafe_base_url: str = ""
    """Base URL override for the TypeSafe API.  Set via ``TYPESAFE_BASE_URL``.
    Leave empty to use the SDK's built-in default (``https://api.typesafe.ai``)."""

    typesafe_default_model: str = "jev-latest"
    """Model identifier used by the decision endpoint.  Set via ``TYPESAFE_DEFAULT_MODEL``."""

    # ── Legacy TypeSafe fields (used by services/policy.py) ─────────────
    typesafe_model: str = "jev-latest"
    typesafe_api_url: str = "https://api.typesafe.ai/v1/systemone"

    # ── Text input ──────────────────────────────────────────────────────
    text_completion_mode: str = "accepted"
    """``accepted`` verifies input locally; ``committed`` waits server-side."""

    # ── Server ──────────────────────────────────────────────────────────
    fastapi_port: int = 8005
    studio_port: int = 3040

    # ── Logging ─────────────────────────────────────────────────────────
    log_level: str = "INFO"
    """Logging verbosity level (DEBUG, INFO, WARNING, ERROR). Configurable
    via LOG_LEVEL or JEV_LOG_LEVEL."""

    log_file: str = ""
    """Path to log file (e.g. 'backend.log'). If set (via LOG_FILE or JEV_LOG_FILE),
    logs are written to this file instead of console. If empty, logs to console."""

    log_to_console: bool = False
    """When log_file is set, also mirror logs to console if True. Configurable
    via LOG_TO_CONSOLE or JEV_LOG_TO_CONSOLE."""

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

    def model_post_init(self, __context: Any) -> None:
        import os

        jev_log_level = os.environ.get("JEV_LOG_LEVEL")
        if jev_log_level:
            self.log_level = jev_log_level

        jev_log_file = os.environ.get("JEV_LOG_FILE") or os.environ.get("LOG_FILE")
        if jev_log_file is not None:
            self.log_file = jev_log_file

        jev_log_console = os.environ.get("JEV_LOG_TO_CONSOLE") or os.environ.get("LOG_TO_CONSOLE")
        if jev_log_console is not None:
            self.log_to_console = jev_log_console.strip().lower() in ("1", "true", "yes")

        if self.typesafe_default_model == "jev-latest" and self.typesafe_model != "jev-latest":
            self.typesafe_default_model = self.typesafe_model
        elif self.typesafe_model == "jev-latest" and self.typesafe_default_model != "jev-latest":
            self.typesafe_model = self.typesafe_default_model


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — call this from dependency injection."""
    return Settings()
