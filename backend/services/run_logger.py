"""Autonomous run logging infrastructure with correlation ID and stage propagation.

Provides:
- Context variables (correlation_id, stage) that automatically propagate through
  asyncio tasks and concurrent runs.
- High-resolution timing (time.perf_counter()) for all execution stages and total runs.
- Formatter and filter including timestamp, log level, correlation ID, stage,
  duration, filename, line number, and diagnostic details.
- Sanitization for credentials, API keys, large screenshots, and XML dumps.
- Configurable log verbosity via environment variable (LOG_LEVEL / JEV_LOG_LEVEL).
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import sys
import time
from typing import Any

# ── Context Variables ────────────────────────────────────────────────────────

_correlation_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
_stage_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "stage", default=None
)


def get_correlation_id() -> str | None:
    """Return the correlation ID active in the current async context, or None."""
    return _correlation_id_ctx.get()


def set_correlation_id(correlation_id: str | None) -> contextvars.Token:
    """Set the active correlation ID in the current async context."""
    return _correlation_id_ctx.set(correlation_id)


def reset_correlation_id(token: contextvars.Token) -> None:
    """Reset the correlation ID back to its previous value."""
    _correlation_id_ctx.reset(token)


def get_current_stage() -> str | None:
    """Return the stage name active in the current async context, or None."""
    return _stage_ctx.get()


def set_current_stage(stage: str | None) -> contextvars.Token:
    """Set the active stage name in the current async context."""
    return _stage_ctx.set(stage)


def reset_current_stage(token: contextvars.Token) -> None:
    """Reset the stage name back to its previous value."""
    _stage_ctx.reset(token)


# ── Sanitization ─────────────────────────────────────────────────────────────

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(api[_-]?key|secret|password|token|auth|bearer)", re.IGNORECASE
)
_API_KEY_VAL_PATTERN = re.compile(r"sk-[a-zA-Z0-9_-]{12,}")


def sanitize_value(val: Any, max_str_len: int = 500) -> Any:
    """Scrub sensitive keys/patterns and prevent logging huge payloads.

    - Replaces bytes with `<bytes N>`.
    - Redacts strings matching API key patterns or known sensitive keywords.
    - Summarises large XML or string payloads exceeding max_str_len.
    - Recursively cleans dictionaries and lists.
    """
    if isinstance(val, (bytes, bytearray)):
        return f"<bytes {len(val)}>"

    if isinstance(val, str):
        if "<hierarchy" in val or "<node" in val:
            return f"<xml {len(val)} chars>"
        # Redact API key tokens
        sanitized = _API_KEY_VAL_PATTERN.sub("[REDACTED]", val)
        # Check against known configured key if available
        try:
            from backend.config import get_settings

            known_key = get_settings().typesafe_api_key
            if known_key and len(known_key) > 4:
                sanitized = sanitized.replace(known_key, "[REDACTED]")
        except Exception:
            pass

        if len(sanitized) > max_str_len:
            return sanitized[:max_str_len] + "... (truncated)"
        return sanitized

    if isinstance(val, dict):
        clean_dict: dict[str, Any] = {}
        for k, v in val.items():
            k_str = str(k)
            if _SENSITIVE_KEY_PATTERN.search(k_str):
                clean_dict[k_str] = "[REDACTED]"
            else:
                clean_dict[k_str] = sanitize_value(v, max_str_len=max_str_len)
        return clean_dict

    if isinstance(val, (list, tuple, set)):
        if len(val) > 20:
            sample = [sanitize_value(x, max_str_len=max_str_len) for x in list(val)[:10]]
            return f"<collection len={len(val)} sample={sample}>"
        return [sanitize_value(x, max_str_len=max_str_len) for x in val]

    return val


def format_diagnostic_details(**kwargs: Any) -> str:
    """Format sanitized diagnostic key-value pairs into a log-friendly string."""
    if not kwargs:
        return ""
    parts = []
    for k, v in kwargs.items():
        parts.append(f"{k}={sanitize_value(v)}")
    return ", ".join(parts)


# ── Stage & Run Context Managers ─────────────────────────────────────────────


class StageContext:
    """Synchronous and asynchronous context manager tracking a single execution stage.

    Emits structured logs on start, completion, and failure using time.perf_counter().
    Updates the stage context variable during execution.
    """

    def __init__(
        self,
        stage_name: str,
        logger: logging.Logger | None = None,
        **details: Any,
    ) -> None:
        self.stage_name = stage_name
        self.logger = logger or logging.getLogger("backend.run")
        self.details = details
        self._token: contextvars.Token | None = None
        self._start_perf: float = 0.0

    def _enter(self) -> "StageContext":
        self._token = set_current_stage(self.stage_name)
        self._start_perf = time.perf_counter()
        detail_msg = format_diagnostic_details(**self.details)
        prefix = f" | {detail_msg}" if detail_msg else ""
        self.logger.info(
            "Stage [%s] started%s",
            self.stage_name,
            prefix,
            extra={
                "stage": self.stage_name,
                "stage_event": "start",
                "duration_ms": "-",
            },
        )
        return self

    def _exit(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        duration_ms = (time.perf_counter() - self._start_perf) * 1000.0
        detail_msg = format_diagnostic_details(**self.details)
        prefix = f" | {detail_msg}" if detail_msg else ""

        if exc_type is not None:
            self.logger.error(
                "Stage [%s] failed after %.2fms%s: %s",
                self.stage_name,
                duration_ms,
                prefix,
                exc_val,
                exc_info=True,
                extra={
                    "stage": self.stage_name,
                    "stage_event": "failure",
                    "duration_ms": duration_ms,
                },
            )
        else:
            self.logger.info(
                "Stage [%s] completed in %.2fms%s",
                self.stage_name,
                duration_ms,
                prefix,
                extra={
                    "stage": self.stage_name,
                    "stage_event": "completion",
                    "duration_ms": duration_ms,
                },
            )

        if self._token is not None:
            reset_current_stage(self._token)
            self._token = None

        return False  # Do not swallow exceptions

    def __enter__(self) -> "StageContext":
        return self._enter()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        return self._exit(exc_type, exc_val, exc_tb)

    async def __aenter__(self) -> "StageContext":
        return self._enter()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        return self._exit(exc_type, exc_val, exc_tb)


class RunContext:
    """Synchronous and asynchronous context manager tracking an autonomous run.

    Binds the correlation ID into contextvars for the current task and any
    subtasks, measures total run duration using time.perf_counter(), and ensures
    correlation ID context cleanup on exit.
    """

    def __init__(
        self,
        correlation_id: str,
        logger: logging.Logger | None = None,
        **details: Any,
    ) -> None:
        self.correlation_id = correlation_id
        self.logger = logger or logging.getLogger("backend.run")
        self.details = details
        self._token: contextvars.Token | None = None
        self._start_perf: float = 0.0

    def _enter(self) -> "RunContext":
        self._token = set_correlation_id(self.correlation_id)
        self._start_perf = time.perf_counter()
        detail_msg = format_diagnostic_details(**self.details)
        prefix = f" | {detail_msg}" if detail_msg else ""
        self.logger.info(
            "Run started [correlation_id=%s]%s",
            self.correlation_id,
            prefix,
            extra={
                "correlation_id": self.correlation_id,
                "stage": "run",
                "stage_event": "start",
                "duration_ms": "-",
            },
        )
        return self

    def _exit(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        duration_ms = (time.perf_counter() - self._start_perf) * 1000.0
        detail_msg = format_diagnostic_details(**self.details)
        prefix = f" | {detail_msg}" if detail_msg else ""

        if exc_type is not None:
            self.logger.error(
                "Run failed after %.2fms [correlation_id=%s]%s: %s",
                duration_ms,
                self.correlation_id,
                prefix,
                exc_val,
                exc_info=True,
                extra={
                    "correlation_id": self.correlation_id,
                    "stage": "run",
                    "stage_event": "failure",
                    "duration_ms": duration_ms,
                },
            )
        else:
            self.logger.info(
                "Run completed in %.2fms [correlation_id=%s]%s",
                duration_ms,
                self.correlation_id,
                prefix,
                extra={
                    "correlation_id": self.correlation_id,
                    "stage": "run",
                    "stage_event": "completion",
                    "duration_ms": duration_ms,
                },
            )

        if self._token is not None:
            reset_correlation_id(self._token)
            self._token = None

        return False

    def __enter__(self) -> "RunContext":
        return self._enter()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        return self._exit(exc_type, exc_val, exc_tb)

    async def __aenter__(self) -> "RunContext":
        return self._enter()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        return self._exit(exc_type, exc_val, exc_tb)


# ── Filter and Formatter ─────────────────────────────────────────────────────


class CorrelationLogFilter(logging.Filter):
    """Enriches every LogRecord with correlation ID, stage name, and duration."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id") or not record.correlation_id:
            record.correlation_id = get_correlation_id() or "-"
        if not hasattr(record, "stage") or not record.stage:
            record.stage = get_current_stage() or "-"

        duration = getattr(record, "duration_ms", None)
        if isinstance(duration, (int, float)):
            record.duration_display = f"{duration:.2f}ms"
        elif duration and duration != "-":
            record.duration_display = f"{duration}ms"
        else:
            record.duration_display = "-"
        return True


class CorrelationFormatter(logging.Formatter):
    """Formats log records including timestamp, level, correlation ID, stage, duration, filename, and lineno."""

    DEFAULT_FORMAT = (
        "%(asctime)s [%(levelname)s] [corr:%(correlation_id)s] [stage:%(stage)s] "
        "[duration:%(duration_display)s] [%(filename)s:%(lineno)d]: %(message)s"
    )

    def __init__(self, fmt: str | None = None, datefmt: str | None = "%Y-%m-%d %H:%M:%S") -> None:
        super().__init__(fmt=fmt or self.DEFAULT_FORMAT, datefmt=datefmt)

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "correlation_id") or not record.correlation_id:
            record.correlation_id = get_correlation_id() or "-"
        if not hasattr(record, "stage") or not record.stage:
            record.stage = get_current_stage() or "-"

        duration = getattr(record, "duration_ms", None)
        if isinstance(duration, (int, float)):
            record.duration_display = f"{duration:.2f}ms"
        elif duration and duration != "-":
            record.duration_display = f"{duration}ms"
        else:
            record.duration_display = "-"

        return super().format(record)


# ── Logging Setup ────────────────────────────────────────────────────────────

_SETUP_HANDLER_MARKER = "_jev_correlation_handler"


from pathlib import Path


def setup_logging(
    log_level: str | int | None = None,
    log_file: str | None = None,
    stream: Any = None,
    log_to_console: bool | None = None,
) -> logging.Logger:
    """Configure backend logging with correlation ID propagation and formatted output.

    Args:
        log_level: Logging level (DEBUG, INFO, etc.). If None, reads from
                   environment variable LOG_LEVEL or JEV_LOG_LEVEL or Settings.
        log_file: Optional file path where logs should be written. If None,
                  reads from LOG_FILE or JEV_LOG_FILE or Settings.log_file.
        stream: Optional stream destination for console handler (defaults to sys.stderr).
        log_to_console: If True, writes to console even when log_file is set.
                        If None, reads from LOG_TO_CONSOLE / JEV_LOG_TO_CONSOLE or defaults
                        to False if log_file is set, True otherwise.

    Returns:
        The configured 'backend' Logger.
    """
    settings = None
    try:
        from backend.config import get_settings

        settings = get_settings()
    except Exception:
        pass

    if log_level is None:
        env_level = os.environ.get("JEV_LOG_LEVEL") or os.environ.get("LOG_LEVEL")
        if env_level:
            log_level = env_level
        elif settings:
            log_level = settings.log_level
        else:
            log_level = "INFO"

    if log_file is None:
        env_file = os.environ.get("JEV_LOG_FILE") or os.environ.get("LOG_FILE")
        if env_file is not None:
            log_file = env_file
        elif settings:
            log_file = settings.log_file
        else:
            log_file = ""

    log_file = log_file.strip() if log_file else ""

    if log_to_console is None:
        env_console = os.environ.get("JEV_LOG_TO_CONSOLE") or os.environ.get("LOG_TO_CONSOLE")
        if env_console is not None:
            log_to_console = env_console.strip().lower() in ("1", "true", "yes")
        elif log_file:
            log_to_console = settings.log_to_console if settings else False
        else:
            log_to_console = True

    if isinstance(log_level, str):
        numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    else:
        numeric_level = log_level

    backend_logger = logging.getLogger("backend")
    backend_logger.setLevel(numeric_level)

    # Clean up existing marked handlers on backend_logger to be idempotent
    existing_handlers = [
        h for h in backend_logger.handlers if getattr(h, _SETUP_HANDLER_MARKER, False)
    ]
    for h in existing_handlers:
        backend_logger.removeHandler(h)

    # File Handler
    if log_file:
        file_path = Path(log_file)
        if file_path.parent and str(file_path.parent) != ".":
            file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(file_path), encoding="utf-8")
        setattr(file_handler, _SETUP_HANDLER_MARKER, True)
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(CorrelationFormatter())
        file_handler.addFilter(CorrelationLogFilter())
        backend_logger.addHandler(file_handler)

    # Console Handler (StreamHandler)
    if log_to_console or not log_file:
        console_handler = logging.StreamHandler(stream or sys.stderr)
        setattr(console_handler, _SETUP_HANDLER_MARKER, True)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(CorrelationFormatter())
        console_handler.addFilter(CorrelationLogFilter())
        backend_logger.addHandler(console_handler)

    # Also configure root logger filter so third-party loggers or root loggers
    # don't fail if they encounter correlation fields
    for root_h in logging.getLogger().handlers:
        if not any(isinstance(f, CorrelationLogFilter) for f in root_h.filters):
            root_h.addFilter(CorrelationLogFilter())

    return backend_logger
