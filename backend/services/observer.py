"""Android UI observation service — captures, parses, and fingerprints device state.

Ported from and faithfully preserving the behavior of the Node.js implementation
(``delete/scripts/mobile-agent/device.mjs`` and ``summarizeState()``).

Keeps observation logic completely decoupled from low-level ADB transport
and agent/decision policies.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Protocol, runtime_checkable

from backend.models.device import (
    Bounds,
    DeviceInfo,
    Element,
    FocusedElement,
    Observation,
    PhoneState,
    ScreenBounds,
)
from backend.services.phone_state import parse_phone_state, parse_screen_size
from backend.services.run_logger import StageContext

logger = logging.getLogger(__name__)


# ── Observation Error Hierarchy ──────────────────────────────────────────


class ObservationError(Exception):
    """Base exception for all observation failures."""


class DeviceDisconnectedError(ObservationError):
    """Raised when the target device is offline, unauthorized, or disconnected."""


class DeviceNotReadyError(ObservationError):
    """Raised when the device is not in the 'device' (ready) state."""


class ObservationTimeoutError(ObservationError):
    """Raised when capturing UI state or screenshot times out."""


class InvalidUIHierarchyError(ObservationError):
    """Raised when the UI hierarchy XML is empty, truncated, or unparseable."""


# ── Protocol for ADB execution ───────────────────────────────────────────


@runtime_checkable
class ADBExecutor(Protocol):
    """Structural interface for ADB command runners."""

    serial: str

    async def _adb(self, *args: str, timeout: float = 15.0) -> str: ...

    async def _adb_bytes(self, *args: str, timeout: float = 15.0) -> bytes: ...

    async def _shell(self, cmd: str, timeout: float = 15.0) -> str: ...

    async def get_device_info(self) -> dict[str, Any]: ...


# ── XML Parsing Constants & Helpers ──────────────────────────────────────

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

_EDITABLE_CLASSES = {
    "android.widget.EditText",
    "android.widget.AutoCompleteTextView",
    "android.widget.MultiAutoCompleteTextView",
}


def _parse_bounds(bounds_str: str) -> dict[str, int] | None:
    """Parse ``[left,top][right,bottom]`` string into a coordinate dict."""
    m = _BOUNDS_RE.match(bounds_str)
    if not m:
        return None
    return {
        "left": int(m.group(1)),
        "top": int(m.group(2)),
        "right": int(m.group(3)),
        "bottom": int(m.group(4)),
    }


def _xml_bool(element: ET.Element, attr: str, default: bool = False) -> bool:
    val = element.get(attr)
    if val is None:
        return default
    return val.strip().lower() == "true"


# ── Core UI Parser & Summarizer ──────────────────────────────────────────


def parse_ui_hierarchy(
    xml_text: str,
    screen: ScreenBounds,
) -> list[Element]:
    """Parse ``uiautomator`` XML dump into structured ``Element`` objects.

    Matches the exact filtering, coordinate clamping, password redaction,
    and hierarchical ID generation from ``device.mjs``:
    - Path prefix: ``ui``
    - Children paths: ``ui.0``, ``ui.0.1``, etc.
    - Clamps bounds to ``[0, 0, screen.width, screen.height]``
    - Discards invisible or zero/negative-area elements
    - Masks passwords to ``[password]`` and clears their label
    - Retains nodes having text, label, clickable, editable, or scrollable flags.
    """
    if not xml_text or not xml_text.strip():
        raise InvalidUIHierarchyError("Empty UI hierarchy XML provided.")

    start = xml_text.find("<")
    if start < 0:
        raise InvalidUIHierarchyError("No XML markup found in uiautomator output.")
    clean_xml = xml_text[start:]

    try:
        root = ET.fromstring(clean_xml)
    except ET.ParseError as exc:
        raise InvalidUIHierarchyError(f"Malformed UI XML hierarchy: {exc}") from exc

    nodes = list(root)
    if not nodes:
        raise InvalidUIHierarchyError("UI hierarchy XML contains no child nodes.")

    elements: list[Element] = []

    def visit(node_elem: ET.Element, path: str) -> None:
        b_raw = _parse_bounds(node_elem.get("bounds", ""))
        bounds = None
        if b_raw:
            bounds = Bounds(
                left=max(0, b_raw["left"]),
                top=max(0, b_raw["top"]),
                right=min(screen.width, b_raw["right"]),
                bottom=min(screen.height, b_raw["bottom"]),
            )

        is_visible = _xml_bool(node_elem, "visible-to-user", default=True)
        # uiautomator doesn't always have visible-to-user; check bounds validity
        is_valid_bounds = (
            bounds is not None
            and bounds.right > bounds.left
            and bounds.bottom > bounds.top
        )

        if is_visible and is_valid_bounds and bounds is not None:
            is_password = _xml_bool(node_elem, "password")
            raw_text = node_elem.get("text", "") or ""
            raw_desc = node_elem.get("content-desc", "") or ""
            class_name = node_elem.get("class", "") or (
                node_elem.tag if node_elem.tag not in ("node", "hierarchy") else ""
            )

            text = "[password]" if is_password else raw_text
            label = "" if is_password else raw_desc

            is_clickable = _xml_bool(node_elem, "clickable")
            is_editable = _xml_bool(node_elem, "editable") or class_name in _EDITABLE_CLASSES
            is_scrollable = _xml_bool(node_elem, "scrollable")

            if text or label or is_clickable or is_editable or is_scrollable:
                elem = Element(
                    id=path,
                    text=text,
                    label=label,
                    resource_id=node_elem.get("resource-id", "") or "",
                    class_name=class_name,
                    hint=node_elem.get("hint", "") or "",
                    bounds=bounds,
                    clickable=is_clickable,
                    editable=is_editable,
                    scrollable=is_scrollable,
                    enabled=_xml_bool(node_elem, "enabled", default=True),
                    focused=_xml_bool(node_elem, "focused"),
                    password=is_password,
                    checkable=_xml_bool(node_elem, "checkable"),
                    checked=_xml_bool(node_elem, "checked"),
                    selected=_xml_bool(node_elem, "selected"),
                )
                elements.append(elem)

        for i, child in enumerate(node_elem):
            visit(child, f"{path}.{i}")

    # Top-level node traversal
    if len(nodes) == 1:
        visit(nodes[0], "ui")
    else:
        for i, node in enumerate(nodes):
            visit(node, f"ui.{i}")

    return elements


def compute_screen_fingerprint(
    device_id: str,
    phone: PhoneState,
    screen: ScreenBounds,
    elements: list[Element],
) -> str:
    """Compute a deterministic SHA-256 fingerprint for screen state.

    Excludes timestamps and raw screenshots so that unchanged UI produces
    an identical fingerprint, matching ``device.mjs``.
    """
    canonical_dict = {
        "deviceId": device_id,
        "phone": phone.model_dump(by_alias=True),
        "screen": screen.model_dump(by_alias=True),
        "elements": [e.model_dump(by_alias=True) for e in elements],
    }
    encoded = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def derive_phone_state(
    phone_data: dict[str, Any],
    elements: list[Element],
) -> PhoneState:
    """Derive focus and input heuristics matching Node.js reference.

    Heuristic:
    - If exactly 1 focused editable input -> focus is on that element.
    - Else if keyboard is visible and exactly 1 editable input -> focus is on it.
    - Otherwise -> no focused input element.
    """
    inputs = [e for e in elements if e.editable and e.enabled]
    focused_inputs = [e for e in inputs if e.focused]

    if len(focused_inputs) == 1:
        input_elem = focused_inputs[0]
    elif phone_data.get("keyboardVisible") and len(inputs) == 1:
        input_elem = inputs[0]
    else:
        input_elem = None

    focus_evidence = "none"
    if phone_data.get("isEditable"):
        focus_evidence = "reported"
    elif input_elem and input_elem.focused:
        focus_evidence = "focused-node"
    elif input_elem:
        focus_evidence = "single-input-with-keyboard"

    focused_elem_dict = phone_data.get("focusedElement") or {}
    focused_elem = FocusedElement(
        resource_id=focused_elem_dict.get("resourceId", "") or "",
        class_name=focused_elem_dict.get("className", "") or "",
    )

    return PhoneState(
        package_name=phone_data.get("packageName", "") or "",
        current_app=phone_data.get("currentApp", "") or "",
        is_editable=phone_data.get("isEditable") is True or input_elem is not None,
        input_element_id=input_elem.id if input_elem else None,
        focus_evidence=focus_evidence,
        keyboard_visible=phone_data.get("keyboardVisible") is True,
        focused_element=focused_elem,
    )


# ── AndroidObserver Service ───────────────────────────────────────────────


class AndroidObserver:
    """Orchestrates Android observation over an ADB executor.

    Handles parallel data collection, format conversion, error recovery,
    fingerprint calculation, and structured ``Observation`` assembly.
    """

    def __init__(
        self,
        executor: ADBExecutor,
        *,
        default_timeout: float = 10.0,
        appium_client: Any | None = None,
    ) -> None:
        self.executor = executor
        self.default_timeout = default_timeout
        self._tty_supported: bool | None = getattr(executor, "_tty_supported", None)

        if appium_client is not None:
            self.appium_client = appium_client
        else:
            try:
                from backend.config import get_settings
                cfg = get_settings()
                if cfg.use_appium:
                    from backend.services.appium_client import get_appium_client
                    self.appium_client = get_appium_client(cfg)
                else:
                    self.appium_client = None
            except Exception:
                self.appium_client = None

    async def _capture_ui_xml(self) -> str:
        """Capture UI hierarchy XML with fallback mechanism and capability caching.

        Attempts high-speed extraction via Appium if available and enabled.
        Falls back seamlessly to ADB TTY or temporary file dumps if Appium is
        offline, disabled, or fails.
        """
        async with StageContext("ui_hierarchy_extraction", logger=logger):
            # 1. Fast Appium extraction if available
            if self.appium_client and getattr(self.appium_client, "enabled", True):
                try:
                    serial = getattr(self.executor, "serial", "") or ""
                    appium_xml = await self.appium_client.get_source(serial)
                    if appium_xml and ("<hierarchy" in appium_xml or "<node" in appium_xml or "<android" in appium_xml):
                        logger.debug("Captured UI XML hierarchy via Appium (%d chars)", len(appium_xml))
                        return appium_xml
                    elif appium_xml:
                        logger.debug("Appium returned unrecognized XML structure; falling back to ADB")
                except Exception as exc:
                    logger.debug("Appium UI hierarchy extraction failed: %s; falling back to ADB", exc)

            # 2. Existing ADB capture logic
            temp_file = "/data/local/tmp/jev_ui_dump.xml"

            if self._tty_supported is not False:
                try:
                    out = await self.executor._shell(
                        "uiautomator dump /dev/tty",
                        timeout=self.default_timeout,
                    )
                    if "<hierarchy" in out or "<node" in out:
                        self._tty_supported = True
                        if hasattr(self.executor, "_tty_supported"):
                            self.executor._tty_supported = True
                        logger.debug("Captured UI XML hierarchy via TTY (%d chars)", len(out))
                        return out
                    else:
                        self._tty_supported = False
                        if hasattr(self.executor, "_tty_supported"):
                            self.executor._tty_supported = False
                        logger.debug("TTY uiautomator dump produced non-XML output; disabling TTY for session")
                except Exception as exc:
                    self._tty_supported = False
                    if hasattr(self.executor, "_tty_supported"):
                        self.executor._tty_supported = False
                    logger.debug("TTY uiautomator dump failed: %s; trying file fallback", exc)

            # Combined single-roundtrip file dump and retrieval
            try:
                xml_content = await self.executor._shell(
                    f"uiautomator dump {temp_file} >/dev/null 2>&1 && cat {temp_file} && rm -f {temp_file}",
                    timeout=self.default_timeout,
                )
                if "<hierarchy" in xml_content or "<node" in xml_content:
                    logger.debug("Captured UI XML hierarchy via file fallback (%d chars)", len(xml_content))
                    return xml_content
            except Exception:
                pass

            # Robust fallback: separate dump and cat commands if combined shell syntax failed
            try:
                await self.executor._shell(
                    f"uiautomator dump {temp_file}",
                    timeout=self.default_timeout,
                )
                xml_content = await self.executor._shell(
                    f"cat {temp_file} && rm -f {temp_file}",
                    timeout=self.default_timeout,
                )
                if "<hierarchy" in xml_content or "<node" in xml_content:
                    logger.debug("Captured UI XML hierarchy via robust file fallback (%d chars)", len(xml_content))
                    return xml_content
            except Exception as exc:
                raise InvalidUIHierarchyError(f"Failed to dump UI hierarchy: {exc}") from exc

            raise InvalidUIHierarchyError("No UI hierarchy XML produced by uiautomator.")

    async def _capture_screenshot(self) -> bytes:
        """Capture screenshot via ``adb exec-out screencap -p``."""
        async with StageContext("screenshot_capture", logger=logger):
            try:
                data = await self.executor._adb_bytes(
                    "exec-out", "screencap", "-p", timeout=self.default_timeout
                )
            except TimeoutError as exc:
                raise ObservationTimeoutError("ADB screencap timed out.") from exc
            except Exception as exc:
                raise ObservationError(f"ADB screencap failed: {exc}") from exc

            if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
                raise ObservationError("Screenshot returned invalid or non-PNG data.")
            logger.debug("Captured screenshot PNG (%d bytes)", len(data))
            return data

    async def observe(
        self,
        *,
        include_screenshot: bool = True,
    ) -> Observation:
        """Execute complete Android observation.

        Returns a structured, typed ``Observation`` object with:
        - Timestamp (observedAt)
        - Device metadata (deviceId, deviceInfo)
        - Raw screenshot PNG bytes (if requested)
        - Extracted interactive/labeled UI elements
        - Phone focus & input state
        - Screen dimensions
        - Deterministic fingerprint
        """
        async with StageContext("observation", logger=logger):
            # 1. Verify device readiness
            async with StageContext("device_readiness", logger=logger):
                try:
                    device_state = await self.executor._adb("get-state", timeout=5.0)
                    state_str = device_state.strip()
                    if state_str != "device":
                        raise DeviceNotReadyError(
                            f"Android device is not ready (state: '{state_str}')."
                        )
                except TimeoutError as exc:
                    raise ObservationTimeoutError("ADB get-state timed out.") from exc
                except Exception as exc:
                    if "not ready" in str(exc).lower():
                        raise
                    raise DeviceDisconnectedError(f"ADB connection failed: {exc}") from exc

            # 2. Gather raw observation data concurrently
            tasks: list[asyncio.Task[Any]] = [
                asyncio.create_task(self._capture_ui_xml()),
                asyncio.create_task(
                    self.executor._shell("dumpsys window displays", timeout=5.0)
                ),
                asyncio.create_task(
                    self.executor._shell("dumpsys input_method", timeout=5.0)
                ),
                asyncio.create_task(
                    self.executor._shell("wm size", timeout=5.0)
                ),
            ]
            if include_screenshot:
                tasks.append(asyncio.create_task(self._capture_screenshot()))

            try:
                results = await asyncio.gather(*tasks)
            except TimeoutError as exc:
                raise ObservationTimeoutError(f"Observation task timed out: {exc}") from exc
            except (ObservationError, DeviceDisconnectedError, DeviceNotReadyError):
                raise
            except Exception as exc:
                raise ObservationError(f"Failed during parallel observation capture: {exc}") from exc

            ui_xml = results[0]
            window_dump = results[1]
            ime_dump = results[2]
            wm_output = results[3]
            screenshot_bytes = results[4] if include_screenshot else None

            # 3. Parse screen dimensions
            try:
                screen_dict = parse_screen_size(wm_output)
                screen = ScreenBounds(
                    width=screen_dict["width"],
                    height=screen_dict["height"],
                )
            except Exception as exc:
                raise ObservationError(f"Invalid screen bounds: {exc}") from exc

            # 4. Parse UI hierarchy XML
            with StageContext("ui_hierarchy_parse", logger=logger):
                elements = parse_ui_hierarchy(ui_xml, screen)
                logger.debug("Parsed %d UI elements", len(elements))

            # 5. Parse phone state and 7. Compute deterministic fingerprint
            with StageContext("state_processing", logger=logger):
                raw_phone_data = parse_phone_state(
                    window_dump=window_dump,
                    ime_dump=ime_dump,
                )
                phone = derive_phone_state(raw_phone_data, elements)

                # 6. Device information
                device_id = getattr(self.executor, "serial", "") or ""
                if not device_id:
                    try:
                        serial_out = await self.executor._adb("get-serialno", timeout=5.0)
                        device_id = serial_out.strip()
                    except Exception:
                        device_id = "default"

                device_info = None
                try:
                    screen_dict = {"width": screen.width, "height": screen.height}
                    try:
                        info_dict = await self.executor.get_device_info(screen_size=screen_dict)
                    except TypeError:
                        info_dict = await self.executor.get_device_info()
                    if info_dict:
                        device_info = DeviceInfo(**info_dict)
                except Exception:
                    pass

                fingerprint = compute_screen_fingerprint(
                    device_id=device_id,
                    phone=phone,
                    screen=screen,
                    elements=elements,
                )

            observed_at = time.time() * 1000.0

            return Observation(
                device_id=device_id,
                phone=phone,
                screen=screen,
                elements=elements,
                fingerprint=fingerprint,
                observed_at=observed_at,
                screenshot=screenshot_bytes,
                device_info=device_info,
            )
