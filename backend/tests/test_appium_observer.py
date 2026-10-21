"""Unit tests for AppiumClient, AndroidObserver integration, session lifecycle,
concurrency, error recovery, and ADB fallback behavior.
"""

from __future__ import annotations

import asyncio
from typing import Any
import httpx
import pytest

from backend.models.device import ScreenBounds
from backend.services.appium_client import (
    AppiumClient,
    AppiumSessionError,
)
from backend.services.observer import (
    AndroidObserver,
    parse_ui_hierarchy,
)

SAMPLE_APPIUM_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy index="0" class="hierarchy" rotation="0" width="1080" height="2400">
  <android.widget.FrameLayout index="0" package="com.android.settings" class="android.widget.FrameLayout" text="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" bounds="[0,0][1080,2400]">
    <android.widget.TextView index="0" text="Connected devices" resource-id="android:id/title" class="android.widget.TextView" package="com.android.settings" content-desc="Device settings" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" bounds="[64,300][900,420]">
    </android.widget.TextView>
    <android.widget.EditText index="1" text="" resource-id="com.android.settings:id/search_box" class="android.widget.EditText" package="com.android.settings" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="true" scrollable="false" bounds="[64,150][1016,250]">
    </android.widget.EditText>
  </android.widget.FrameLayout>
</hierarchy>
"""

SAMPLE_ADB_FALLBACK_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.android.settings" content-desc="" bounds="[0,0][1080,2400]">
    <node index="0" text="ADB Fallback Node" resource-id="android:id/title" class="android.widget.TextView" package="com.android.settings" content-desc="" clickable="true" bounds="[64,300][900,420]" />
  </node>
</hierarchy>
"""


class MockADBExecutor:
    """Mock ADB runner providing predictable shell and adb outputs."""

    def __init__(self, ui_xml: str = SAMPLE_ADB_FALLBACK_XML) -> None:
        self.serial = "mock-serial-456"
        self.ui_xml = ui_xml
        self.adb_calls: list[str] = []

    async def _adb(self, *args: str, timeout: float = 15.0) -> str:
        self.adb_calls.append(" ".join(args))
        if args and args[0] == "get-state":
            return "device"
        if args and args[0] == "get-serialno":
            return self.serial
        return ""

    async def _adb_bytes(self, *args: str, timeout: float = 15.0) -> bytes:
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    async def _shell(self, cmd: str, timeout: float = 15.0) -> str:
        self.adb_calls.append(cmd)
        if "uiautomator dump" in cmd or "cat" in cmd:
            return self.ui_xml
        if "dumpsys window" in cmd:
            return "mCurrentFocus=Window{123 u0 com.android.settings/com.android.settings.Settings}"
        if "dumpsys input_method" in cmd:
            return "mInputShown=false"
        if "wm size" in cmd:
            return "Physical size: 1080x2400"
        return ""

    async def get_device_info(self, screen_size: dict[str, int] | None = None) -> dict[str, str]:
        return {"id": self.serial, "name": "Mock Phone"}


# ── AppiumClient Tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_appium_client_session_lifecycle():
    """Verify session creation, source retrieval, and deletion with mock transport."""
    created_sessions: list[str] = []
    deleted_sessions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url.endswith("/session"):
            session_id = "test-session-123"
            created_sessions.append(session_id)
            return httpx.Response(200, json={"value": {"sessionId": session_id}})
        if request.method == "GET" and "/session/test-session-123/source" in url:
            return httpx.Response(200, json={"value": SAMPLE_APPIUM_XML})
        if request.method == "DELETE" and "/session/test-session-123" in url:
            deleted_sessions.append("test-session-123")
            return httpx.Response(200, json={"value": None})
        if request.method == "GET" and url.endswith("/status"):
            return httpx.Response(200, json={"value": {"ready": True}})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4723") as mock_http:
        client = AppiumClient(base_url="http://127.0.0.1:4723", http_client=mock_http)

        assert await client.is_healthy() is True

        # First source call creates session
        source = await client.get_source("mock-serial-456")
        assert source == SAMPLE_APPIUM_XML
        assert created_sessions == ["test-session-123"]

        # Second source call reuses session
        source2 = await client.get_source("mock-serial-456")
        assert source2 == SAMPLE_APPIUM_XML
        assert len(created_sessions) == 1

        # Close session
        closed = await client.close_session("mock-serial-456")
        assert closed is True
        assert deleted_sessions == ["test-session-123"]


@pytest.mark.asyncio
async def test_appium_client_expired_session_recovery():
    """Verify that an expired session (404) is automatically recreated on retry."""
    session_counter = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_counter
        url = str(request.url)
        if request.method == "POST" and url.endswith("/session"):
            session_counter += 1
            return httpx.Response(200, json={"value": {"sessionId": f"sess-{session_counter}"}})
        if request.method == "GET" and "/session/sess-1/source" in url:
            # First session is expired/invalid
            return httpx.Response(404, json={"value": {"error": "invalid session id"}})
        if request.method == "GET" and "/session/sess-2/source" in url:
            # Second recreated session succeeds
            return httpx.Response(200, json={"value": SAMPLE_APPIUM_XML})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4723") as mock_http:
        client = AppiumClient(base_url="http://127.0.0.1:4723", http_client=mock_http)

        source = await client.get_source("mock-device")
        assert source == SAMPLE_APPIUM_XML
        # Verified that it created session 1, encountered 404, recreated session 2, and succeeded
        assert session_counter == 2


@pytest.mark.asyncio
async def test_appium_client_concurrency_same_device():
    """Verify concurrent requests for the same device serialize under the device lock."""
    creation_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal creation_attempts
        url = str(request.url)
        if request.method == "POST" and url.endswith("/session"):
            creation_attempts += 1
            return httpx.Response(200, json={"value": {"sessionId": "concurrent-sess"}})
        if request.method == "GET" and "/session/concurrent-sess/source" in url:
            return httpx.Response(200, json={"value": SAMPLE_APPIUM_XML})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4723") as mock_http:
        client = AppiumClient(base_url="http://127.0.0.1:4723", http_client=mock_http)

        # 5 concurrent requests for the same device
        results = await asyncio.gather(*[client.get_source("same-device") for _ in range(5)])

        assert all(r == SAMPLE_APPIUM_XML for r in results)
        # Lock ensures session was created exactly once despite concurrent calls
        assert creation_attempts == 1


# ── Observer Integration & Fallback Tests ────────────────────────────────────


@pytest.mark.asyncio
async def test_android_observer_with_appium_success():
    """Verify AndroidObserver uses Appium when healthy and produces valid Observation."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url.endswith("/session"):
            return httpx.Response(200, json={"value": {"sessionId": "obs-sess"}})
        if request.method == "GET" and "/session/obs-sess/source" in url:
            return httpx.Response(200, json={"value": SAMPLE_APPIUM_XML})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4723") as mock_http:
        appium = AppiumClient(base_url="http://127.0.0.1:4723", http_client=mock_http)
        executor = MockADBExecutor()
        observer = AndroidObserver(executor, appium_client=appium)

        obs = await observer.observe(include_screenshot=False)

        assert obs.device_id == "mock-serial-456"
        assert len(obs.elements) == 2
        # Verify first element is the TextView from Appium XML
        assert obs.elements[0].text == "Connected devices"
        assert obs.elements[0].clickable is True
        # Verify second element is the EditText
        assert obs.elements[1].resource_id == "com.android.settings:id/search_box"
        assert obs.elements[1].editable is True
        # Verify ADB uiautomator dump was NOT invoked because Appium succeeded
        assert not any("uiautomator dump" in call for call in executor.adb_calls)


@pytest.mark.asyncio
async def test_android_observer_fallback_on_appium_failure():
    """Verify observer falls back to ADB when Appium encounters connection error."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused to Appium")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4723") as mock_http:
        appium = AppiumClient(base_url="http://127.0.0.1:4723", http_client=mock_http)
        executor = MockADBExecutor()
        observer = AndroidObserver(executor, appium_client=appium)

        obs = await observer.observe(include_screenshot=False)

        assert obs.device_id == "mock-serial-456"
        assert len(obs.elements) == 1
        # Element comes from ADB fallback XML
        assert obs.elements[0].text == "ADB Fallback Node"
        # Verify ADB uiautomator dump was invoked as fallback
        assert any("uiautomator dump" in call for call in executor.adb_calls)


@pytest.mark.asyncio
async def test_android_observer_fallback_on_malformed_appium_xml():
    """Verify observer falls back to ADB when Appium returns malformed/empty XML."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url.endswith("/session"):
            return httpx.Response(200, json={"value": {"sessionId": "bad-xml-sess"}})
        if request.method == "GET" and "/session/bad-xml-sess/source" in url:
            # Non-XML return
            return httpx.Response(200, json={"value": "<html>Not valid Android hierarchy</html>"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4723") as mock_http:
        appium = AppiumClient(base_url="http://127.0.0.1:4723", http_client=mock_http)
        executor = MockADBExecutor()
        observer = AndroidObserver(executor, appium_client=appium)

        obs = await observer.observe(include_screenshot=False)

        # Successfully parsed elements via fallback
        assert len(obs.elements) == 1
        assert obs.elements[0].text == "ADB Fallback Node"
        assert any("uiautomator dump" in call for call in executor.adb_calls)


@pytest.mark.asyncio
async def test_parse_ui_hierarchy_compatibility():
    """Verify parse_ui_hierarchy handles both standard node tags and Appium class tags."""
    screen = ScreenBounds(width=1080, height=2400)
    elements = parse_ui_hierarchy(SAMPLE_APPIUM_XML, screen)

    assert len(elements) == 2
    el0 = elements[0]
    assert el0.id == "ui.0"
    assert el0.text == "Connected devices"
    assert el0.label == "Device settings"
    assert el0.class_name == "android.widget.TextView"
    assert el0.clickable is True

    el1 = elements[1]
    assert el1.id == "ui.1"
    assert el1.class_name == "android.widget.EditText"
    assert el1.editable is True
    assert el1.focused is True
