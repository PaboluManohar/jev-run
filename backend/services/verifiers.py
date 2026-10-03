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
