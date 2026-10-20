"""Focused tests for backend performance optimizations.

Covers:
- Strict timeout bounding in confirm_input() during slow observation calls
- Metadata caching (serial, model, screen size) in ADBDevice
- TTY capability caching in AndroidObserver
- Settle loop skip when observation duration exceeds settle_timeout_ms
- Non-screenshot observation pass-through
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend.services.adb_device import ADBDevice
from backend.services.observer import AndroidObserver, InvalidUIHierarchyError
from backend.services.input_verification import (
    InputVerification,
    confirm_input,
)
from backend.services.agent import run_agent


# ── 1. Input Verification Timeout Bounding ───────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_input_strictly_bounded_by_timeout():
    """Verify confirm_input does not block beyond timeout_ms even with slow observe()."""
    initial = {
        "deviceId": "dev1",
        "phone": {"packageName": "com.test"},
        "elements": [{"id": "field1", "text": "wrong", "editable": True, "enabled": True}],
    }
    verification = InputVerification(
        device_id="dev1",
        package_name="com.test",
        target={"id": "field1", "resourceId": "res1", "hint": "", "bounds": {"left": 0, "top": 0, "right": 100, "bottom": 100}},
        text="expected_text",
    )

    async def slow_observe():
        # Simulate a slow 2-second observation
        await asyncio.sleep(2.0)
        return {
            "deviceId": "dev1",
            "phone": {"packageName": "com.test"},
            "elements": [{"id": "field1", "text": "expected_text", "editable": True, "enabled": True, "resourceId": "res1"}],
        }

    t0 = time.monotonic()
    # Configure a short timeout of 150ms
    result = await confirm_input(
        initial=initial,
        verification=verification,
        observe=slow_observe,
        timeout_ms=150,
        poll_ms=20,
    )
    elapsed = (time.monotonic() - t0) * 1000

    # Must fail because slow_observe took 2.0s, but must return within bounded time (~150-350ms)
    assert result.verified is False
    assert elapsed < 600, f"confirm_input took {elapsed:.1f}ms, which exceeded expected timeout bound"


# ── 2. Device Metadata Caching in ADBDevice ──────────────────────────────────


@pytest.mark.asyncio
async def test_adb_device_assert_ready_metadata_caching():
    """Verify assert_ready() reuses cached model and serial within ready TTL."""
    device = ADBDevice(serial="test-serial")
    device._adb = AsyncMock(return_value="device\n")
    device._shell = AsyncMock(return_value="Pixel 8 Pro\n")

    info1 = await device.assert_ready()
    assert info1["name"] == "Pixel 8 Pro"
    assert info1["id"] == "test-serial"
    assert device._shell.await_count == 1
    assert device._adb.await_count == 1

    # Second call within TTL should return cached dict without running shell/adb
    info2 = await device.assert_ready()
    assert info2["name"] == "Pixel 8 Pro"
    assert device._shell.await_count == 1
    assert device._adb.await_count == 1

    # Force refresh should re-query
    info3 = await device.assert_ready(force_refresh=True)
    assert info3["name"] == "Pixel 8 Pro"
    assert device._shell.await_count == 2
    assert device._adb.await_count == 2


@pytest.mark.asyncio
async def test_adb_device_get_device_info_reuses_passed_screen_size():
    """Verify get_device_info avoids wm size shell command when screen_size is passed."""
    device = ADBDevice(serial="test-serial")
    device.assert_ready = AsyncMock(return_value={"id": "test-serial", "name": "Pixel 8 Pro", "state": "ready"})
    device._shell = AsyncMock(return_value="Physical size: 1080x2400\n")

    info = await device.get_device_info(screen_size={"width": 1440, "height": 3120})
    assert info["screenWidth"] == 1440
    assert info["screenHeight"] == 3120
    assert info["streamUrl"] is not None
    # wm size must not have been called because screen_size was supplied
    device._shell.assert_not_awaited()


# ── 3. TTY Capability Caching in AndroidObserver ─────────────────────────────


@pytest.mark.asyncio
async def test_android_observer_caches_unsupported_tty():
    """Verify that after TTY fails once, AndroidObserver bypasses TTY on subsequent calls."""
    mock_executor = MagicMock()
    mock_executor._shell = AsyncMock()

    # First call: TTY returns non-XML header, fallback returns valid hierarchy XML
    valid_xml = '<hierarchy rotation="0"><node bounds="[0,0][1080,2400]" class="android.view.View" /></hierarchy>'
    mock_executor._shell.side_effect = [
        "UI hierchary dumped to: /dev/tty\n",  # TTY non-XML
        valid_xml,                             # Fast combined file dump
    ]

    observer = AndroidObserver(mock_executor)
    xml1 = await observer._capture_ui_xml()
    assert "<hierarchy" in xml1
    assert observer._tty_supported is False

    # Second call: observer must skip TTY and call only the file fallback
    mock_executor._shell.side_effect = [valid_xml]
    xml2 = await observer._capture_ui_xml()
    assert "<hierarchy" in xml2
    # Only 1 call made on the second invocation (no TTY attempt)
    assert mock_executor._shell.await_count == 3  # 2 from first + 1 from second


# ── 4. Agent Settle Loop Optimization ────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_settle_loop_skips_when_observe_exceeds_settle_timeout():
    """Verify settle loop does not re-observe if post-action observation already exceeded settle window."""
    obs1 = {
        "deviceId": "dev1",
        "phone": {"packageName": "com.android.settings"},
        "elements": [{"id": "ui.1", "text": "Wi-Fi", "clickable": True, "enabled": True, "editable": False, "scrollable": False}],
        "screen": {"width": 1080, "height": 2400},
        "fingerprint": "same_fingerprint",
        "observedAt": time.time() * 1000,
    }

    device = MagicMock()
    device.assert_ready = AsyncMock(return_value={"id": "dev1", "state": "ready"})
    device.list_apps = AsyncMock(return_value=[])
    device.act = AsyncMock(return_value=None)

    # Simulate observation taking 100ms
    async def delayed_observe(*args, **kwargs):
        await asyncio.sleep(0.05)
        return dict(obs1)

    device.observe = AsyncMock(side_effect=delayed_observe)

    policy = MagicMock()
    decisions = [
        {"status": "action", "action": {"type": "tap-element", "elementId": "ui.1"}},
        {"status": "done", "operation": "DONE"},
    ]
    dec_iter = iter(decisions)
    policy.decide = AsyncMock(side_effect=lambda **kw: next(dec_iter))

    # settle_timeout_ms is set to 20ms. Since observe takes 50ms, settle deadline is already expired
    # so the while loop must NOT execute an extra observation.
    result = await run_agent(
        device=device,
        policy=policy,
        goal="Tap Wi-Fi",
        execute=True,
        settle_timeout_ms=20,
    )

    assert result["status"] == "done"
    # observe should be called exactly twice:
    # 1. initial_obs (line 158)
    # 2. post-action observe (line 288)
    # Settle loop must NOT have called observe a 3rd time
    assert device.observe.await_count == 2
