"""Agent decision loop — observe, decide, act, repeat.

Faithful port of ``scripts/mobile-agent/agent.mjs``'s ``runAgent()`` function.
The loop takes a device (``ADBDevice``) and a policy (``TypeSafePolicy``),
drives the observe→decide→act cycle, and returns a result dict.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable

from backend.services.staleness import StaleObservationError
from backend.services.actions import describe_action
from backend.services.input_verification import confirm_input, InputVerification


async def run_agent(
    *,
    device: Any,  # ADBDevice
    policy: Any,  # TypeSafePolicy
    goal: str,
    texts: list[str] | None = None,
    execute: bool = False,
    max_steps: int = 10,
    settle_ms: float = 0,
    settle_timeout_ms: float = 400,
    wait_timeout_ms: float = 15_000,
    input_timeout_ms: float = 2500,
    on_step: Callable[..., Awaitable[None]] | None = None,
    on_action: Callable[..., Awaitable[None]] | None = None,
    on_observation: Callable[..., Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Run the agent loop until completion, limit, or failure.

    Parameters match the JS ``runAgent()`` signature.  The result dict has
    the same shape: ``{status, steps, decision, observation, timings}``.
    """
    texts = texts or []

    if not isinstance(max_steps, int) or max_steps < 1 or max_steps > 100:
        raise ValueError("maxSteps must be 1–100.")
    for name, value in {
        "settleMs": settle_ms,
        "settleTimeoutMs": settle_timeout_ms,
        "inputTimeoutMs": input_timeout_ms,
    }.items():
        if not isinstance(value, (int, float)) or value < 0 or value > 10_000:
            raise ValueError(f"{name} must be 0–10000.")
    if not isinstance(wait_timeout_ms, (int, float)) or wait_timeout_ms < 0 or wait_timeout_ms > 60_000:
        raise ValueError("waitTimeoutMs must be 0–60000.")

    started = time.monotonic()
    timings = {
        "modelMs": 0.0,
        "actionMs": 0.0,
        "observationMs": 0.0,
        "waitMs": 0.0,
        "staleRetries": 0,
        "modelCalls": 0,
    }
    history: list[dict[str, Any]] = []
    repeated: set[str] = set()

    async def observe() -> dict[str, Any]:
        t0 = time.monotonic()
        try:
            result = await device.observe()
            # Normalise Pydantic Observation → plain dict so every consumer in
            # the loop can call .get() / JSON-serialise without type checks.
            if hasattr(result, "model_dump"):
                result = result.model_dump(by_alias=True, exclude={"screenshot"})
            return result
        finally:
            timings["observationMs"] += (time.monotonic() - t0) * 1000

    async def wait(ms: float) -> None:
        t0 = time.monotonic()
        await asyncio.sleep(ms / 1000)
        timings["waitMs"] += (time.monotonic() - t0) * 1000

    await device.assert_ready()

    async def _list_apps_or_empty() -> list:
        if hasattr(device, "list_apps"):
            return await device.list_apps()
        return []

    initial_obs, installed_apps = await asyncio.gather(
        observe(),
        _list_apps_or_empty(),
    )
    # Normalise Observation Pydantic model → plain dict so all downstream
    # code (.get(), JSON serialisation, fingerprint comparison) works uniformly
    # regardless of whether device.observe() returns a model or a raw dict.
    if hasattr(initial_obs, "model_dump"):
        initial_obs = initial_obs.model_dump(by_alias=True, exclude={"screenshot"})
    observation = initial_obs

    def finish(status: str, decision: dict[str, Any] | None = None) -> dict[str, Any]:
        wall_ms = (time.monotonic() - started) * 1000
        return {
            "status": status,
            "steps": len(history),
            "decision": decision,
            "observation": observation,
            "timings": {
                k: round(v, 1) if isinstance(v, float) else v
                for k, v in {**timings, "wallMs": wall_ms}.items()
            },
        }

    consecutive_waits = 0
    consecutive_stale = 0
    waiting_since: float | None = None

    for attempt in range(max_steps * 2 + 4):
        model_start = time.monotonic()
        decision = await policy.decide(
            goal=goal,
            observation=observation,
            history=history,
            texts=texts,
            apps=installed_apps,
        )
        timings["modelMs"] += (time.monotonic() - model_start) * 1000
        timings["modelCalls"] += 1

        step = len(history)
        if on_step:
            await on_step(step=step, attempt=attempt, **decision)

        if decision.get("status") != "action":
            return finish(decision["status"], decision)
        if not execute:
            return finish("preview", decision)
        if step >= max_steps:
            return finish("step_limit", decision)

        is_wait = decision.get("action", {}).get("type") == "wait"
        label = decision.get("label") or (
            "Wait for screen update"
            if is_wait
            else describe_action(decision["action"], observation)
        )

        signature = f"{observation.get('fingerprint')}:{json.dumps(decision['action'], sort_keys=True)}"
        if not is_wait and signature in repeated:
            return finish("stuck", decision)

        if is_wait:
            if waiting_since is None:
                waiting_since = time.monotonic()
            if (time.monotonic() - waiting_since) * 1000 >= wait_timeout_ms:
                return finish("loading_timeout", decision)
        else:
            waiting_since = None

        action_start = time.monotonic()
        stale = False
        receipt = None
        try:
            if is_wait:
                budget = min(
                    100 * (2 ** min(consecutive_waits, 4)),
                    1000,
                    max(0, wait_timeout_ms - (time.monotonic() - (waiting_since or 0)) * 1000),
                )
                await wait(budget)
            else:
                receipt = await device.act(decision["action"], expected=observation)
        except StaleObservationError:
            timings["staleRetries"] += 1
            stale = True
        finally:
            if not is_wait:
                timings["actionMs"] += (time.monotonic() - action_start) * 1000

        if stale:
            consecutive_stale += 1
            if consecutive_stale >= 3:
                return finish("unstable_screen", decision)
            observation = await observe()
            continue

        consecutive_stale = 0
        repeated.add(signature)
        consecutive_waits = consecutive_waits + 1 if is_wait else 0

        entry: dict[str, Any] = {
            "operation": decision.get("operation") or decision["action"]["type"],
            "label": label,
            "action": decision["action"],
            "before": observation.get("fingerprint"),
            "screenChanged": None,
        }
        if decision["action"].get("type") == "type":
            entry["text"] = decision["action"].get("text", "")
        history.append(entry)

        if on_action:
            await on_action(
                step=step,
                action=decision["action"],
                operation=entry["operation"],
                label=label,
                executedMs=round((time.monotonic() - started) * 1000),
            )

        if settle_ms:
            await wait(settle_ms)

        after = await observe()

        # Input verification polling.
        if receipt and isinstance(receipt, dict) and receipt.get("inputVerification"):
            iv = receipt["inputVerification"]
            confirmation = await confirm_input(
                initial=after,
                verification=iv,
                observe=observe,
                timeout_ms=input_timeout_ms,
            )
            after = confirmation.observation
            if not confirmation.verified:
                observation = after
                if on_observation:
                    await on_observation(
                        step=step,
                        observation=after,
                        screenChanged=entry["before"] != after.get("fingerprint"),
                    )
                return finish(
                    "input_unverified",
                    {
                        **decision,
                        "reason": (
                            "Text was sent, but its complete value could not be "
                            "confirmed in the input field. Inspect the screen "
                            "before retrying."
                        ),
                    },
                )

        # Wait for screen to settle (skip transient system-bar snapshots).
        wait_deadline = time.monotonic() + settle_timeout_ms / 1000
        while (
            not is_wait
            and not (receipt and isinstance(receipt, dict) and receipt.get("inputVerification"))
            and (
                after.get("fingerprint") == observation.get("fingerprint")
                or not after.get("phone", {}).get("packageName")
            )
            and time.monotonic() + 0.06 < wait_deadline
        ):
            await wait(60)
            after = await observe()

        entry["after"] = after.get("fingerprint")
        entry["screenChanged"] = observation.get("fingerprint") != after.get("fingerprint")

        if on_observation:
            await on_observation(
                step=step,
                observation=after,
                screenChanged=entry["screenChanged"],
            )
        observation = after

    return finish("decision_limit")
