"""TypeSafe System One decision-model service.

Wraps the ``typesafe-sdk`` ``AsyncTypeSafeClient`` so the FastAPI application
can ask System One to select one of a set of available action IDs (or signal
task completion) given a free-text task and a current environment observation.

Lifecycle
---------
The client is created once per application startup (via ``init_client`` in the
FastAPI lifespan) and torn down cleanly on shutdown (via ``close_client``).
Use the ``get_service`` FastAPI dependency to obtain the shared instance.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, TypeSafeError

from backend.config import Settings, get_settings
from backend.models.decision import ActionOption, DecisionResponse

logger = logging.getLogger(__name__)

# Sentinel key added to the Choice criteria so the model can signal "done".
_DONE_KEY = "__done__"


class DecisionModelError(Exception):
    """Raised when the decision service encounters a non-recoverable error."""


class MissingConfigError(DecisionModelError):
    """Raised when required TypeSafe configuration is absent."""


class InvalidResponseError(DecisionModelError):
    """Raised when System One returns a choice that is not in the supplied set."""


# ---------------------------------------------------------------------------
# Application-scoped singleton
# ---------------------------------------------------------------------------

_service_instance: DecisionModelService | None = None


def get_service() -> "DecisionModelService":
    """FastAPI dependency — returns the application-scoped service.

    Raises ``MissingConfigError`` if the service has not been initialised yet
    (i.e., ``init_client`` was never called during lifespan startup).
    """
    if _service_instance is None:
        raise MissingConfigError(
            "DecisionModelService has not been initialised.  "
            "Call init_client() during application startup."
        )
    return _service_instance


async def init_client(settings: Settings | None = None) -> "DecisionModelService":
    """Create and store the application-scoped :class:`DecisionModelService`.

    Should be called exactly once during the FastAPI lifespan ``startup``
    phase.  Subsequent calls replace the existing instance and close the old
    client gracefully.

    Args:
        settings: Optional settings override (useful in tests).  If *None*,
            the global ``get_settings()`` cache is used.

    Returns:
        The newly created :class:`DecisionModelService` instance.

    Raises:
        ``MissingConfigError`` when ``TYPESAFE_API_KEY`` is empty.
    """
    global _service_instance

    cfg = settings or get_settings()
    if not cfg.typesafe_api_key:
        raise MissingConfigError(
            "TYPESAFE_API_KEY is not configured.  "
            "Set it in your environment or .env file."
        )

    if _service_instance is not None:
        await _service_instance.close()

    service = DecisionModelService(settings=cfg)
    _service_instance = service
    logger.info(
        "DecisionModelService initialised (model=%s)",
        cfg.typesafe_default_model,
    )
    return service


async def close_client() -> None:
    """Tear down the application-scoped client gracefully.

    Safe to call even if ``init_client`` was never invoked (no-op).
    """
    global _service_instance

    if _service_instance is not None:
        await _service_instance.close()
        _service_instance = None
        logger.info("DecisionModelService closed.")


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class DecisionModelService:
    """Wraps :class:`AsyncTypeSafeClient` to implement action-selection decisions.

    Args:
        settings: Populated :class:`~backend.config.Settings` instance.

    Instantiation does not open any network connection.  The underlying httpx
    client is opened lazily on the first call to :meth:`decide` and kept alive
    for the lifetime of the service.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Pass base_url only when explicitly overridden; the SDK has its own
        # default so we must not pass an empty string.
        base_url_kwarg: dict[str, Any] = {}
        if settings.typesafe_base_url:
            base_url_kwarg["base_url"] = settings.typesafe_base_url

        self._client = AsyncTypeSafeClient(
            api_key=settings.typesafe_api_key,
            model=settings.typesafe_default_model,
            **base_url_kwarg,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client gracefully."""
        await self._client.__aexit__(None, None, None)

    async def decide(
        self,
        *,
        task: str,
        observation: dict[str, Any],
        actions: list[ActionOption],
    ) -> DecisionResponse:
        """Ask System One to pick one of the supplied actions or signal completion.

        The model is presented with a :class:`~typesafe_sdk.Choice` question
        whose criteria are the available action IDs plus a synthetic
        ``__done__`` key that represents task completion.  A
        :class:`~typesafe_sdk.Noul` question gauges whether the task is already
        finished before any action is needed.

        Args:
            task: Natural-language goal the agent is pursuing.
            observation: Current environment state (UI elements, screen, etc.)
                passed verbatim as the System One *state*.
            actions: Non-empty list of :class:`~backend.models.decision.ActionOption`
                instances the model may choose from.

        Returns:
            A :class:`~backend.models.decision.DecisionResponse` with the
            selected action ID (``None`` when ``done`` is ``True``) plus
            confidence and latency metadata.

        Raises:
            ``MissingConfigError``: API key not set.
            ``InvalidResponseError``: Model returned an action ID not in the
                supplied list.
            ``DecisionModelError``: Any SDK / network failure that is not
                separately typed.
        """
        if not task.strip():
            raise ValueError("task must be a non-empty string.")
        if not actions:
            raise ValueError("At least one action must be supplied.")

        # Build criteria: action_id → human-readable description.
        # Also include __done__ so the model can signal completion.
        action_ids = {a.id for a in actions}
        criteria: dict[str, str | None] = {
            a.id: a.description for a in actions
        }
        criteria[_DONE_KEY] = (
            "The task is already complete — no further action is required."
        )

        state: dict[str, Any] = {
            "task": task,
            **observation,
        }

        questions = {
            "action": Choice(
                instructions=(
                    "You are a mobile-device automation agent.  "
                    "Given the current observation and the task, choose the single "
                    "best action to take next, or choose '__done__' if the task is "
                    "already fully accomplished.  Choose ONLY from the offered action IDs."
                ),
                criteria=criteria,
            ),
            "task_complete": Noul(
                instructions=(
                    "Is the task already fully accomplished based on the current "
                    "observation?  Answer yes (noul → 1) only if there is clear "
                    "evidence that all requirements are satisfied."
                ),
            ),
        }

        t0 = time.monotonic()
        try:
            response = await self._client.system_one(
                state=state,
                questions=questions,
            )
        except TypeSafeError as exc:
            # Never expose the API key; scrub it from exception messages.
            _safe_raise(exc, self._settings.typesafe_api_key)

        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        # ── Extract & validate the choice answer ──────────────────────────
        action_answer = response.choices.get("action")
        if action_answer is None:
            raise InvalidResponseError(
                "System One did not return a 'action' choice answer."
            )

        chosen = action_answer.choice
        task_complete_noul = response.nouls.get("task_complete")
        task_complete_prob = task_complete_noul.noul if task_complete_noul else 0.0

        # Treat __done__ choice OR high task_complete noul (≥ 0.9) as done.
        if chosen == _DONE_KEY or task_complete_prob >= 0.9:
            return DecisionResponse(
                selected_action_id=None,
                done=True,
                confidence=action_answer.confidence,
                probabilities={
                    k: v
                    for k, v in action_answer.probabilities.items()
                    if k != _DONE_KEY
                },
                model=response.model,
                latency_ms=latency_ms,
            )

        # Validate: the chosen ID must be one of the supplied action IDs.
        if chosen not in action_ids:
            raise InvalidResponseError(
                f"System One returned action ID {chosen!r} which is not in the "
                f"supplied action list: {sorted(action_ids)!r}"
            )

        return DecisionResponse(
            selected_action_id=chosen,
            done=False,
            confidence=action_answer.confidence,
            probabilities={
                k: v
                for k, v in action_answer.probabilities.items()
                if k != _DONE_KEY
            },
            model=response.model,
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_raise(exc: TypeSafeError, api_key: str) -> None:
    """Re-raise *exc* after scrubbing the API key from its string representation.

    This guarantees we never leak the key into logs or HTTP responses.
    """
    msg = str(exc)
    if api_key:
        msg = msg.replace(api_key, "[REDACTED]")

    from typesafe_sdk import (  # noqa: PLC0415
        TypeSafeAPITimeoutError,
        TypeSafeAPIConnectionError,
        TypeSafeAuthenticationError,
    )

    if isinstance(exc, TypeSafeAPITimeoutError):
        raise DecisionModelError(f"TypeSafe API timed out: {msg}") from exc
    if isinstance(exc, TypeSafeAuthenticationError):
        raise MissingConfigError(
            f"TypeSafe API authentication failed — check TYPESAFE_API_KEY: {msg}"
        ) from exc
    if isinstance(exc, TypeSafeAPIConnectionError):
        raise DecisionModelError(f"TypeSafe API connection error: {msg}") from exc

    raise DecisionModelError(f"TypeSafe API error: {msg}") from exc
