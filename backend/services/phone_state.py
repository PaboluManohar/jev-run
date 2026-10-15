"""Gather phone state from ADB ``dumpsys`` commands.

Produces the ``phone_state`` dict that ``summarize_state()`` expects —
mirroring the shape that MobileRun returned.
"""

from __future__ import annotations

import re
from typing import Any


def parse_phone_state(
    *,
    window_dump: str,
    ime_dump: str,
) -> dict[str, Any]:
    """Build a ``phone_state`` dict from ADB dumpsys output.

    Parameters
    ----------
    window_dump : str
        Output of ``adb shell dumpsys window displays``.
    ime_dump : str
        Output of ``adb shell dumpsys input_method``.

    Returns a dict matching MobileRun's ``phone_state`` shape.
    """
    package_name = ""
    current_app = ""
    focused_resource_id = ""
    focused_class_name = ""

    # Parse current focus from ``dumpsys window displays``.
    # Typical line: ``mCurrentFocus=Window{abc d/e com.pkg/.Activity}``
    # or: ``mFocusedApp=ActivityRecord{... com.pkg/.Activity ...}``
    for line in window_dump.splitlines():
        stripped = line.strip()

        # mCurrentFocus gives the window owner.
        m = re.search(
            r"mCurrentFocus=Window\{[^}]*\s+(\S+)/(\S+)\}",
            stripped,
        )
        if m:
            package_name = m.group(1)
            current_app = m.group(2)
            continue

        # Fallback: mFocusedApp
        m = re.search(
            r"mFocusedApp=.*?(\S+)/(\S+)",
            stripped,
        )
        if m and not package_name:
            package_name = m.group(1)
            current_app = m.group(2)

    # Parse keyboard visibility from ``dumpsys input_method``.
    keyboard_visible = False
    is_editable = False
    for line in ime_dump.splitlines():
        stripped = line.strip()
        if "mInputShown=true" in stripped:
            keyboard_visible = True
        if "mServedInputConnection" in stripped and "null" not in stripped.lower():
            is_editable = True

    # In addition to mInputShown=true, verify if mInputShown appears anywhere in the dumpsys
    if not keyboard_visible and re.search(r"\bmInputShown\s*=\s*true\b", ime_dump):
        keyboard_visible = True

    return {
        "packageName": package_name,
        "currentApp": current_app,
        "isEditable": is_editable or keyboard_visible,
        "keyboardVisible": keyboard_visible,
        "focusedElement": {
            "resourceId": focused_resource_id,
            "className": focused_class_name,
        },
    }


def parse_screen_size(wm_output: str) -> dict[str, int]:
    """Parse ``adb shell wm size`` output into ``{width, height}``.

    Example output::

        Physical size: 1080x2400
        Override size: 1080x2340

    We use the override size if present, otherwise the physical size.
    """
    width, height = 0, 0
    for line in reversed(wm_output.strip().splitlines()):
        m = re.search(r"(\d+)x(\d+)", line)
        if m:
            width = int(m.group(1))
            height = int(m.group(2))
            break
    if width < 1 or height < 1:
        raise ValueError(f"Could not parse screen size from: {wm_output!r}")
    return {"width": width, "height": height}
