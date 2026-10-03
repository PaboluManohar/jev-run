"""Candidate action discovery and human-readable action descriptions.

Faithful port of ``scripts/mobile-agent/actions.mjs``.  Given a summarised
observation, ``candidates_for()`` enumerates every operation the agent could
attempt on the current screen.
"""

from __future__ import annotations

import json
import math
from typing import Any


def candidates_for(
    observation: dict[str, Any],
    texts: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the action candidate map from an observation dict.

    The observation must have the shape produced by ``summarize_state()`` (or
    the Pydantic ``Observation`` model serialised with ``by_alias=True``).
    """
    texts = texts or []
    actions: dict[str, dict[str, Any]] = {}

    for node in observation["elements"]:
        if node["enabled"] and (node["clickable"] or node["editable"]):
            actions[f"tap_{node['id']}"] = {
                "type": "tap-element",
                "elementId": node["id"],
            }

    actions["back"] = {"type": "global", "name": "back"}
    actions["home"] = {"type": "global", "name": "home"}

    # Derive gestures from each scrollable region.
    scroll_bounds: set[str] = set()
    scrollable = [
        e for e in observation["elements"] if e["enabled"] and e["scrollable"]
    ]

    def _area(b: dict) -> int:
        return (b["right"] - b["left"]) * (b["bottom"] - b["top"])

    for node in scrollable:
        # Prefer a nested list that occupies most of the container.
        if any(
            child["id"].startswith(node["id"] + ".")
            and _area(child["bounds"]) >= _area(node["bounds"]) * 0.7
            for child in scrollable
        ):
            continue
        bounds_key = json.dumps(node["bounds"], sort_keys=True)
        if bounds_key in scroll_bounds:
            continue
        scroll_bounds.add(bounds_key)

        b = node["bounds"]
        left, top, right, bottom = b["left"], b["top"], b["right"], b["bottom"]
        x = math.floor((left + right) / 2)
        y = math.floor((top + bottom) / 2)
        x1 = math.floor(left + (right - left) * 0.2)
        x2 = math.floor(left + (right - left) * 0.8)
        y1 = math.floor(top + (bottom - top) * 0.2)
        y2 = math.floor(top + (bottom - top) * 0.8)

        for direction, coords in {
            "down": (x, y2, x, y1),
            "up": (x, y1, x, y2),
            "right": (x2, y, x1, y),
            "left": (x1, y, x2, y),
        }.items():
            actions[f"scroll_{direction}_{node['id']}"] = {
                "type": "swipe",
                "startX": coords[0],
                "startY": coords[1],
                "endX": coords[2],
                "endY": coords[3],
                "duration": 300,
                "regionId": node["id"],
            }

    phone = observation.get("phone", {})
    if phone.get("isEditable"):
        actions["enter"] = {"type": "key", "key": "enter"}
        # Don't offer text entry into password fields.
        password_focused = any(
            e["password"]
            and (
                e["focused"]
                or e["id"] == phone.get("inputElementId")
            )
            for e in observation["elements"]
        )
        if not password_focused:
            for i, text in enumerate(texts):
                actions[f"text_{i}"] = {"type": "type", "text": text, "clear": True}

    return actions


def describe_action(action: dict[str, Any], observation: dict[str, Any]) -> str:
    """Produce a human-readable label for *action* given the current observation."""
    if action["type"] == "open-app":
        return f"Open {action.get('appLabel') or action['packageName']}"

    if action["type"] == "tap-element":
        target = next(
            (e for e in observation["elements"] if e["id"] == action["elementId"]),
            None,
        )
        if target and target.get("editable"):
            field_hint = (
                target.get("hint")
                or target.get("label")
                or target.get("text")
                or "empty input field"
            )
            return f"Focus text input: {field_hint}."
        labels = []
        for e in observation["elements"]:
            if e["id"] == action["elementId"] or e["id"].startswith(
                action["elementId"] + "."
            ):
                for key in ("text", "label"):
                    val = e.get(key)
                    if val and val not in labels:
                        labels.append(val)
        return f"Tap {' / '.join(labels) or action['elementId']}."

    if action["type"] == "swipe":
        if action["startY"] > action["endY"]:
            direction = "down"
        elif action["startY"] < action["endY"]:
            direction = "up"
        elif action["startX"] > action["endX"]:
            direction = "right"
        else:
            direction = "left"
        return (
            f"Scroll {direction} to reveal content further {direction} "
            f"in this scrollable region. Gesture: {json.dumps(action)}"
        )

    return json.dumps(action)
