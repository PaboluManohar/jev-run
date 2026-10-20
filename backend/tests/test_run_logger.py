"""Tests for autonomous run logging: correlation ID propagation, concurrent isolation,
context cleanup, timing, and formatting.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.run_logger import (
    CorrelationFormatter,
    CorrelationLogFilter,
    RunContext,
    StageContext,
    format_diagnostic_details,
    get_correlation_id,
    get_current_stage,
    reset_correlation_id,
    reset_current_stage,
    sanitize_value,
    set_correlation_id,
    set_current_stage,
    setup_logging,
)
from backend.services.store import RunStore


class MemoryLogHandler(logging.Handler):
    """In-memory logging handler capturing LogRecord instances."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def log_capture():
    """Fixture providing an isolated logger and capture handler."""
    logger = logging.getLogger(f"test_logger_{id(object())}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = MemoryLogHandler()
    handler.setLevel(logging.DEBUG)
    formatter = CorrelationFormatter()
    handler.setFormatter(formatter)
    handler.addFilter(CorrelationLogFilter())
    logger.addHandler(handler)

    yield logger, handler

    logger.removeHandler(handler)


# ── 1. Correlation ID Propagation & Context Cleanup ─────────────────────────


@pytest.mark.asyncio
async def test_correlation_id_set_and_reset():
    """Verify set_correlation_id, get_correlation_id, and reset_correlation_id."""
    assert get_correlation_id() is None

    token = set_correlation_id("test-corr-123")
    assert get_correlation_id() == "test-corr-123"

    reset_correlation_id(token)
    assert get_correlation_id() is None


@pytest.mark.asyncio
async def test_run_context_manager_cleanup():
    """Verify RunContext sets correlation ID and cleans up on exit."""
    assert get_correlation_id() is None

    async with RunContext("corr-run-999"):
        assert get_correlation_id() == "corr-run-999"

    # Context must be cleaned up on normal exit
    assert get_correlation_id() is None

    # Context must be cleaned up on exception exit
    with pytest.raises(RuntimeError):
        async with RunContext("corr-run-failed"):
            assert get_correlation_id() == "corr-run-failed"
            raise RuntimeError("Run explosion")

    assert get_correlation_id() is None


@pytest.mark.asyncio
async def test_correlation_id_propagates_to_async_subtasks():
    """Verify correlation ID propagates to asyncio tasks and gather."""
    observed_corrs: list[str | None] = []

    async def worker(task_name: str):
        await asyncio.sleep(0.01)
        observed_corrs.append(get_correlation_id())

    async with RunContext("parent-corr-456"):
        assert get_correlation_id() == "parent-corr-456"
        task1 = asyncio.create_task(worker("t1"))
        task2 = asyncio.create_task(worker("t2"))
        await asyncio.gather(task1, task2)

    assert observed_corrs == ["parent-corr-456", "parent-corr-456"]
    assert get_correlation_id() is None


# ── 2. Concurrent-Run Isolation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_run_isolation(log_capture):
    """Verify that multiple concurrent runs never mix correlation IDs or stage logs."""
    logger, handler = log_capture

    async def simulate_run(run_id: str, stages: list[str]):
        async with RunContext(run_id, logger=logger):
            for stage in stages:
                async with StageContext(stage, logger=logger):
                    logger.debug("Inside stage %s for %s", stage, run_id)
                    await asyncio.sleep(0.02)
                    logger.info("Finishing stage %s for %s", stage, run_id)

    # Launch two runs concurrently
    await asyncio.gather(
        simulate_run("run-alpha", ["observation", "decision_model_request"]),
        simulate_run("run-beta", ["screenshot_capture", "action_execution"]),
    )

    alpha_records = [r for r in handler.records if getattr(r, "correlation_id", None) == "run-alpha"]
    beta_records = [r for r in handler.records if getattr(r, "correlation_id", None) == "run-beta"]

    assert len(alpha_records) > 0
    assert len(beta_records) > 0

    # Ensure all alpha records mention alpha and none mention beta
    for r in alpha_records:
        assert "run-beta" not in r.getMessage()
        assert r.correlation_id == "run-alpha"

    # Ensure all beta records mention beta and none mention alpha
    for r in beta_records:
        assert "run-alpha" not in r.getMessage()
        assert r.correlation_id == "run-beta"

    # Verify context is fully reset after all concurrent tasks complete
    assert get_correlation_id() is None
    assert get_current_stage() is None


# ── 3. Stage Context & Nesting ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stage_context_nesting_and_cleanup(log_capture):
    """Verify stage nesting restores parent stage on exit and measures duration."""
    logger, handler = log_capture

    assert get_current_stage() is None

    async with StageContext("outer_stage", logger=logger):
        assert get_current_stage() == "outer_stage"
        async with StageContext("inner_stage", logger=logger):
            assert get_current_stage() == "inner_stage"
            await asyncio.sleep(0.01)
        assert get_current_stage() == "outer_stage"

    assert get_current_stage() is None

    # Verify duration was recorded for both stages
    completion_records = [
        r for r in handler.records if getattr(r, "stage_event", None) == "completion"
    ]
    assert len(completion_records) == 2
    for r in completion_records:
        assert isinstance(r.duration_ms, float)
        assert r.duration_ms > 0.0


@pytest.mark.asyncio
async def test_stage_failure_logging(log_capture):
    """Verify stage failure logs error with duration and stack trace."""
    logger, handler = log_capture

    with pytest.raises(ValueError):
        async with StageContext("failing_stage", logger=logger):
            raise ValueError("Failure in stage")

    failure_records = [
        r for r in handler.records if getattr(r, "stage_event", None) == "failure"
    ]
    assert len(failure_records) == 1
    rec = failure_records[0]
    assert rec.stage == "failing_stage"
    assert rec.levelno == logging.ERROR
    assert rec.exc_info is not None  # Stack trace recorded
    assert rec.duration_ms > 0.0


# ── 4. Formatter & Log Output Structure ──────────────────────────────────────


def test_correlation_formatter_output():
    """Verify log formatter produces timestamp, level, correlation ID, stage, duration, file/lineno."""
    formatter = CorrelationFormatter()
    record = logging.LogRecord(
        name="backend.test",
        level=logging.INFO,
        pathname="test_file.py",
        lineno=42,
        msg="Sample log message",
        args=(),
        exc_info=None,
    )
    record.correlation_id = "corr-xyz"
    record.stage = "action_execution"
    record.duration_ms = 12.345

    output = formatter.format(record)

    assert "[INFO]" in output
    assert "[corr:corr-xyz]" in output
    assert "[stage:action_execution]" in output
    assert "[duration:12.35ms]" in output
    assert "[test_file.py:42]" in output
    assert "Sample log message" in output


def test_correlation_formatter_defaults_when_empty():
    """Verify formatter displays '-' when correlation ID or stage is not set."""
    formatter = CorrelationFormatter()
    record = logging.LogRecord(
        name="backend.test",
        level=logging.WARNING,
        pathname="other.py",
        lineno=10,
        msg="Warning without context",
        args=(),
        exc_info=None,
    )

    output = formatter.format(record)
    assert "[corr:-]" in output
    assert "[stage:-]" in output
    assert "[duration:-]" in output


# ── 5. Sanitization & Secrets Protection ─────────────────────────────────────


def test_sanitization_removes_secrets_and_large_buffers():
    """Verify API keys, passwords, screenshots, and XML dumps are safely sanitized."""
    # API key scrubbing
    secret_str = "Connecting to https://api.typesafe.ai with sk-1234567890abcdef123"
    sanitized_str = sanitize_value(secret_str)
    assert "sk-1234567890abcdef123" not in sanitized_str
    assert "[REDACTED]" in sanitized_str

    # Large screenshot bytes scrubbing
    screenshot_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50000
    sanitized_bytes = sanitize_value(screenshot_bytes)
    assert sanitized_bytes == "<bytes 50008>"

    # XML hierarchy scrubbing
    xml_data = "<hierarchy rotation='0'><node bounds='[0,0][1080,2400]'/></hierarchy>" * 10
    sanitized_xml = sanitize_value(xml_data)
    assert sanitized_xml.startswith("<xml ")

    # Dictionary with sensitive keys
    diag = {
        "api_key": "topsecret",
        "password": "mypassword",
        "auth_token": "bearer 1234",
        "goal": "Open settings",
        "normal_field": 42,
    }
    sanitized_dict = sanitize_value(diag)
    assert sanitized_dict["api_key"] == "[REDACTED]"
    assert sanitized_dict["password"] == "[REDACTED]"
    assert sanitized_dict["auth_token"] == "[REDACTED]"
    assert sanitized_dict["goal"] == "Open settings"
    assert sanitized_dict["normal_field"] == 42


def test_format_diagnostic_details():
    """Verify diagnostic details formatting cleans values."""
    formatted = format_diagnostic_details(
        action_type="type",
        key="sk-secret1234567890",
        raw_screenshot=b"\x00" * 100,
    )
    assert "action_type=type" in formatted
    assert "sk-secret1234567890" not in formatted
    assert "<bytes 100>" in formatted


# ── 6. Setup Logging & Verbosity Configuration ───────────────────────────────


def test_setup_logging_configurable_verbosity():
    """Verify setup_logging sets the expected log level from argument or environment."""
    stream = io.StringIO()

    # Configure as DEBUG
    logger_debug = setup_logging(log_level="DEBUG", stream=stream)
    assert logger_debug.level == logging.DEBUG

    # Configure as WARNING
    logger_warn = setup_logging(log_level="WARNING", stream=stream)
    assert logger_warn.level == logging.WARNING


# ── 7. RunStore Correlation ID Integration ───────────────────────────────────


@pytest.mark.asyncio
async def test_run_store_with_custom_and_default_correlation_id():
    """Verify RunStore.start assigns correlation ID and propagates to run dictionary."""
    store = RunStore()

    with patch.object(store, "_run_agent_task", new_callable=AsyncMock) as mock_agent_task:
        # Run with explicit correlation_id
        run_custom = store.start(
            goal="Test custom correlation ID",
            max_steps=5,
            correlation_id="custom-cid-12345",
        )
        assert run_custom["correlationId"] == "custom-cid-12345"
        assert run_custom["id"] != ""

        # Stop run so store is free
        store.stop(run_custom["id"])
        store._runs[run_custom["id"]]["status"] = "stopped"

        # Run with default correlation_id (should equal run_id)
        run_default = store.start(
            goal="Test default correlation ID",
            max_steps=5,
        )
        store.stop(run_default["id"])


# ── 8. File Logging & Destination Tests ──────────────────────────────────────


def test_setup_logging_with_file(tmp_path):
    """Verify setup_logging writes logs to a file when log_file is specified."""
    log_file = tmp_path / "test_run.log"
    logger = setup_logging(log_level="INFO", log_file=str(log_file), log_to_console=False)

    logger.info("Message written to log file")

    # Flush handlers
    for h in logger.handlers:
        h.flush()

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "[INFO]" in content
    assert "Message written to log file" in content


def test_setup_logging_file_and_console_mirroring(tmp_path):
    """Verify setup_logging can mirror to both file and console when requested."""
    log_file = tmp_path / "mirrored.log"
    stream = io.StringIO()

    logger = setup_logging(
        log_level="INFO",
        log_file=str(log_file),
        stream=stream,
        log_to_console=True,
    )

    logger.info("Mirrored log message")

    for h in logger.handlers:
        h.flush()

    assert "Mirrored log message" in log_file.read_text(encoding="utf-8")
    assert "Mirrored log message" in stream.getvalue()
