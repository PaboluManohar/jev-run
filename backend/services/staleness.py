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


def _normalize(val: Any) -> Any:
    """Recursively convert Pydantic models to dicts (using aliases) or primitives."""
    if val is None:
        return None
    if hasattr(val, "model_dump"):
        return _normalize(val.model_dump(by_alias=True))
    if isinstance(val, dict):
        return {k: _normalize(v) for k, v in val.items()}
    if isinstance(val, (list, tuple, set)):
        return [_normalize(item) for item in val]
    return val


def _target_meaning(observation: Any, element_id: str) -> str | None:
    """Return a JSON-serialised semantic fingerprint of the element subtree."""
    obs = _normalize(observation)
    elements = obs.get("elements", []) if isinstance(obs, dict) else []
    target = next(
        (e for e in elements if e.get("id") == element_id), None
    )
    if target is None:
        return None
    meaning = {k: v for k, v in target.items() if k != "bounds"}
    content = []
    prefix = element_id + "."
    for e in elements:
        eid = e.get("id", "")
        if eid.startswith(prefix):
            content.append(
                {
                    "id": eid,
                    "text": e.get("text", ""),
                    "label": e.get("label", ""),
                    "resourceId": e.get("resourceId") or e.get("resource_id", ""),
                    "editable": e.get("editable", False),
                    "enabled": e.get("enabled", True),
                    "checked": e.get("checked", False),
                    "selected": e.get("selected", False),
                }
            )
    meaning["content"] = content
    return json.dumps(meaning, sort_keys=True)


def assert_fresh(
    current: Any,
    expected: Any,
    action: dict[str, Any],
    max_age_ms: float = 30_000,
) -> None:
    """Raise ``StaleObservationError`` if *current* diverges from *expected*.

    Per-action-type checks mirror the original JavaScript implementation.
    """
    if not expected:
        raise StaleObservationError(
            "Screen changed or observation expired; observe and decide again."
        )

    current_norm = _normalize(current) or {}
    expected_norm = _normalize(expected) or {}

    now_ms = time.time() * 1000
    observed_at = expected_norm.get("observedAt")
    if observed_at is None:
        observed_at = expected_norm.get("observed_at")

    curr_device = current_norm.get("deviceId") or current_norm.get("device_id")
    exp_device = expected_norm.get("deviceId") or expected_norm.get("device_id")

    curr_phone = current_norm.get("phone") or {}
    exp_phone = expected_norm.get("phone") or {}
    curr_pkg = curr_phone.get("packageName") or curr_phone.get("package_name")
    exp_pkg = exp_phone.get("packageName") or exp_phone.get("package_name")

    if (
        curr_device != exp_device
        or not isinstance(observed_at, (int, float))
        or not _finite(observed_at)
        or now_ms - observed_at > max_age_ms
        or observed_at > now_ms
        or curr_pkg != exp_pkg
        or json.dumps(current_norm.get("screen"), sort_keys=True)
        != json.dumps(expected_norm.get("screen"), sort_keys=True)
    ):
        raise StaleObservationError(
            "Screen changed or observation expired; observe and decide again."
        )

    action_type = action.get("type")
    fresh: bool

    if action_type == "tap-element":
        eid = action["elementId"]
        fresh = (
            _target_meaning(expected_norm, eid) is not None
            and _target_meaning(current_norm, eid) == _target_meaning(expected_norm, eid)
        )

    elif action_type in ("type", "clear", "key"):
        exp_input_id = exp_phone.get("inputElementId") or exp_phone.get("input_element_id")
        if exp_input_id:
            curr_is_editable = curr_phone.get("isEditable") or curr_phone.get("is_editable")
            curr_input_id = curr_phone.get("inputElementId") or curr_phone.get("input_element_id")
            fresh = (
                bool(curr_is_editable)
                and curr_input_id == exp_input_id
                and _target_meaning(current_norm, exp_input_id)
                == _target_meaning(expected_norm, exp_input_id)
            )
        else:
            fresh = current_norm.get("fingerprint") == expected_norm.get("fingerprint")

    elif action_type == "global" and action.get("name") == "home":
        # HOME is independent of in-app content.
        fresh = True

    elif action_type == "global":
        fresh = _navigation_meaning(current_norm) == _navigation_meaning(expected_norm)

    elif action_type == "swipe" and action.get("regionId"):
        rid = action["regionId"]
        elements_exp = expected_norm.get("elements", [])
        elements_curr = current_norm.get("elements", [])
        before = next((e for e in elements_exp if e.get("id") == rid), None)
        after = next((e for e in elements_curr if e.get("id") == rid), None)
        fresh = bool(
            before
            and after
            and after.get("enabled")
            and after.get("scrollable")
            and (before.get("resourceId") or before.get("resource_id"))
            == (after.get("resourceId") or after.get("resource_id"))
        )

    else:
        fresh = current_norm.get("fingerprint") == expected_norm.get("fingerprint")

    if not fresh:
        raise StaleObservationError(
            "Screen changed or observation expired; observe and decide again."
        )


# ── helpers ──────────────────────────────────────────────────────────────


def _finite(n: Any) -> bool:
    return isinstance(n, (int, float)) and n == n and abs(n) != float("inf")


def _navigation_meaning(state: Any) -> str:
    obs = _normalize(state) or {}
    phone = obs.get("phone", {})
    elements = obs.get("elements", [])
    controls = []
    headings = []
    for e in elements:
        if e.get("clickable") or e.get("editable"):
            controls.append(_target_meaning(obs, e.get("id", "")))
        rid = e.get("resourceId") or e.get("resource_id", "")
        label = e.get("label", "")
        text = e.get("text", "")
        if (rid and rid.endswith(":id/title")) or label:
            headings.append([e.get("id", ""), text, label])
    return json.dumps(
        {"phone": phone, "controls": controls, "headings": headings},
        sort_keys=True,
    )
