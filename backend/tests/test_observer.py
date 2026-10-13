"""Unit tests for Android Observer service.

Validates UI parsing, password redaction, bounds clamping, hierarchical IDs,
fingerprint generation, phone state derivation, and error handling for ADB/XML failures.
"""

import asyncio
import pytest

from backend.models.device import Bounds, DeviceInfo, Element, FocusedElement, PhoneState, ScreenBounds
from backend.services.observer import (
    AndroidObserver,
    DeviceDisconnectedError,
    DeviceNotReadyError,
    InvalidUIHierarchyError,
    ObservationError,
    ObservationTimeoutError,
    compute_screen_fingerprint,
    derive_phone_state,
    parse_ui_hierarchy,
)

SAMPLE_UI_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,2400]">
    <node index="0" text="Network &amp; internet" resource-id="android:id/title" class="android.widget.TextView" package="com.android.settings" content-desc="Network settings" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[64,300][900,420]">
    </node>
    <node index="1" text="" resource-id="com.android.settings:id/search_box" class="android.widget.EditText" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="true" scrollable="false" long-clickable="true" password="false" selected="false" bounds="[64,150][1016,250]">
    </node>
    <node index="2" text="" resource-id="com.android.settings:id/list" class="androidx.recyclerview.widget.RecyclerView" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="true" long-clickable="false" password="false" selected="false" bounds="[0,260][1080,2200]">
    </node>
    <node index="3" text="Hidden Info" resource-id="" class="android.widget.TextView" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][0,0]">
    </node>
  </node>
</hierarchy>
"""

PNG_MAGIC_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# ── UI Hierarchy Parsing Tests ───────────────────────────────────────────


def test_parse_ui_hierarchy_basic():
    """Verify standard uiautomator XML is parsed into structured elements."""
    screen = ScreenBounds(width=1080, height=2400)
    elements = parse_ui_hierarchy(SAMPLE_UI_XML, screen)

    # Elements expected: Network TextView, EditText search box, RecyclerView list
    # The zero-bounds Hidden Info should be filtered out
    assert len(elements) == 3

    # Check first element (TextView)
    el0 = elements[0]
    assert el0.id == "ui.0"
    assert el0.text == "Network & internet"
    assert el0.label == "Network settings"
    assert el0.resource_id == "android:id/title"
    assert el0.class_name == "android.widget.TextView"
    assert el0.clickable is True
    assert el0.scrollable is False
    assert el0.bounds.left == 64
    assert el0.bounds.top == 300
    assert el0.bounds.right == 900
    assert el0.bounds.bottom == 420

    # Check second element (EditText)
    el1 = elements[1]
    assert el1.id == "ui.1"
    assert el1.class_name == "android.widget.EditText"
    assert el1.editable is True
    assert el1.focused is True
    assert el1.resource_id == "com.android.settings:id/search_box"

    # Check third element (RecyclerView)
    el2 = elements[2]
    assert el2.id == "ui.2"
    assert el2.scrollable is True


def test_parse_ui_hierarchy_password_redaction():
    """Verify password fields are redacted to '[password]' and label is blanked."""
    xml = """<hierarchy rotation="0">
      <node index="0" text="secret123" resource-id="pin_field" class="android.widget.EditText" package="app" content-desc="Enter PIN" clickable="true" enabled="true" password="true" bounds="[100,100][500,200]" />
    </hierarchy>"""
    screen = ScreenBounds(width=1080, height=2400)
    elements = parse_ui_hierarchy(xml, screen)

    assert len(elements) == 1
    assert elements[0].text == "[password]"
    assert elements[0].label == ""
    assert elements[0].password is True
    assert "secret123" not in elements[0].model_dump_json()


def test_parse_ui_hierarchy_bounds_clamping():
    """Verify coordinates are clamped to screen dimensions and negative values to 0."""
    xml = """<hierarchy rotation="0">
      <node index="0" text="Offscreen" resource-id="off" class="android.widget.Button" package="app" content-desc="" clickable="true" enabled="true" bounds="[-50,-10][1200,2500]" />
    </hierarchy>"""
    screen = ScreenBounds(width=1080, height=2400)
    elements = parse_ui_hierarchy(xml, screen)

    assert len(elements) == 1
    b = elements[0].bounds
    assert b.left == 0
    assert b.top == 0
    assert b.right == 1080
    assert b.bottom == 2400


def test_parse_ui_hierarchy_empty_and_malformed():
    """Verify empty or malformed XML raises InvalidUIHierarchyError."""
    screen = ScreenBounds(width=1080, height=2400)

    with pytest.raises(InvalidUIHierarchyError, match="Empty UI hierarchy"):
        parse_ui_hierarchy("", screen)

    with pytest.raises(InvalidUIHierarchyError, match="Empty UI hierarchy"):
        parse_ui_hierarchy("   \n\t  ", screen)

    with pytest.raises(InvalidUIHierarchyError, match="No XML markup"):
        parse_ui_hierarchy("ERROR: could not dump", screen)

    with pytest.raises(InvalidUIHierarchyError, match="Malformed UI XML"):
        parse_ui_hierarchy("<hierarchy><node unclosed", screen)

    with pytest.raises(InvalidUIHierarchyError, match="contains no child nodes"):
        parse_ui_hierarchy("<hierarchy rotation='0'></hierarchy>", screen)


# ── Fingerprint Generation Tests ─────────────────────────────────────────


def test_compute_screen_fingerprint_determinism():
    """Verify identical UI produce identical fingerprints, ignoring timestamp/screenshot."""
    screen = ScreenBounds(width=1080, height=2400)
    elem = Element(
        id="ui.0",
        text="Home",
        bounds=Bounds(left=0, top=0, right=100, bottom=100),
        clickable=True,
    )
    phone = PhoneState(package_name="com.android.launcher", current_app="Launcher")

    fp1 = compute_screen_fingerprint("device-1", phone, screen, [elem])
    fp2 = compute_screen_fingerprint("device-1", phone, screen, [elem])
    assert fp1 == fp2
    assert len(fp1) == 64  # Valid SHA-256 hex


def test_compute_screen_fingerprint_sensitivity():
    """Verify changing an element or state produces a different fingerprint."""
    screen = ScreenBounds(width=1080, height=2400)
    elem1 = Element(
        id="ui.0",
        text="Home",
        bounds=Bounds(left=0, top=0, right=100, bottom=100),
        clickable=True,
    )
    elem2 = Element(
        id="ui.0",
        text="Settings",
        bounds=Bounds(left=0, top=0, right=100, bottom=100),
        clickable=True,
    )
    phone = PhoneState(package_name="com.android.launcher", current_app="Launcher")

    fp1 = compute_screen_fingerprint("device-1", phone, screen, [elem1])
    fp2 = compute_screen_fingerprint("device-1", phone, screen, [elem2])
    assert fp1 != fp2


# ── Phone State & Focus Derivation Tests ─────────────────────────────────


def test_derive_phone_state_focused_node():
    """Verify focus evidence is set to 'focused-node' when one input is focused."""
    elements = [
        Element(
            id="ui.1",
            text="Query",
            bounds=Bounds(left=0, top=0, right=100, bottom=50),
            editable=True,
            focused=True,
            enabled=True,
        ),
        Element(
            id="ui.2",
            text="Button",
            bounds=Bounds(left=0, top=60, right=100, bottom=100),
            clickable=True,
        ),
    ]
    raw_phone = {
        "packageName": "com.app",
        "currentApp": "AppActivity",
        "keyboardVisible": False,
        "isEditable": False,
    }
    state = derive_phone_state(raw_phone, elements)

    assert state.is_editable is True
    assert state.input_element_id == "ui.1"
    assert state.focus_evidence == "focused-node"


def test_derive_phone_state_single_input_keyboard():
    """Verify focus evidence is set when keyboard is up and one editable input exists."""
    elements = [
        Element(
            id="ui.0",
            text="",
            bounds=Bounds(left=0, top=0, right=100, bottom=50),
            editable=True,
            focused=False,
            enabled=True,
        ),
    ]
    raw_phone = {
        "packageName": "com.app",
        "currentApp": "AppActivity",
        "keyboardVisible": True,
        "isEditable": False,
    }
    state = derive_phone_state(raw_phone, elements)

    assert state.is_editable is True
    assert state.input_element_id == "ui.0"
    assert state.focus_evidence == "single-input-with-keyboard"


# ── Mock ADB Executor & Observation Pipeline Tests ───────────────────────


class MockADBExecutor:
    """Mock ADB runner simulating device responses and error states."""

    def __init__(
        self,
        *,
        state: str = "device",
        ui_xml: str = SAMPLE_UI_XML,
        window_dump: str = "mCurrentFocus=Window{abc com.android.settings/com.android.settings.Settings}",
        ime_dump: str = "mInputShown=false",
        wm_size: str = "Physical size: 1080x2400",
        screenshot_bytes: bytes = PNG_MAGIC_BYTES,
        timeout_command: str | None = None,
        fail_get_state: bool = False,
    ) -> None:
        self.serial = "mock-serial-123"
        self.state = state
        self.ui_xml = ui_xml
        self.window_dump = window_dump
        self.ime_dump = ime_dump
        self.wm_size = wm_size
        self.screenshot_bytes = screenshot_bytes
        self.timeout_command = timeout_command
        self.fail_get_state = fail_get_state

    async def _adb(self, *args: str, timeout: float = 15.0) -> str:
        if self.timeout_command == "adb":
            raise TimeoutError("ADB command timed out")
        if args and args[0] == "get-state":
            if self.fail_get_state:
                raise RuntimeError("error: no devices/emulators found")
            return self.state
        if args and args[0] == "get-serialno":
            return self.serial
        return ""

    async def _adb_bytes(self, *args: str, timeout: float = 15.0) -> bytes:
        if self.timeout_command == "screencap":
            raise TimeoutError("Screencap timed out")
        return self.screenshot_bytes

    async def _shell(self, cmd: str, timeout: float = 15.0) -> str:
        if self.timeout_command and self.timeout_command in cmd:
            raise TimeoutError(f"Shell command {cmd} timed out")
        if "uiautomator dump" in cmd:
            return self.ui_xml
        if "dumpsys window" in cmd:
            return self.window_dump
        if "dumpsys input_method" in cmd:
            return self.ime_dump
        if "wm size" in cmd:
            return self.wm_size
        return ""

    async def get_device_info(self) -> dict[str, str]:
        return {
            "id": self.serial,
            "name": "Mock Phone",
            "state": "ready",
            "environment": "Local",
        }


@pytest.mark.asyncio
async def test_android_observer_full_pipeline():
    """Verify complete observation creates a valid, structured Observation object."""
    mock_exec = MockADBExecutor()
    observer = AndroidObserver(mock_exec)

    obs = await observer.observe(include_screenshot=True)

    # Validate structured object attributes
    assert obs.device_id == "mock-serial-123"
    assert obs.screen.width == 1080
    assert obs.screen.height == 2400
    assert obs.phone.package_name == "com.android.settings"
    assert len(obs.elements) == 3
    assert len(obs.fingerprint) == 64
    assert obs.observed_at > 0
    assert obs.screenshot == PNG_MAGIC_BYTES
    assert obs.device_info is not None
    assert obs.device_info.name == "Mock Phone"

    # Validate dict-like access for backward compatibility
    assert obs["deviceId"] == "mock-serial-123"
    assert obs["screen"]["width"] == 1080
    assert obs["phone"]["packageName"] == "com.android.settings"
    assert len(obs["elements"]) == 3
    assert "fingerprint" in obs


@pytest.mark.asyncio
async def test_android_observer_device_disconnected():
    """Verify DeviceDisconnectedError is raised when device is missing."""
    mock_exec = MockADBExecutor(fail_get_state=True)
    observer = AndroidObserver(mock_exec)

    with pytest.raises(DeviceDisconnectedError, match="ADB connection failed"):
        await observer.observe()


@pytest.mark.asyncio
async def test_android_observer_device_not_ready():
    """Verify DeviceNotReadyError is raised when device is unauthorized or offline."""
    mock_exec = MockADBExecutor(state="offline")
    observer = AndroidObserver(mock_exec)

    with pytest.raises(DeviceNotReadyError, match="not ready"):
        await observer.observe()


@pytest.mark.asyncio
async def test_android_observer_timeout():
    """Verify ObservationTimeoutError is raised when an ADB command hangs."""
    mock_exec = MockADBExecutor(timeout_command="adb")
    observer = AndroidObserver(mock_exec)

    with pytest.raises(ObservationTimeoutError, match="timed out"):
        await observer.observe()
