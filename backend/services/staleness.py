"""Staleness validation — guard against acting on an outdated observation.

Faithful port of ``assertFresh()`` and ``targetMeaning()`` from
``scripts/mobile-agent/device.mjs``.
"""

from __future__ import annotations

import json
import time
from typing import Any


class StaleObservationError(Exception):
    """Raised when the screen changed between decision and execution."""


def _target_meaning(observation: dict[str, Any], element_id: str) -> str | None:
    """Return a JSON-serialised semantic fingerprint of the element subtree."""
    target = next(
        (e for e in observation["elements"] if e["id"] == element_id), None
    )
    if target is None:
        return None
    meaning = {k: v for k, v in target.items() if k != "bounds"}
    content = []
    prefix = element_id + "."
    for e in observation["elements"]:
        if e["id"].startswith(prefix):
            content.append(
                {
                    "id": e["id"],
                    "text": e.get("text", ""),
                    "label": e.get("label", ""),
                    "resourceId": e.get("resourceId", ""),
                    "editable": e.get("editable", False),
                    "enabled": e.get("enabled", True),
                    "checked": e.get("checked", False),
                    "selected": e.get("selected", False),
                }
            )
    meaning["content"] = content
    return json.dumps(meaning, sort_keys=True)


def assert_fresh(
    current: dict[str, Any],
    expected: dict[str, Any],
    action: dict[str, Any],
    max_age_ms: float = 30_000,
) -> None:
    """Raise ``StaleObservationError`` if *current* diverges from *expected*.

    Per-action-type checks mirror the original JavaScript implementation.
    """
    now_ms = time.time() * 1000
    observed_at = expected.get("observedAt")

    if (
        not expected
        or current.get("deviceId") != expected.get("deviceId")
        or not isinstance(observed_at, (int, float))
        or not _finite(observed_at)
        or now_ms - observed_at > max_age_ms
        or observed_at > now_ms
        or current.get("phone", {}).get("packageName")
        != expected.get("phone", {}).get("packageName")
        or json.dumps(current.get("screen"), sort_keys=True)
        != json.dumps(expected.get("screen"), sort_keys=True)
    ):
        raise StaleObservationError(
            "Screen changed or observation expired; observe and decide again."
        )

    action_type = action.get("type")
    fresh: bool

    if action_type == "tap-element":
        eid = action["elementId"]
        fresh = (
            _target_meaning(expected, eid) is not None
            and _target_meaning(current, eid) == _target_meaning(expected, eid)
        )

    elif action_type in ("type", "clear", "key") and expected.get("phone", {}).get(
        "inputElementId"
    ):
        eid = expected["phone"]["inputElementId"]
        fresh = (
            current.get("phone", {}).get("isEditable")
            and current.get("phone", {}).get("inputElementId") == eid
            and _target_meaning(current, eid) == _target_meaning(expected, eid)
        )

    elif action_type == "global" and action.get("name") == "home":
        # HOME is independent of in-app content.
        fresh = True

    elif action_type == "global":
        fresh = _navigation_meaning(current) == _navigation_meaning(expected)

    elif action_type == "swipe" and action.get("regionId"):
        rid = action["regionId"]
        before = next(
            (e for e in expected["elements"] if e["id"] == rid), None
        )
        after = next(
            (e for e in current["elements"] if e["id"] == rid), None
        )
        fresh = bool(
            before
            and after
            and after.get("enabled")
            and after.get("scrollable")
            and before.get("resourceId") == after.get("resourceId")
        )

    else:
        fresh = current.get("fingerprint") == expected.get("fingerprint")

    if not fresh:
        raise StaleObservationError(
            "Screen changed or observation expired; observe and decide again."
        )


# ── helpers ──────────────────────────────────────────────────────────────


def _finite(n: Any) -> bool:
    return isinstance(n, (int, float)) and n == n and abs(n) != float("inf")


def _navigation_meaning(state: dict[str, Any]) -> str:
    phone = state.get("phone", {})
    controls = []
    headings = []
    for e in state.get("elements", []):
        if e.get("clickable") or e.get("editable"):
            controls.append(_target_meaning(state, e["id"]))
        rid = e.get("resourceId", "")
        if rid.endswith(":id/title") or e.get("label"):
            headings.append([e["id"], e.get("text", ""), e.get("label", "")])
    return json.dumps(
        {"phone": phone, "controls": controls, "headings": headings},
        sort_keys=True,
    )
