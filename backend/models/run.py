"""Pydantic models for task runs and their events."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Event(BaseModel):
    """A single event emitted during an agent run."""

    type: str  # decision | action | result | error
    at: float
    step: int | None = None
    attempt: int | None = None
    operation: str | None = None
    label: str | None = None
    confidence: float | None = None
    target_confidence: float | None = Field(None, alias="targetConfidence")
    latency_ms: float | None = Field(None, alias="latencyMs")
    model: str | None = None
    outcome: str | None = None
    reason: str | None = None
    sequence: int = 0

    model_config = {"populate_by_name": True}


class Run(BaseModel):
    """Full state of a single agent task run."""

    id: str
    goal: str
    max_steps: int = Field(30, alias="maxSteps")
    status: str  # running | stopping | stopped | succeeded | blocked | failed
    started_at: float = Field(0.0, alias="startedAt")
    ended_at: float | None = Field(None, alias="endedAt")
    events: list[Event] = Field(default_factory=list)
    outcome: str | None = None
    error: str | None = None

    model_config = {"populate_by_name": True}


class RunRequest(BaseModel):
    """Payload for ``POST /runs``."""

    goal: str
    max_steps: int = Field(30, alias="maxSteps")

    model_config = {"populate_by_name": True}
