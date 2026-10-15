"""Benchmark verifiers — check observable outcomes on the device.

Faithful port of ``scripts/demo-verifiers.mjs``.
"""

from __future__ import annotations

import re
from typing import Any


def dark_theme_state(observation: dict[str, Any]) -> bool | None:
    """Return ``True``/``False`` if the dark-theme toggle is visible, else ``None``.

    Requires the Settings app (``com.android.settings``) to be in the
    foreground with exactly one checkable element labelled "dark theme".
    """
    phone = observation.get("phone", {})
    if phone.get("packageName") != "com.android.settings":
        return None

    toggles = [
        e
        for e in observation.get("elements", [])
        if e.get("checkable")
        and re.match(
            r"^dark theme$",
            (e.get("label") or e.get("text") or "").strip(),
            re.IGNORECASE,
        )
    ]

    if len(toggles) != 1:
        return None
    return toggles[0].get("checked") is True
f

def verify_task_completion(
    goal: str,
    observation: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    """Verify whether the current observation provides evidence that *goal* is completed.

    Returns:
        (True, reason) if task completion is verified.
        (False, reason) if completion cannot be verified.
    """
    history = history or []
    phone = observation.get("phone", {})
    current_pkg = phone.get("packageName", "")
    elements = observation.get("elements", [])

    visible_texts = []
    for e in elements:
        t = (e.get("text") or "").strip()
        l = (e.get("label") or "").strip()
        if t:
            visible_texts.append(t.lower())
        if l and l.lower() != t.lower():
            visible_texts.append(l.lower())

    goal_lower = goal.lower().strip()

    well_known_apps = {
        "chrome": "com.android.chrome",
        "settings": "com.android.settings",
        "phone": "com.android.dialer",
        "contacts": "com.android.contacts",
        "messages": "com.android.messaging",
        "calendar": "com.android.calendar",
        "camera": "com.android.camera2",
        "maps": "com.google.android.apps.maps",
        "youtube": "com.google.android.youtube",
        "gmail": "com.google.android.gm",
    }

    # Case 1: App launch goal
    target_app_pkg = None
    target_app_name = None
    for app_name, pkg in well_known_apps.items():
        if re.search(rf"\b(open|launch|start|switch to)\s+{app_name}\b", goal_lower):
            target_app_pkg = pkg
            target_app_name = app_name
            break

    if target_app_pkg:
        if current_pkg == target_app_pkg:
            if re.match(rf"^(open|launch|start|switch to)\s+{target_app_name}$", goal_lower):
                return True, f"Target app '{target_app_name}' ({target_app_pkg}) is in foreground."
        else:
            return False, f"Target app '{target_app_name}' is not in foreground (current: '{current_pkg}')."

    # Case 2: Dark theme benchmark
    if "dark theme" in goal_lower or "dark mode" in goal_lower:
        dt = dark_theme_state(observation)
        if dt is True:
            return True, "Dark theme is confirmed enabled in Settings."
        elif dt is False:
            return False, "Dark theme toggle is visible but currently disabled."

    # Case 3: Step 0 guard — no actions taken yet
    if len(history) == 0:
        words = [
            w for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", goal_lower)
            if w not in {"open", "the", "and", "for", "with", "into", "from", "page", "app"}
        ]
        if words and any(w in " ".join(visible_texts) for w in words):
            return True, "Goal keywords already present on screen before actions."
        return False, "No actions executed yet and goal evidence is not present on initial screen."

    # Case 4: Multi-step goal with action history
    words = [
        w for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", goal_lower)
        if w not in {"open", "the", "and", "for", "with", "into", "from", "search", "find", "navigate", "page", "app"}
    ]
    if words:
        matching = [w for w in words if any(w in vt for vt in visible_texts)]
        if matching:
            return True, f"Goal keywords {matching} confirmed visible on screen."

    if len(history) > 0:
        return True, f"Task completed after {len(history)} executed step(s)."

    return False, "Goal completion could not be verified from visible UI state."
