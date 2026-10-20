"""ADB device abstraction — drop-in replacement for ``MobilerunDevice``.

Every method on this class maps 1-to-1 to the original MobileRun API surface
but uses local ``adb`` commands instead of HTTPS calls to the MobileRun cloud.

Observation format
------------------
``observe()`` produces the same ``{deviceId, phone, screen, elements,
fingerprint, observedAt}`` dict as the JS ``summarizeState()`` function.

Action dispatch
---------------
``act()`` accepts the same action dict shapes (``tap``, ``tap-element``,
``swipe``, ``type``, ``clear``, ``key``, ``global``, ``open-app``) and
includes the same staleness guard (``assertFresh``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import shlex
import time
from typing import Any

from backend.services.ui_parser import parse_ui_xml
from backend.services.phone_state import parse_phone_state, parse_screen_size
from backend.services.staleness import StaleObservationError, assert_fresh
from backend.services.input_verification import prepare_input_verification
from backend.services.observer import AndroidObserver
from backend.services.run_logger import StageContext
from backend.models.device import Observation

logger = logging.getLogger(__name__)


# ── Key mappings (match the JS constants) ────────────────────────────────

KEYS: dict[str, int] = {
    "back": 4,
    "tab": 61,
    "enter": 66,
    "delete": 67,
    "forward_delete": 112,
}

GLOBAL_ACTIONS: dict[str, int] = {"back": 4, "home": 3, "recent": 187}

# ── App Discovery Optimization & Caching ─────────────────────────────────

DEFAULT_APPS_CACHE_TTL: float = 300.0  # 5 minutes
MAX_CONCURRENT_LABEL_RESOLUTIONS: int = 8

WELL_KNOWN_SYSTEM_APP_LABELS: dict[str, str] = {
    "com.android.settings": "Settings",
    "com.android.chrome": "Chrome",
    "com.android.contacts": "Contacts",
    "com.android.dialer": "Phone",
    "com.android.messaging": "Messages",
    "com.android.calendar": "Calendar",
    "com.android.camera2": "Camera",
    "com.google.android.apps.maps": "Maps",
    "com.google.android.youtube": "YouTube",
    "com.google.android.gm": "Gmail",
}


class AppCacheEntry:
    """Cache entry holding resolved apps and per-package label index."""

    def __init__(
        self,
        timestamp: float,
        apps: list[dict[str, str]],
        labels: dict[str, str] | None = None,
    ) -> None:
        self.timestamp = timestamp
        self.apps = apps
        self.labels = labels or {}


_DEVICE_APPS_CACHE: dict[str, AppCacheEntry] = {}
_DEVICE_APPS_LOCKS: dict[str, asyncio.Lock] = {}


def _get_device_lock(device_id: str) -> asyncio.Lock:
    if device_id not in _DEVICE_APPS_LOCKS:
        _DEVICE_APPS_LOCKS[device_id] = asyncio.Lock()
    return _DEVICE_APPS_LOCKS[device_id]


class ADBDevice:
    """Local Android device controlled via ADB."""

    def __init__(
        self,
        *,
        serial: str = "",
        adb_path: str = "adb",
        text_completion_mode: str = "accepted",
    ) -> None:
        self.serial = serial
        self.adb_path = adb_path
        if text_completion_mode not in ("accepted", "committed"):
            raise ValueError("text_completion_mode must be 'accepted' or 'committed'.")
        self.text_completion_mode = text_completion_mode
        self._ready_at: float = -float("inf")
        self.installed_apps: list[dict[str, str]] = []

    # ── ADB shell helpers ────────────────────────────────────────────────

    def _serial_args(self) -> list[str]:
        return ["-s", self.serial] if self.serial else []

    async def _adb(self, *args: str, timeout: float = 15.0) -> str:
        """Run an ``adb`` command and return stdout."""
        cmd = [self.adb_path, *self._serial_args(), *args]
        cmd_str = " ".join(cmd)
        async with StageContext("adb_command", logger=logger, cmd=cmd_str):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise TimeoutError(f"ADB command timed out: {cmd_str}")
            if proc.returncode != 0:
                err = stderr.decode(errors="replace").strip()
                raise RuntimeError(
                    f"ADB command failed (exit {proc.returncode}): {err}"
                )
            return stdout.decode(errors="replace")

    async def _adb_bytes(self, *args: str, timeout: float = 15.0) -> bytes:
        """Run an ``adb`` command and return raw stdout bytes."""
        cmd = [self.adb_path, *self._serial_args(), *args]
        cmd_str = " ".join(cmd)
        async with StageContext("adb_bytes_command", logger=logger, cmd=cmd_str):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise TimeoutError(f"ADB command timed out: {cmd_str}")
            if proc.returncode != 0:
                err = stderr.decode(errors="replace").strip()
                raise RuntimeError(
                    f"ADB command failed (exit {proc.returncode}): {err}"
                )
            return stdout

    async def _shell(self, cmd: str, timeout: float = 15.0) -> str:
        """Shortcut for ``adb shell <cmd>``."""
        async with StageContext("adb_shell", logger=logger, cmd=cmd):
            return await self._adb("shell", cmd, timeout=timeout)

    # ── Public device API (mirrors MobilerunDevice) ──────────────────────

    async def list_devices(self) -> list[dict[str, str]]:
        """List connected ADB devices."""
        output = await self._adb("devices", "-l")
        devices = []
        for line in output.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                serial = parts[0]
                state = parts[1]
                # Extract model name from properties.
                name = serial
                for part in parts[2:]:
                    if part.startswith("model:"):
                        name = part.split(":", 1)[1]
                        break
                devices.append({"id": serial, "name": name, "state": state})
        return devices

    async def assert_ready(self) -> dict[str, str]:
        """Verify the device is online and reachable."""
        state_output = await self._adb("get-state")
        state = state_output.strip()
        if state != "device":
            raise RuntimeError(
                f"Device is '{state}'; it must be 'device' (online)."
            )
        # Get device model name.
        name = (await self._shell("getprop ro.product.model")).strip()
        serial = self.serial or (await self._get_serial())
        self._ready_at = time.monotonic()
        return {"id": serial, "name": name or serial, "state": "ready"}

    async def _get_serial(self) -> str:
        """Resolve the serial when none was explicitly set."""
        output = await self._adb("get-serialno")
        return output.strip()

    @classmethod
    def clear_apps_cache(cls, device_id: str | None = None) -> None:
        """Clear cached installed-app metadata for testing or cache invalidation."""
        if device_id is not None:
            _DEVICE_APPS_CACHE.pop(device_id, None)
        else:
            _DEVICE_APPS_CACHE.clear()

    async def list_apps(
        self,
        *,
        force_refresh: bool = False,
        ttl: float = DEFAULT_APPS_CACHE_TTL,
    ) -> list[dict[str, str]]:
        """List installed apps with package names and labels.

        Optimized with per-device caching, bounded concurrency for label
        resolution, and double-checked locking to avoid redundant ADB subprocesses.
        """
        device_id = self.serial or await self._get_serial()

        # Fast path: check valid cache without locking
        cached = _DEVICE_APPS_CACHE.get(device_id)
        now = time.monotonic()
        if (
            not force_refresh
            and cached is not None
            and (now - cached.timestamp < ttl)
        ):
            self.installed_apps = cached.apps
            return cached.apps

        # Synchronize refresh per device to avoid concurrent duplicate requests
        lock = _get_device_lock(device_id)
        async with lock:
            cached = _DEVICE_APPS_CACHE.get(device_id)
            now = time.monotonic()
            if (
                not force_refresh
                and cached is not None
                and (now - cached.timestamp < ttl)
            ):
                self.installed_apps = cached.apps
                return cached.apps

            # Retain previously resolved labels across refreshes
            label_cache = dict(cached.labels) if cached is not None else {}
            apps = await self._fetch_apps_fresh(label_cache)

            _DEVICE_APPS_CACHE[device_id] = AppCacheEntry(
                timestamp=time.monotonic(),
                apps=apps,
                labels=label_cache,
            )
            self.installed_apps = apps
            return apps

    async def _fetch_apps_fresh(
        self,
        label_cache: dict[str, str],
    ) -> list[dict[str, str]]:
        """Fetch package list and resolve labels with bounded concurrency."""
        output = await self._shell("pm list packages -3")
        packages: list[str] = []
        for line in output.strip().splitlines():
            line = line.strip()
            if line.startswith("package:"):
                packages.append(line[8:])

        # Key system apps that are commonly targeted
        system_apps = [
            "com.android.settings",
            "com.android.chrome",
            "com.android.contacts",
            "com.android.dialer",
            "com.android.messaging",
            "com.android.calendar",
            "com.android.camera2",
            "com.google.android.apps.maps",
            "com.google.android.youtube",
            "com.google.android.gm",
        ]
        for pkg in system_apps:
            if pkg not in packages:
                packages.append(pkg)

        sem = asyncio.Semaphore(MAX_CONCURRENT_LABEL_RESOLUTIONS)

        async def resolve_one(pkg: str) -> tuple[str, str]:
            if pkg in label_cache:
                return pkg, label_cache[pkg]
            if pkg in WELL_KNOWN_SYSTEM_APP_LABELS:
                lbl = WELL_KNOWN_SYSTEM_APP_LABELS[pkg]
                label_cache[pkg] = lbl
                return pkg, lbl
            async with sem:
                lbl = await self._get_app_label(pkg)
                if lbl:
                    label_cache[pkg] = lbl
                return pkg, lbl

        results = await asyncio.gather(*(resolve_one(pkg) for pkg in packages))
        return [{"packageName": pkg, "label": lbl} for pkg, lbl in results if lbl]

    async def _get_app_label(self, package_name: str) -> str:
        """Resolve a human-readable label for an installed package."""
        try:
            output = await self._shell(
                f"dumpsys package {shlex.quote(package_name)}",
                timeout=5.0,
            )
            # Look for the application label in the dump.
            for line in output.splitlines():
                stripped = line.strip()
                # Non-localized label line.
                m = re.match(r"non-localized-label:\s*(.+)", stripped)
                if m:
                    return m.group(1).strip()
            # Fallback: use the last component of the package name.
            return package_name.rsplit(".", 1)[-1].replace("_", " ").title()
        except Exception:
            return package_name.rsplit(".", 1)[-1].replace("_", " ").title()

    async def observe(self, *, include_screenshot: bool = True) -> Observation:
        """Capture the current UI state and return a structured Observation.

        Delegates to ``AndroidObserver`` to handle parsing, error recovery,
        filtering, and fingerprinting while decoupling observation logic
        from low-level transport.
        """
        observer = AndroidObserver(self)
        return await observer.observe(include_screenshot=include_screenshot)

    async def screenshot(self) -> bytes:
        """Capture the device screen as a PNG."""
        data = await self._adb_bytes("exec-out", "screencap", "-p", timeout=10.0)
        # Validate PNG magic bytes.
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Screenshot response is not a PNG.")
        return data

    async def act(
        self,
        action: dict[str, Any],
        *,
        expected: dict[str, Any] | None = None,
        max_age_ms: float = 30_000,
    ) -> dict[str, Any] | None:
        """Execute an action on the device.

        If *expected* is provided, the current screen is observed first and
        validated for staleness.  Returns an ``inputVerification`` receipt
        when applicable.
        """
        if not action or not isinstance(action, dict):
            raise ValueError("An action object is required.")

        # Periodic readiness check.
        if time.monotonic() - self._ready_at > 30:
            await self.assert_ready()

        action_type = action.get("type")
        async with StageContext("action_execution", logger=logger, action_type=action_type):
            if action_type == "open-app":
                pkg = action["packageName"]
                if not any(
                    app["packageName"] == pkg for app in self.installed_apps
                ):
                    raise ValueError("The app was not observed in the installed-app list.")
                if expected and expected.get("deviceId") != (
                    self.serial or await self._get_serial()
                ):
                    raise ValueError("Observation belongs to a different device.")
                await self._shell(
                    f"monkey -p {shlex.quote(pkg)} "
                    f"-c android.intent.category.LAUNCHER 1"
                )
                return None

            current = await self.observe()
            if expected:
                assert_fresh(current, expected, action, max_age_ms)

            input_verification = None

            if action_type == "tap":
                _check_point(action["x"], action["y"], current["screen"])
                await self._shell(f"input tap {action['x']} {action['y']}")

            elif action_type == "tap-element":
                if not expected:
                    raise ValueError("Element taps require their original observation.")
                node = next(
                    (e for e in current["elements"] if e["id"] == action["elementId"]),
                    None,
                )
                if not node or not node["enabled"] or not (
                    node["clickable"] or node["editable"]
                ):
                    raise ValueError("Element is not actionable.")
                b = node["bounds"]
                x = math.floor((b["left"] + b["right"]) / 2)
                y = math.floor((b["top"] + b["bottom"]) / 2)
                await self._shell(f"input tap {x} {y}")

            elif action_type == "swipe":
                start_x = action["startX"]
                start_y = action["startY"]
                end_x = action["endX"]
                end_y = action["endY"]
                duration = action.get("duration", 300)

                # Re-project swipe if region bounds changed.
                if action.get("regionId") and expected:
                    start_x, start_y, end_x, end_y = _reproject_swipe(
                        action, expected, current
                    )

                _check_point(start_x, start_y, current["screen"])
                _check_point(end_x, end_y, current["screen"])
                await self._shell(
                    f"input swipe {start_x} {start_y} {end_x} {end_y} {duration}"
                )

            elif action_type == "type":
                phone = current.get("phone", {})
                if not phone.get("isEditable"):
                    raise ValueError("Focus an editable field before typing.")
                text = action.get("text", "")
                clear = action.get("clear", False)
                if not isinstance(text, str):
                    raise ValueError("Text must be a string.")

                input_verification = (
                    prepare_input_verification(current, action)
                    if self.text_completion_mode == "accepted"
                    else None
                )

                if clear:
                    await self._clear_field()
                await self._type_text(text)

            elif action_type == "clear":
                phone = current.get("phone", {})
                if not phone.get("isEditable"):
                    raise ValueError("Focus an editable field before clearing.")
                await self._clear_field()

            elif action_type == "key":
                key_name = action.get("key", "")
                if key_name not in KEYS:
                    raise ValueError(f"Unsupported keyboard key: {key_name}")
                await self._shell(f"input keyevent {KEYS[key_name]}")

            elif action_type == "global":
                name = action.get("name", "")
                if name not in GLOBAL_ACTIONS:
                    raise ValueError(f"Unsupported global action: {name}")
                await self._shell(f"input keyevent {GLOBAL_ACTIONS[name]}")

            else:
                raise ValueError(f"Unsupported action type: {action_type}")

            if input_verification:
                return {"inputVerification": input_verification}
            return None

    # ── Private text-input helpers ───────────────────────────────────────

    async def _clear_field(self) -> None:
        """Select-all then delete the content of the currently focused field."""
        try:
            # Android keycombination: Ctrl (113) + A (29)
            await self._shell("input keycombination 113 29")
            await asyncio.sleep(0.05)
            await self._shell("input keyevent KEYCODE_DEL")
        except Exception:
            # Fallback: move to end and delete
            await self._shell("input keyevent KEYCODE_MOVE_END")
            for _ in range(40):
                await self._shell("input keyevent KEYCODE_DEL")

    async def _type_text(self, text: str) -> None:
        """Type text into the currently focused field.

        Uses ``input text`` for ASCII-safe strings.  For text containing
        spaces or special characters, uses a broadcast-based approach
        (requires ADBKeyboard if installed) or falls back to
        character-by-character input.
        """
        if not text:
            return

        # ``adb shell input text`` can handle alphanumeric + some symbols
        # but fails on spaces, unicode, and certain special chars.
        # Escape what we can; for complex text, use the broadcast method.
        if _is_simple_ascii(text):
            escaped = text.replace("\\", "\\\\")
            escaped = escaped.replace(" ", "%s")
            escaped = escaped.replace("'", "\\'")
            escaped = escaped.replace('"', '\\"')
            escaped = escaped.replace("&", "\\&")
            escaped = escaped.replace("<", "\\<")
            escaped = escaped.replace(">", "\\>")
            escaped = escaped.replace(";", "\\;")
            escaped = escaped.replace("(", "\\(")
            escaped = escaped.replace(")", "\\)")
            escaped = escaped.replace("|", "\\|")
            escaped = escaped.replace("$", "\\$")
            await self._shell(f"input text {shlex.quote(escaped)}")
        else:
            # Try ADBKeyboard broadcast for unicode support.
            try:
                await self._shell(
                    f"am broadcast -a ADB_INPUT_TEXT --es msg {shlex.quote(text)}"
                )
            except RuntimeError:
                # Final fallback: character by character via input text.
                for char in text:
                    if char == " ":
                        await self._shell("input text %s")
                    elif char.isascii() and char.isprintable():
                        escaped = char.replace("\\", "\\\\")
                        await self._shell(f"input text {shlex.quote(escaped)}")
                    else:
                        # For non-ASCII, try the broadcast per-character.
                        await self._shell(
                            f"am broadcast -a ADB_INPUT_TEXT --es msg {shlex.quote(char)}"
                        )

    # ── Device info for the frontend ─────────────────────────────────────

    async def get_device_info(self) -> dict[str, Any]:
        """Return device info matching the frontend's ``Device`` type."""
        try:
            info = await self.assert_ready()
            width, height = 1080, 2400
            try:
                wm_output = await self._shell("wm size")
                size = parse_screen_size(wm_output)
                width, height = size["width"], size["height"]
            except Exception:
                pass
            return {
                "id": info["id"],
                "name": info["name"],
                "state": "ready",
                "streamUrl": None,
                "streamToken": None,
                "environment": "Local",
                "screenWidth": width,
                "screenHeight": height,
            }
        except Exception:
            return {
                "id": self.serial or "none",
                "name": "No device connected",
                "state": "disconnected",
                "streamUrl": None,
                "streamToken": None,
                "environment": "Local",
                "screenWidth": 1080,
                "screenHeight": 2400,
            }


# ── Module-level helpers ─────────────────────────────────────────────────


def _is_simple_ascii(text: str) -> bool:
    """Check if text can be safely passed to ``adb shell input text``."""
    return all(c.isascii() and c.isprintable() or c == " " for c in text)


def _check_point(x: int, y: int, screen: dict[str, int]) -> None:
    if not isinstance(x, int) or not isinstance(y, int) or x < 0 or y < 0:
        raise ValueError(f"Coordinates must be non-negative integers: ({x}, {y})")
    if x >= screen["width"] or y >= screen["height"]:
        raise ValueError("Coordinates are outside the screen.")


def _reproject_swipe(
    action: dict[str, Any],
    expected: dict[str, Any],
    current: dict[str, Any],
) -> tuple[int, int, int, int]:
    """Re-project a swipe gesture into current screen geometry."""
    rid = action["regionId"]
    before = next(e for e in expected["elements"] if e["id"] == rid)["bounds"]
    after = next(e for e in current["elements"] if e["id"] == rid)["bounds"]

    def project(
        value: int, old_start: int, old_end: int, new_start: int, new_end: int
    ) -> int:
        span = old_end - old_start
        if span == 0:
            raise ValueError("Swipe leaves its observed region.")
        fraction = (value - old_start) / span
        if not (0 <= fraction < 1):
            raise ValueError("Swipe leaves its observed region.")
        return math.floor(new_start + fraction * (new_end - new_start))

    return (
        project(action["startX"], before["left"], before["right"], after["left"], after["right"]),
        project(action["startY"], before["top"], before["bottom"], after["top"], after["bottom"]),
        project(action["endX"], before["left"], before["right"], after["left"], after["right"]),
        project(action["endY"], before["top"], before["bottom"], after["top"], after["bottom"]),
    )


def summarize_state(raw: dict[str, Any], device_id: str) -> dict[str, Any]:
    """Transform raw UI state into the normalised observation dict.

    This is a faithful port of the JS ``summarizeState()`` function.
    The output shape is identical so all downstream logic (actions, policy,
    staleness, input verification) works unchanged.
    """
    screen = raw.get("device_context", {}).get("screen_bounds")
    if (
        not screen
        or not isinstance(screen.get("width"), int)
        or not isinstance(screen.get("height"), int)
        or screen["width"] < 1
        or screen["height"] < 1
        or not raw.get("phone_state")
        or not raw.get("a11y_tree")
    ):
        raise ValueError(
            "UI state is missing its tree, phone state, or valid screen bounds."
        )

    elements: list[dict[str, Any]] = []

    def visit(node: dict[str, Any], path: str) -> None:
        if not node or not isinstance(node, dict):
            return
        b = node.get("boundsInScreen")
        bounds = None
        if b and all(
            isinstance(b.get(k), (int, float))
            for k in ("left", "top", "right", "bottom")
        ):
            bounds = {
                "left": max(0, b["left"]),
                "top": max(0, b["top"]),
                "right": min(screen["width"], b["right"]),
                "bottom": min(screen["height"], b["bottom"]),
            }

        if (
            node.get("isVisibleToUser") is not False
            and bounds
            and bounds["right"] > bounds["left"]
            and bounds["bottom"] > bounds["top"]
        ):
            password = node.get("isPassword") is True
            text = "[password]" if password else (node.get("text") or "")
            label = "" if password else (node.get("contentDescription") or "")
            editable = node.get("isEditable") is True or node.get("className") in (
                "android.widget.EditText",
                "android.widget.AutoCompleteTextView",
                "android.widget.MultiAutoCompleteTextView",
            )

            if text or label or node.get("isClickable") or editable or node.get("isScrollable"):
                elements.append(
                    {
                        "id": path,
                        "text": text,
                        "label": label,
                        "resourceId": node.get("resourceId") or "",
                        "hint": node.get("hint") or "",
                        "bounds": bounds,
                        "clickable": node.get("isClickable") is True,
                        "editable": editable,
                        "scrollable": node.get("isScrollable") is True,
                        "enabled": node.get("isEnabled") is not False,
                        "focused": node.get("isFocused") is True,
                        "password": password,
                        "checkable": node.get("isCheckable") is True,
                        "checked": node.get("isChecked") is True,
                        "selected": node.get("isSelected") is True,
                    }
                )

        for i, child in enumerate(node.get("children", [])):
            visit(child, f"{path}.{i}")

    visit(raw["a11y_tree"], "ui")

    # Determine the focused input field (same heuristic as JS).
    ps = raw["phone_state"]
    inputs = [e for e in elements if e["editable"] and e["enabled"]]
    focused_inputs = [e for e in inputs if e["focused"]]

    if len(focused_inputs) == 1:
        input_field = focused_inputs[0]
    elif ps.get("keyboardVisible") and len(inputs) == 1:
        input_field = inputs[0]
    else:
        input_field = None

    phone: dict[str, Any] = {
        "packageName": ps.get("packageName", ""),
        "currentApp": ps.get("currentApp", ""),
        "isEditable": ps.get("isEditable") is True or input_field is not None,
        "inputElementId": input_field["id"] if input_field else None,
        "focusEvidence": (
            "reported"
            if ps.get("isEditable")
            else "focused-node"
            if input_field and input_field.get("focused")
            else "single-input-with-keyboard"
            if input_field
            else "none"
        ),
        "keyboardVisible": ps.get("keyboardVisible") is True,
        "focusedElement": {
            "resourceId": ps.get("focusedElement", {}).get("resourceId", ""),
            "className": ps.get("focusedElement", {}).get("className", ""),
        },
    }

    content = {
        "deviceId": device_id,
        "phone": phone,
        "screen": screen,
        "elements": elements,
    }

    fingerprint = hashlib.sha256(
        json.dumps(content, sort_keys=True).encode()
    ).hexdigest()

    return {
        **content,
        "fingerprint": fingerprint,
        "observedAt": time.time() * 1000,  # ms since epoch, matching JS Date.now()
    }
