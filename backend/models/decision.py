"""Pydantic models for the TypeSafe System One decision endpoint."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ActionOption(BaseModel):
    """A single action the model may choose from."""

    id: str = Field(..., description="Unique action identifier, e.g. 'tap_login_button'.")
    description: str = Field(
        ...,
        description="Human-readable description of what this action does.",
    )


class DecisionRequest(BaseModel):
    """Payload for ``POST /decision``."""

    task: str = Field(
        ...,
        min_length=1,
        description="The user-supplied goal or task the agent is trying to accomplish.",
    )
    observation: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Current environment observation, e.g. visible UI elements, "
            "current screen, phone state.  Passed verbatim as the System One state."
        ),
    )
    actions: list[ActionOption] = Field(
        ...,
        min_length=1,
        description="Ordered list of available action options the model may choose from.",
    )

    model_config = {"populate_by_name": True}


class DecisionResponse(BaseModel):
    """Structured response returned by ``POST /decision``."""

    selected_action_id: str | None = Field(
        None,
        alias="selectedActionId",
        description=(
            "The ID of the action selected by System One, or ``null`` when "
            "``done`` is ``true``."
        ),
    )
    done: bool = Field(
        False,
        description=(
            "True when the model indicates the task is already complete "
            "and no further action is needed."
        ),
    )
    confidence: float | None = Field(
        None,
        description="Model confidence in the selected choice, 0–1.",
    )
    probabilities: dict[str, float] | None = Field(
        None,
        description="Per-action probability distribution returned by System One.",
    )
    model: str | None = Field(
        None,
        description="Model identifier actually used by the TypeSafe API.",
    )
    latency_ms: float | None = Field(
        None,
        alias="latencyMs",
        description="End-to-end SDK round-trip latency in milliseconds.",
    )

    model_config = {"populate_by_name": True}
