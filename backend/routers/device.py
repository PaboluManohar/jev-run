"""Device information and screenshot endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from backend.config import Settings, get_settings
from backend.services.adb_device import ADBDevice

router = APIRouter(tags=["device"])


def _get_device(settings: Settings = Depends(get_settings)) -> ADBDevice:
    return ADBDevice(
        serial=settings.adb_device_serial,
        adb_path=settings.adb_path,
        text_completion_mode=settings.text_completion_mode,
    )


@router.get("/device")
async def get_device_info(device: ADBDevice = Depends(_get_device)):
    """Return device information matching the frontend's ``Device`` type."""
    return await device.get_device_info()


@router.get("/device/screenshot")
async def get_screenshot(device: ADBDevice = Depends(_get_device)):
    """Capture the device screen as a PNG image."""
    png_bytes = await device.screenshot()
    return Response(content=png_bytes, media_type="image/png")


@router.get("/apps")
async def list_apps(device: ADBDevice = Depends(_get_device)):
    """List installed apps with package names and labels."""
    return await device.list_apps()


@router.get("/devices")
async def list_devices(device: ADBDevice = Depends(_get_device)):
    """List all connected ADB devices."""
    return await device.list_devices()
