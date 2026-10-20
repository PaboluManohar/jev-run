"""In-memory run state management with asyncio task execution.

Replaces ``apps/jev-studio/server/store.mjs`` — instead of spawning
``web-runner.mjs`` as a child process with JSONL stdin/stdout, the FastAPI
backend runs the agent loop directly as an ``asyncio.Task``.

The pub/sub interface is preserved for SSE streaming to the frontend.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any, Callable
from uuid import uuid4

from backend.config import get_settings
from backend.services.adb_device import ADBDevice
from backend.services.policy import TypeSafePolicy
from backend.services.agent import run_agent
from backend.services.run_logger import RunContext

logger = logging.getLogger(__name__)


class StudioError(Exception):
    """Application-level error with an HTTP status code."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class RunStore:
    """Manage agent task runs with pub/sub event notification."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._listeners: dict[str, set[Callable]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._settings = get_settings()

    def _snapshot(self, run_id: str) -> dict[str, Any] | None:
        run = self._runs.get(run_id)
        return copy.deepcopy(run) if run else None

    def _publish(self, run_id: str) -> None:
        snapshot = self._snapshot(run_id)
        if not snapshot:
            return
        dead: list[Callable] = []
        for cb in self._listeners.get(run_id, set()):
            try:
                cb(snapshot)
            except Exception:
                dead.append(cb)
        for cb in dead:
            self._listeners.get(run_id, set()).discard(cb)

    def _active(self) -> dict[str, Any] | None:
        return next(
            (r for r in self._runs.values() if r["status"] in ("running", "stopping")),
            None,
        )

    # ── Public API ───────────────────────────────────────────────────────

    def list(self) -> list[dict[str, Any]]:
        return [
            self._snapshot(r["id"])
            for r in reversed(list(self._runs.values()))
            if self._snapshot(r["id"]) is not None
        ]

    def get(self, run_id: str) -> dict[str, Any] | None:
        return self._snapshot(run_id)

    def clear(self) -> dict[str, int]:
        if self._active():
            raise StudioError("Stop the active task before clearing recent runs.", 409)
        cleared = len(self._runs)
        self._runs.clear()
        self._listeners.clear()
        return {"cleared": cleared}

    def subscribe(self, run_id: str, callback: Callable) -> Callable[[], None]:
        if run_id not in self._runs:
            raise StudioError("Run not found.", 404)
        self._listeners.setdefault(run_id, set()).add(callback)

        def unsubscribe() -> None:
            self._listeners.get(run_id, set()).discard(callback)

        return unsubscribe

    def start(
        self,
        goal: str,
        max_steps: int = 30,
        device_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new run and launch the agent as an asyncio task."""
        if not isinstance(goal, str) or not goal.strip() or len(goal) > self._settings.max_goal_length:
            raise StudioError(
                f"Enter a goal between 1 and {self._settings.max_goal_length:,} characters."
            )
        if not isinstance(max_steps, int) or max_steps < 1 or max_steps > self._settings.max_steps_limit:
            raise StudioError(
                f"The step limit must be between 1 and {self._settings.max_steps_limit}."
            )
        if self._active():
            raise StudioError("A task is already running on this device.", 409)

        # Evict oldest runs to stay under the limit.
        while len(self._runs) >= self._settings.max_runs:
            oldest_id = next(iter(self._runs))
            del self._runs[oldest_id]
            self._listeners.pop(oldest_id, None)

        run_id = str(uuid4())
        cid = correlation_id or run_id
        now_ms = time.time() * 1000
        run: dict[str, Any] = {
            "id": run_id,
            "correlationId": cid,
            "goal": goal.strip(),
            "maxSteps": max_steps,
            "deviceId": device_id or self._settings.adb_device_serial,
            "status": "running",
            "startedAt": now_ms,
            "endedAt": None,
            "events": [],
            "outcome": None,
            "error": None,
        }
        self._runs[run_id] = run

        task = asyncio.create_task(self._run_agent_task(run_id))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))

        return self._snapshot(run_id)  # type: ignore[return-value]

    def stop(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if not run:
            raise StudioError("Run not found.", 404)
        task = self._tasks.get(run_id)
        if not task or run["endedAt"] is not None:
            return self._snapshot(run_id)  # type: ignore[return-value]

        run["status"] = "stopping"
        self._publish(run_id)
        task.cancel()

        return self._snapshot(run_id)  # type: ignore[return-value]

    # ── Internal agent execution ─────────────────────────────────────────

    async def _run_agent_task(self, run_id: str) -> None:
        """Execute the agent loop and update the run state with events."""
        run = self._runs.get(run_id)
        if not run:
            return

        settings = self._settings
        outcome: str | None = None
        cid = run.get("correlationId") or run_id

        def _finish(status: str, message: str | None = None) -> None:
            if run["endedAt"] is not None:
                return
            run["status"] = status
            run["endedAt"] = time.time() * 1000
            if message:
                run["error"] = message
            self._publish(run_id)

        async with RunContext(
            correlation_id=cid,
            logger=logger,
            run_id=run_id,
            goal=run["goal"],
            max_steps=run["maxSteps"],
            device_id=run.get("deviceId"),
        ):
            try:
                target_serial = run.get("deviceId") or settings.adb_device_serial
                device = ADBDevice(
                    serial=target_serial,
                    adb_path=settings.adb_path,
                    text_completion_mode=settings.text_completion_mode,
                )
                policy = TypeSafePolicy(
                    api_key=settings.typesafe_api_key,
                    model=settings.typesafe_default_model or settings.typesafe_model,
                )

                async def on_step(**kwargs: Any) -> None:
                    event = {
                        "type": "decision",
                        "at": time.time() * 1000,
                        "step": kwargs.get("step"),
                        "attempt": kwargs.get("attempt"),
                        "status": kwargs.get("status"),
                        "operation": kwargs.get("operation"),
                        "label": kwargs.get("label"),
                        "confidence": kwargs.get("confidence"),
                        "targetConfidence": kwargs.get("targetConfidence"),
                        "latencyMs": kwargs.get("latencyMs"),
                        "model": kwargs.get("responseModel"),
                        "reason": kwargs.get("reason"),
                        "sequence": len(run["events"]),
                    }
                    run["events"].append(event)
                    self._publish(run_id)

                async def on_action(**kwargs: Any) -> None:
                    event = {
                        "type": "action",
                        "at": time.time() * 1000,
                        "step": kwargs.get("step"),
                        "operation": kwargs.get("operation"),
                        "label": kwargs.get("label"),
                        "executedMs": kwargs.get("executedMs"),
                        "sequence": len(run["events"]),
                    }
                    run["events"].append(event)
                    self._publish(run_id)

                result = await run_agent(
                    device=device,
                    policy=policy,
                    goal=run["goal"],
                    execute=True,
                    max_steps=run["maxSteps"],
                    on_step=on_step,
                    on_action=on_action,
                )

                outcome = result.get("status")
                run["outcome"] = outcome

                # Emit result event.
                result_event = {
                    "type": "result",
                    "at": time.time() * 1000,
                    "outcome": outcome,
                    "steps": result.get("steps"),
                    "reason": result.get("decision", {}).get("reason") if result.get("decision") else None,
                    "sequence": len(run["events"]),
                }
                run["events"].append(result_event)
                self._publish(run_id)

            except asyncio.CancelledError:
                _finish("stopped")
                return
            except Exception as exc:
                error_event = {
                    "type": "error",
                    "at": time.time() * 1000,
                    "message": str(exc),
                    "sequence": len(run["events"]),
                }
                run["events"].append(error_event)
                run["error"] = str(exc)
                _finish("failed", str(exc))
                return

            if run["status"] == "stopping":
                _finish("stopped")
            elif run["error"] or not outcome:
                _finish("failed", run["error"] or "The task ended unexpectedly.")
            elif outcome == "done":
                _finish("succeeded")
            else:
                _finish("blocked")


# ── Singleton ────────────────────────────────────────────────────────────

_store: RunStore | None = None


def get_store() -> RunStore:
    """Return the global singleton RunStore."""
    global _store
    if _store is None:
        _store = RunStore()
    return _store
