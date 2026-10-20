"""Input verification — confirm typed text actually landed in the field.

Faithful port of ``scripts/mobile-agent/input-verification.mjs``.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass
class InputVerification:
    device_id: str
    package_name: str
    target: dict[str, Any]
    text: str


@dataclass
class ConfirmResult:
    verified: bool
    observation: dict[str, Any]


def prepare_input_verification(
    observation: dict[str, Any],
    action: dict[str, Any],
) -> InputVerification | None:
    """Build a verification descriptor before executing a ``type`` action.

    Returns ``None`` when the action cannot be locally verified (non-clear
    type, password field, etc.).  Mirrors the JS ``prepareInputVerification``.
    """
    if action.get("type") != "type" or action.get("clear") is not True:
        return None

    phone = observation.get("phone", {})
    input_eid = phone.get("inputElementId")
    if not input_eid:
        return None

    target = next(
        (
            e
            for e in observation.get("elements", [])
            if e["id"] == input_eid
        ),
        None,
    )
    if not target or not target.get("editable") or not target.get("enabled"):
        return None
    if target.get("password"):
        return None

    return InputVerification(
        device_id=observation.get("deviceId", ""),
        package_name=phone.get("packageName", ""),
        target={
            "id": target["id"],
            "resourceId": target.get("resourceId", ""),
            "hint": target.get("hint", ""),
            "bounds": target["bounds"],
        },
        text=action["text"],
    )


def input_matches(
    observation: dict[str, Any],
    verification: InputVerification,
) -> bool:
    """Check whether the field now contains the expected text."""
    if observation.get("deviceId") != verification.device_id:
        return False
    if (
        observation.get("phone", {}).get("packageName")
        != verification.package_name
    ):
        return False

    inputs = [
        e
        for e in observation.get("elements", [])
        if e.get("editable") and e.get("enabled") and not e.get("password")
    ]
    t = verification.target

    if t.get("resourceId"):
        candidates = [e for e in inputs if e.get("resourceId") == t["resourceId"]]
        if len(candidates) > 1:
            candidates = [e for e in candidates if e["id"] == t["id"]]
    else:
        candidates = [
            e
            for e in inputs
            if e["id"] == t["id"]
            and e.get("hint") == t.get("hint")
            and (
                json.dumps(e["bounds"].model_dump() if hasattr(e["bounds"], "model_dump") else e["bounds"], sort_keys=True)
                == json.dumps(t["bounds"].model_dump() if hasattr(t["bounds"], "model_dump") else t["bounds"], sort_keys=True)
                or e.get("focused")
            )
        ]
        if not candidates:
            focused_candidates = [e for e in inputs if e.get("focused") and e.get("text") == verification.text]
            if len(focused_candidates) == 1:
                candidates = focused_candidates

    return len(candidates) == 1 and candidates[0].get("text") == verification.text


async def confirm_input(
    *,
    initial: dict[str, Any],
    verification: InputVerification,
    observe: Callable[[], Awaitable[dict[str, Any]]],
    timeout_ms: float = 2500,
    poll_ms: float = 60,
) -> ConfirmResult:
    """Poll until the typed text appears in the field, or timeout."""
    if timeout_ms < 0 or poll_ms < 1:
        raise ValueError("Invalid input verification timing.")

    deadline = time.monotonic() + timeout_ms / 1000
    observation = initial

    while not input_matches(observation, verification):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ConfirmResult(verified=False, observation=observation)
        await asyncio.sleep(min(poll_ms / 1000, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ConfirmResult(verified=False, observation=observation)
        try:
            observation = await asyncio.wait_for(observe(), timeout=remaining)
        except (asyncio.TimeoutError, TimeoutError):
            return ConfirmResult(verified=False, observation=observation)

    return ConfirmResult(verified=True, observation=observation)
