"""Run management and SSE event streaming endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from backend.models.run import RunRequest
from backend.services.store import StudioError, get_store

router = APIRouter(tags=["runs"])


@router.get("/runs")
async def list_runs():
    """Return all recent runs."""
    return get_store().list()


@router.post("/runs", status_code=201)
async def start_run(body: RunRequest):
    """Create and start a new agent run."""
    try:
        device_id = body.device_id or body.udid
        return get_store().start(body.goal, body.max_steps, device_id=device_id)
    except StudioError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.post("/runs/clear")
async def clear_runs():
    """Clear all recent runs (no active task must be running)."""
    try:
        return get_store().clear()
    except StudioError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    """Return a single run by ID."""
    run = get_store().get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str):
    """Stop a running task."""
    try:
        return get_store().stop(run_id)
    except StudioError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request):
    """Stream run state updates as Server-Sent Events.

    The frontend connects via ``EventSource`` and receives the full
    ``Run`` dict on every state change + a periodic heartbeat.
    """
    store = get_store()
    run = store.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    async def event_generator() -> AsyncGenerator[dict, None]:
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        def on_update(state: dict) -> None:
            queue.put_nowait(state)

        unsubscribe = store.subscribe(run_id, on_update)

        try:
            # Send the current state immediately.
            yield {"data": json.dumps(run)}

            while True:
                # Check if client disconnected.
                if await request.is_disconnected():
                    break

                try:
                    state = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if state is None:
                        break
                    yield {"data": json.dumps(state)}
                except asyncio.TimeoutError:
                    # Heartbeat to keep the connection alive.
                    yield {"comment": "heartbeat"}

                # If the run is no longer active, send final state and close.
                current = store.get(run_id)
                if current and current.get("status") not in ("running", "stopping"):
                    yield {"data": json.dumps(current)}
                    break
        finally:
            unsubscribe()

    return EventSourceResponse(event_generator())
