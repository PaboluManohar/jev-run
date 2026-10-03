"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.config import Settings, get_settings
from backend.services.adb_device import ADBDevice

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(settings: Settings = Depends(get_settings)):
    """Check ADB and device connectivity."""
    checks: list[dict] = []

    # ADB reachable
    try:
        device = ADBDevice(
            serial=settings.adb_device_serial, adb_path=settings.adb_path
        )
        devices = await device.list_devices()
        checks.append({"name": "ADB", "ok": True, "detail": f"{len(devices)} device(s)"})
    except Exception as exc:
        checks.append({"name": "ADB", "ok": False, "detail": str(exc)})
        return {"status": "unhealthy", "checks": checks}

    # Device ready
    try:
        info = await device.assert_ready()
        checks.append({
            "name": "Device",
            "ok": True,
            "detail": f"{info['name']}: {info['state']}",
        })
    except Exception as exc:
        checks.append({"name": "Device", "ok": False, "detail": str(exc)})

    # UI observation
    try:
        obs = await device.observe()
        checks.append({
            "name": "UI Observation",
            "ok": True,
            "detail": (
                f"{len(obs['elements'])} elements, "
                f"{obs['screen']['width']}×{obs['screen']['height']}"
            ),
        })
    except Exception as exc:
        checks.append({"name": "UI Observation", "ok": False, "detail": str(exc)})

    # TypeSafe key present
    checks.append({
        "name": "TypeSafe API Key",
        "ok": bool(settings.typesafe_api_key),
        "detail": "Set" if settings.typesafe_api_key else "Missing — set TYPESAFE_API_KEY",
    })

    all_ok = all(c["ok"] for c in checks)
    return {"status": "healthy" if all_ok else "degraded", "checks": checks}
