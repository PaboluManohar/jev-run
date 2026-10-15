"""Decision endpoint — ask TypeSafe System One to select the next action.

``POST /decision`` accepts the current task, an observation, and a list of
available actions.  It delegates to :mod:`backend.services.decision_model` and
returns a structured :class:`~backend.models.decision.DecisionResponse`.

No action is executed here — this endpoint is purely inferential.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.models.decision import DecisionRequest, DecisionResponse
from backend.services.decision_model import (
    DecisionModelError,
    DecisionModelService,
    InvalidResponseError,
    MissingConfigError,
    get_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["decision"])


@router.post(
    "/decision",
    response_model=DecisionResponse,
    summary="Ask System One to select the next action",
    description=(
        "Sends the current task, observation, and available actions to TypeSafe "
        "System One and returns the selected action ID or a task-completion signal.  "
        "Actions are **not** executed — this endpoint is purely inferential."
    ),
)
async def post_decision(
    body: DecisionRequest,
    service: DecisionModelService = Depends(get_service),
) -> DecisionResponse:
    """Select the next action for the agent to execute.

    Args:
        body: Task, observation, and action list.
        service: Application-scoped decision-model service (injected).

    Returns:
        :class:`~backend.models.decision.DecisionResponse` with the chosen
        action ID (or ``done=True`` for task completion).

    Raises:
        ``503`` when TypeSafe configuration is missing or authentication fails.
        ``502`` on SDK / network errors (the API key is never exposed).
        ``422`` when System One returns an unexpected action ID.
    """
    try:
        return await service.decide(
            task=body.task,
            observation=body.observation,
            actions=body.actions,
        )
    except MissingConfigError as exc:
        logger.error("TypeSafe configuration error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "TypeSafe API is not configured.  "
                "Ensure TYPESAFE_API_KEY is set in the environment."
            ),
        ) from exc
    except InvalidResponseError as exc:
        logger.error("Invalid System One response: %s", exc)
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    except DecisionModelError as exc:
        # Scrubbing already happened in _safe_raise(); safe to surface.
        logger.error("Decision model error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc
