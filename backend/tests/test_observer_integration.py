"""Live integration tests for Android observation on real or emulated devices.

Executes only when an active Android device is connected via ADB.
Gracefully skips when running in offline CI environments without hardware.
"""

import os
import subprocess
import pytest
from backend.services.adb_device import ADBDevice
from backend.services.observer import AndroidObserver


def is_live_device_available() -> bool:
    """Check if at least one Android device is connected and authorized."""
    if os.environ.get("LIVE_DEVICE_TEST") != "1":
        return False
    try:
        res = subprocess.run(
            ["adb", "get-state"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return res.returncode == 0 and res.stdout.strip() == "device"
    except Exception:
        return False


@pytest.mark.asyncio
async def test_live_device_observe():
    """Live integration test: capture observation from a real Android device."""
    if not is_live_device_available():
        pytest.skip("No online Android device or emulator available via ADB.")

    device = ADBDevice()
    observer = AndroidObserver(device)

    obs = await observer.observe(include_screenshot=True)

    printable_items = [ "device_id", "screen", "elements", "fingerprint", "observed_at"]
    
    for item in printable_items:
        print("=====================================")
        print(f"{item}: {getattr(obs, item)}")


    # Validate live observation
    assert obs.device_id, "Observation must have a valid device_id"
    assert obs.screen.width > 0, "Screen width must be positive"
    assert obs.screen.height > 0, "Screen height must be positive"
    assert isinstance(obs.elements, list), "Elements must be a list"
    assert obs.fingerprint, "Observation must have a non-empty fingerprint"
    assert obs.observed_at > 0, "Observation must have a valid timestamp"

    # Validate live screenshot
    assert obs.screenshot is not None, "Screenshot must be captured"
    assert obs.screenshot[:8] == b"\x89PNG\r\n\x1a\n", "Screenshot must be valid PNG bytes"
