"""Unit and benchmark tests for ADBDevice.list_apps() caching and concurrency.

Covers cache hits, forced refresh, device isolation, label-resolution fallbacks,
concurrent dogpiling prevention, error isolation, and benchmark measurements.
"""

import asyncio
import time
import pytest

from backend.services.adb_device import ADBDevice


class FakeADBDevice(ADBDevice):
    """Subclass of ADBDevice that simulates ADB shell calls and counts invocations."""

    def __init__(
        self,
        serial: str,
        packages: list[str],
        dumpsys_delay: float = 0.01,
        fail_pm: bool = False,
    ) -> None:
        super().__init__(serial=serial)
        self.packages = packages
        self.dumpsys_delay = dumpsys_delay
        self.fail_pm = fail_pm
        self.pm_calls = 0
        self.dumpsys_calls = 0
        self.total_subprocess_calls = 0

    async def _shell(self, cmd: str, timeout: float = 15.0) -> str:
        self.total_subprocess_calls += 1
        if "pm list packages" in cmd:
            self.pm_calls += 1
            if self.fail_pm:
                raise RuntimeError("ADB shell pm failed")
            lines = [f"package:{pkg}" for pkg in self.packages]
            return "\n".join(lines)
        if "dumpsys package" in cmd:
            self.dumpsys_calls += 1
            if self.dumpsys_delay > 0:
                await asyncio.sleep(self.dumpsys_delay)
            pkg_name = cmd.split()[-1]
            if pkg_name.startswith("broken."):
                raise RuntimeError("dumpsys error")
            if pkg_name.startswith("nolabel."):
                return "Package [nolabel.app] (1234):"
            return f"  non-localized-label: Custom {pkg_name.split('.')[-1].title()}\n"
        return ""


@pytest.fixture(autouse=True)
def clean_cache():
    """Ensure clean app cache before and after every test."""
    ADBDevice.clear_apps_cache()
    yield
    ADBDevice.clear_apps_cache()


@pytest.mark.asyncio
async def test_apps_cache_hit():
    """Verify first call performs ADB calls, second call hits cache with 0 ADB calls."""
    packages = ["com.example.app1", "com.example.app2"]
    dev = FakeADBDevice(serial="device-1", packages=packages)

    # 1. Cold lookup
    apps1 = await dev.list_apps()
    cold_subprocess_calls = dev.total_subprocess_calls
    assert cold_subprocess_calls > 0
    assert any(a["packageName"] == "com.example.app1" for a in apps1)

    # 2. Warm cached lookup on same device
    dev.total_subprocess_calls = 0
    apps2 = await dev.list_apps()
    assert dev.total_subprocess_calls == 0
    assert apps1 == apps2


@pytest.mark.asyncio
async def test_apps_force_refresh():
    """Verify force_refresh=True bypasses cache and queries pm list packages."""
    packages = ["com.example.app1"]
    dev = FakeADBDevice(serial="device-1", packages=packages)

    await dev.list_apps()
    assert dev.pm_calls == 1

    # Normal call uses cache
    await dev.list_apps()
    assert dev.pm_calls == 1

    # Force refresh
    dev.packages = ["com.example.app1", "com.example.app2"]
    refreshed = await dev.list_apps(force_refresh=True)
    assert dev.pm_calls == 2
    assert any(a["packageName"] == "com.example.app2" for a in refreshed)


@pytest.mark.asyncio
async def test_apps_device_isolation():
    """Verify caches are isolated by device serial."""
    dev_a = FakeADBDevice(serial="device-A", packages=["com.app.alpha"])
    dev_b = FakeADBDevice(serial="device-B", packages=["com.app.beta"])

    apps_a = await dev_a.list_apps()
    apps_b = await dev_b.list_apps()

    # Alpha should be in dev_a but not in dev_b's 3rd party list
    assert any(a["packageName"] == "com.app.alpha" for a in apps_a)
    assert not any(a["packageName"] == "com.app.alpha" for a in apps_b)
    assert any(a["packageName"] == "com.app.beta" for a in apps_b)


@pytest.mark.asyncio
async def test_apps_label_resolution_fallback():
    """Verify broken dumpsys and missing labels fall back to title-cased suffix."""
    packages = ["broken.bad_app", "nolabel.my_tool"]
    dev = FakeADBDevice(serial="device-1", packages=packages)

    apps = await dev.list_apps()
    by_pkg = {a["packageName"]: a["label"] for a in apps}

    # "broken.bad_app" should fall back to "Bad App"
    assert by_pkg["broken.bad_app"] == "Bad App"
    # "nolabel.my_tool" should fall back to "My Tool"
    assert by_pkg["nolabel.my_tool"] == "My Tool"


@pytest.mark.asyncio
async def test_apps_concurrent_calls():
    """Verify concurrent callers share a single refresh and do not duplicate subprocess calls."""
    packages = ["com.app.x1", "com.app.x2", "com.app.x3"]
    dev = FakeADBDevice(serial="device-concurrent", packages=packages, dumpsys_delay=0.02)

    # Launch 5 concurrent list_apps() requests
    results = await asyncio.gather(*[dev.list_apps() for _ in range(5)])

    # All results must be identical
    for r in results:
        assert r == results[0]

    # Only 1 pm list packages call should have occurred
    assert dev.pm_calls == 1


@pytest.mark.asyncio
async def test_apps_error_not_cached():
    """Verify failed refreshes do not overwrite cache with bad data."""
    packages = ["com.app.valid"]
    dev = FakeADBDevice(serial="device-err", packages=packages)

    # Successful first call
    apps1 = await dev.list_apps()
    assert len(apps1) > 0

    # Device now fails
    dev.fail_pm = True
    with pytest.raises(RuntimeError, match="ADB shell pm failed"):
        await dev.list_apps(force_refresh=True)

    # Unforced call should still return previous valid cache
    dev.fail_pm = False
    apps2 = await dev.list_apps()
    assert apps2 == apps1


@pytest.mark.asyncio
async def test_apps_performance_benchmark():
    """Measure duration and subprocess count for cold vs warm cached lookup."""
    # 20 third-party packages to simulate a real device
    packages = [f"com.test.app{i}" for i in range(20)]
    dev = FakeADBDevice(serial="device-bench", packages=packages, dumpsys_delay=0.005)

    # 1. Cold lookup
    t0 = time.perf_counter()
    cold_apps = await dev.list_apps()
    cold_time = time.perf_counter() - t0
    cold_subprocesses = dev.total_subprocess_calls

    # 2. Warm cached lookup
    dev.total_subprocess_calls = 0
    t1 = time.perf_counter()
    warm_apps = await dev.list_apps()
    warm_time = time.perf_counter() - t1
    warm_subprocesses = dev.total_subprocess_calls

    assert len(cold_apps) == len(warm_apps)
    assert warm_subprocesses == 0
    assert warm_time < cold_time

    print(
        f"\n[BENCHMARK] Cold: {cold_time*1000:.2f}ms ({cold_subprocesses} subprocesses) | "
        f"Warm: {warm_time*1000:.2f}ms ({warm_subprocesses} subprocesses) | "
        f"Speedup: {cold_time / max(warm_time, 1e-6):.1f}x"
    )
