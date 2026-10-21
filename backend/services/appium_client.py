"""Appium UiAutomator2 client for high-performance UI observation.

Maintains a reusable async HTTP client, per-device sessions, concurrency
locks, timeout protection, and lifecycle management with automatic recovery.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from backend.config import Settings, get_settings
from backend.services.run_logger import StageContext

logger = logging.getLogger(__name__)


class AppiumClientError(Exception):
    """Base exception for Appium client operations."""


class AppiumSessionError(AppiumClientError):
    """Raised when session creation, lookup, or deletion fails."""


class AppiumClient:
    """Manages Appium sessions and UI-tree source retrieval."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:4723",
        *,
        enabled: bool = True,
        session_timeout: float = 300.0,
        request_timeout: float = 5.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.enabled = enabled
        self.session_timeout = session_timeout
        self.request_timeout = request_timeout

        self._custom_client = http_client is not None
        self._http_client: httpx.AsyncClient | None = http_client

        # Per-device session tracking: serial -> session_id
        self._sessions: dict[str, str] = {}
        # Per-device concurrency locks to prevent race conditions
        self._device_locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    def _get_device_lock(self, serial: str) -> asyncio.Lock:
        key = serial or "default"
        if key not in self._device_locks:
            self._device_locks[key] = asyncio.Lock()
        return self._device_locks[key]

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.request_timeout, connect=2.0),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self._http_client

    async def is_healthy(self) -> bool:
        """Check if the Appium server is reachable and ready."""
        if not self.enabled:
            return False
        try:
            client = await self._get_http_client()
            resp = await client.get("/status")
            if resp.status_code == 200:
                data = resp.json()
                val = data.get("value", {})
                return val.get("ready", True) is not False
            return False
        except Exception as exc:
            logger.debug("Appium health check failed: %s", exc)
            return False

    async def create_session(self, serial: str = "") -> str:
        """Create a new Appium UiAutomator2 session for the specified device."""
        norm_serial = serial or "default"
        client = await self._get_http_client()

        caps: dict[str, Any] = {
            "capabilities": {
                "alwaysMatch": {
                    "platformName": "Android",
                    "appium:automationName": "UiAutomator2",
                    "appium:noReset": True,
                    "appium:ignoreHiddenApiPolicyError": True,
                    "appium:newCommandTimeout": int(self.session_timeout),
                }
            }
        }
        if serial and serial != "default":
            caps["capabilities"]["alwaysMatch"]["appium:udid"] = serial

        logger.info("Creating Appium session for device '%s' at %s", norm_serial, self.base_url)
        try:
            resp = await client.post("/session", json=caps, timeout=30.0)
            if resp.status_code not in (200, 201):
                err_msg = resp.text
                try:
                    err_msg = resp.json().get("value", {}).get("message", err_msg)
                except Exception:
                    pass
                raise AppiumSessionError(f"Failed to create Appium session ({resp.status_code}): {err_msg}")

            data = resp.json()
            session_id = data.get("value", {}).get("sessionId") or data.get("sessionId")
            if not session_id:
                raise AppiumSessionError(f"No sessionId in Appium response: {data}")

            async with self._registry_lock:
                self._sessions[norm_serial] = session_id

            logger.info("Created Appium session '%s' for device '%s'", session_id, norm_serial)
            return session_id

        except httpx.RequestError as exc:
            raise AppiumSessionError(f"HTTP error creating Appium session: {exc}") from exc

    async def get_or_create_session(self, serial: str = "") -> str:
        """Get an existing session ID or create a new one under a device lock."""
        norm_serial = serial or "default"
        dev_lock = self._get_device_lock(norm_serial)

        async with dev_lock:
            session_id = self._sessions.get(norm_serial)
            if session_id:
                return session_id
            return await self.create_session(serial)

    async def get_source(self, serial: str = "") -> str | None:
        """Fetch the UI hierarchy XML source for the specified device.

        Returns None if Appium is disabled, unavailable, or encounters an
        unrecoverable error (triggering ADB fallback).
        """
        if not self.enabled:
            return None

        norm_serial = serial or "default"
        dev_lock = self._get_device_lock(norm_serial)

        async with dev_lock:
            session_id = self._sessions.get(norm_serial)
            if not session_id:
                try:
                    session_id = await self.create_session(serial)
                except Exception as exc:
                    logger.debug("Could not establish Appium session for '%s': %s", norm_serial, exc)
                    return None

            client = await self._get_http_client()

            # First attempt
            xml = await self._fetch_source_internal(client, session_id)
            if xml is not None:
                return xml

            # Session might have expired or server restarted; recreate and retry once
            logger.debug("Appium session '%s' failed to fetch source; re-creating session", session_id)
            async with self._registry_lock:
                self._sessions.pop(norm_serial, None)

            try:
                new_session_id = await self.create_session(serial)
                return await self._fetch_source_internal(client, new_session_id)
            except Exception as exc:
                logger.warning("Failed to recover Appium session for device '%s': %s", norm_serial, exc)
                return None

    async def _fetch_source_internal(
        self, client: httpx.AsyncClient, session_id: str
    ) -> str | None:
        """Fetch source directly from an established session."""
        try:
            resp = await client.get(f"/session/{session_id}/source")
            if resp.status_code == 200:
                data = resp.json()
                raw_val = data.get("value")
                if isinstance(raw_val, str) and raw_val.strip():
                    return raw_val
                logger.debug("Appium source returned empty or non-string value")
                return None
            if resp.status_code in (404, 400):
                logger.debug("Appium source returned %d (invalid/expired session)", resp.status_code)
                return None
            logger.debug("Appium source returned unexpected status %d: %s", resp.status_code, resp.text)
            return None
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.debug("Appium HTTP request error during get_source: %s", exc)
            return None

    async def close_session(self, serial: str = "") -> bool:
        """Close and delete the active Appium session for a device."""
        norm_serial = serial or "default"
        dev_lock = self._get_device_lock(norm_serial)

        async with dev_lock:
            async with self._registry_lock:
                session_id = self._sessions.pop(norm_serial, None)

            if not session_id:
                return False

            client = await self._get_http_client()
            try:
                logger.info("Closing Appium session '%s' for device '%s'", session_id, norm_serial)
                resp = await client.delete(f"/session/{session_id}", timeout=10.0)
                return resp.status_code in (200, 204, 404)
            except Exception as exc:
                logger.debug("Error deleting Appium session '%s': %s", session_id, exc)
                return False

    async def close_all(self) -> None:
        """Close all active Appium sessions and release HTTP connections."""
        async with self._registry_lock:
            active_items = list(self._sessions.items())
            self._sessions.clear()

        client = self._http_client
        for serial, session_id in active_items:
            if client and not client.is_closed:
                try:
                    logger.info("Closing Appium session '%s' (device: %s)", session_id, serial)
                    await client.delete(f"/session/{session_id}", timeout=5.0)
                except Exception as exc:
                    logger.debug("Error deleting session '%s' on shutdown: %s", session_id, exc)

        if self._http_client and not self._custom_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None


# ── Global Singleton Provider ────────────────────────────────────────────────

_appium_client_instance: AppiumClient | None = None
_client_lock = asyncio.Lock()


def get_appium_client(settings: Settings | None = None) -> AppiumClient:
    """Get or instantiate the global AppiumClient singleton."""
    global _appium_client_instance
    if _appium_client_instance is None:
        cfg = settings or get_settings()
        _appium_client_instance = AppiumClient(
            base_url=cfg.appium_url,
            enabled=cfg.use_appium,
            session_timeout=cfg.appium_session_timeout,
            request_timeout=cfg.appium_request_timeout,
        )
    return _appium_client_instance


def set_appium_client(client: AppiumClient | None) -> None:
    """Set or clear the global AppiumClient singleton (useful for testing)."""
    global _appium_client_instance
    _appium_client_instance = client


async def close_appium_client() -> None:
    """Tear down all Appium sessions and the global client."""
    global _appium_client_instance
    if _appium_client_instance is not None:
        await _appium_client_instance.close_all()
        _appium_client_instance = None
