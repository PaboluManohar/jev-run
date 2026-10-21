import asyncio
import statistics
import time

from backend.config import get_settings
from backend.models.device import ScreenBounds
from backend.services.adb_device import ADBDevice
from backend.services.appium_client import AppiumClient, get_appium_client
from backend.services.observer import AndroidObserver, parse_ui_hierarchy


async def run_benchmark():
    settings = get_settings()
    serial = settings.adb_device_serial or "f43dbe44"
    device = ADBDevice(serial=serial)
    await device.assert_ready()
    appium = get_appium_client()

    print(f"Target device: {serial}")

    # ── 1. Benchmark Appium ─────────────────────────────
    print("\n=== Appium UI-Tree Extraction Benchmark ===")
    appium_obs = AndroidObserver(device, appium_client=appium)
    # Warm-up request
    t0 = time.perf_counter()
    w_xml = await appium_obs._capture_ui_xml()
    w_time = (time.perf_counter() - t0) * 1000
    print(f"Appium warm-up: {w_time:.1f}ms (len: {len(w_xml)})")

    appium_latencies = []
    appium_xmls = []
    for i in range(12):
        t0 = time.perf_counter()
        xml = await appium_obs._capture_ui_xml()
        dur = (time.perf_counter() - t0) * 1000
        appium_latencies.append(dur)
        appium_xmls.append(xml)
        print(f"Appium run {i+1:02d}: {dur:.1f}ms")
        await asyncio.sleep(0.05)

    # Close Appium session so Android UiAutomation is released back for ADB dump
    print("\nClosing Appium session to release UiAutomation service for ADB...")
    await appium.close_session(serial)
    await asyncio.sleep(1.0)

    # ── 2. Benchmark ADB ────────────────────────────────
    print("\n=== ADB uiautomator dump Benchmark ===")
    disabled_appium = AppiumClient(enabled=False)
    adb_obs = AndroidObserver(device, appium_client=disabled_appium)
    # Warm-up request
    t0 = time.perf_counter()
    w_xml_adb = await adb_obs._capture_ui_xml()
    w_time_adb = (time.perf_counter() - t0) * 1000
    print(f"ADB warm-up: {w_time_adb:.1f}ms (len: {len(w_xml_adb)})")

    adb_latencies = []
    adb_xmls = []
    for i in range(12):
        t0 = time.perf_counter()
        xml = await adb_obs._capture_ui_xml()
        dur = (time.perf_counter() - t0) * 1000
        adb_latencies.append(dur)
        adb_xmls.append(xml)
        print(f"ADB run {i+1:02d}: {dur:.1f}ms")
        await asyncio.sleep(0.05)

    # ── 3. Statistical Analysis ─────────────────────────
    def compute_stats(data):
        s = sorted(data)
        p95_idx = int(len(s) * 0.95)
        return {
            "min": min(s),
            "max": max(s),
            "median": statistics.median(s),
            "p95": s[min(p95_idx, len(s) - 1)],
            "mean": statistics.mean(s),
        }

    app_stats = compute_stats(appium_latencies)
    adb_stats = compute_stats(adb_latencies)

    print("\n=== LATENCY COMPARISON TABLE (12 warmed-up runs) ===")
    print(f"{'Metric':<10} | {'ADB (ms)':<12} | {'Appium (ms)':<12} | {'Speedup':<10}")
    print("-" * 52)
    for m in ["min", "median", "p95", "max", "mean"]:
        adb_val = adb_stats[m]
        app_val = app_stats[m]
        speedup = adb_val / app_val
        print(f"{m.capitalize():<10} | {adb_val:<12.1f} | {app_val:<12.1f} | {speedup:<10.1f}x")

    # ── 4. Verify Usability & Compatibility ─────────────
    screen = ScreenBounds(width=1080, height=2400)
    app_elems = parse_ui_hierarchy(appium_xmls[-1], screen)
    adb_elems = parse_ui_hierarchy(adb_xmls[-1], screen)
    print(f"\nAppium elements extracted: {len(app_elems)}")
    print(f"ADB elements extracted:    {len(adb_elems)}")
    print("Sample Appium elements:", [(e.id, e.class_name, e.label or e.text) for e in app_elems[:3]])
    print("Sample ADB elements:   ", [(e.id, e.class_name, e.label or e.text) for e in adb_elems[:3]])


if __name__ == "__main__":
    asyncio.run(run_benchmark())
