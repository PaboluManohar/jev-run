"""Unit tests for the TypeSafe System One decision-model integration.

All tests use ``unittest.mock`` to patch the SDK — no real API key required.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.models.decision import ActionOption, DecisionRequest, DecisionResponse
from backend.services.decision_model import (
    DecisionModelError,
    DecisionModelService,
    InvalidResponseError,
    MissingConfigError,
    _DONE_KEY,
    close_client,
    get_service,
    init_client,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_API_KEY = "sk-test-deadbeef"  # noqa: S105 (test credential)
_FAKE_MODEL = "jev-test"

_ACTIONS: list[ActionOption] = [
    ActionOption(id="tap_login", description="Tap the Login button"),
    ActionOption(id="type_email", description="Type email address"),
    ActionOption(id="scroll_down", description="Scroll down"),
]


def _make_settings(**overrides: Any) -> Settings:
    """Return a Settings instance with the TypeSafe key pre-filled."""
    return Settings(
        typesafe_api_key=overrides.pop("typesafe_api_key", _FAKE_API_KEY),
        typesafe_default_model=overrides.pop("typesafe_default_model", _FAKE_MODEL),
        **overrides,
    )


def _make_choice_answer(choice: str, confidence: float = 0.85) -> MagicMock:
    """Build a mock ChoiceAnswer."""
    answer = MagicMock()
    answer.choice = choice
    answer.confidence = confidence
    # Provide a plausible probability distribution.
    ids = [a.id for a in _ACTIONS] + [_DONE_KEY]
    probs = {k: (0.85 if k == choice else 0.05) for k in ids}
    answer.probabilities = probs
    return answer


def _make_noul_answer(noul: float) -> MagicMock:
    answer = MagicMock()
    answer.noul = noul
    return answer


def _make_sdk_response(
    choice: str,
    confidence: float = 0.85,
    task_complete_noul: float = 0.05,
    model: str = _FAKE_MODEL,
) -> MagicMock:
    """Build a mock SystemOneResponse."""
    response = MagicMock()
    response.model = model
    response.choices = {"action": _make_choice_answer(choice, confidence)}
    response.nouls = {"task_complete": _make_noul_answer(task_complete_noul)}
    return response


# ---------------------------------------------------------------------------
# DecisionModelService unit tests (no HTTP)
# ---------------------------------------------------------------------------


class TestDecisionModelService:
    """Unit tests for DecisionModelService.decide()."""

    def _make_service(self, **settings_overrides: Any) -> DecisionModelService:
        settings = _make_settings(**settings_overrides)
        service = DecisionModelService(settings=settings)
        return service

    @pytest.mark.asyncio
    async def test_valid_action_selected(self):
        """Model returns a valid action ID → DecisionResponse with that ID."""
        service = self._make_service()
        mock_response = _make_sdk_response(choice="tap_login")

        with patch.object(
            service._client, "system_one", new=AsyncMock(return_value=mock_response)
        ):
            result = await service.decide(
                task="Log in to the app",
                observation={"screen": "login_screen"},
                actions=_ACTIONS,
            )

        assert isinstance(result, DecisionResponse)
        assert result.selected_action_id == "tap_login"
        assert result.done is False
        assert result.confidence == pytest.approx(0.85)
        assert result.model == _FAKE_MODEL
        assert result.latency_ms is not None
        # __done__ key should be stripped from probabilities
        assert _DONE_KEY not in (result.probabilities or {})

    @pytest.mark.asyncio
    async def test_done_via_done_key(self):
        """Model chooses __done__ → DecisionResponse with done=True."""
        service = self._make_service()
        mock_response = _make_sdk_response(choice=_DONE_KEY, confidence=0.95)

        with patch.object(
            service._client, "system_one", new=AsyncMock(return_value=mock_response)
        ):
            result = await service.decide(
                task="Open settings",
                observation={},
                actions=_ACTIONS,
            )

        assert result.done is True
        assert result.selected_action_id is None

    @pytest.mark.asyncio
    async def test_done_via_noul(self):
        """High task_complete noul (≥ 0.9) → done=True even if action chosen."""
        service = self._make_service()
        mock_response = _make_sdk_response(
            choice="scroll_down",
            task_complete_noul=0.95,
        )

        with patch.object(
            service._client, "system_one", new=AsyncMock(return_value=mock_response)
        ):
            result = await service.decide(
                task="Open settings",
                observation={},
                actions=_ACTIONS,
            )

        assert result.done is True
        assert result.selected_action_id is None

    @pytest.mark.asyncio
    async def test_invalid_action_id_raises(self):
        """Model returns an unknown action ID → InvalidResponseError."""
        service = self._make_service()
        mock_response = _make_sdk_response(choice="nonexistent_action")

        with patch.object(
            service._client, "system_one", new=AsyncMock(return_value=mock_response)
        ):
            with pytest.raises(InvalidResponseError, match="nonexistent_action"):
                await service.decide(
                    task="Do something",
                    observation={},
                    actions=_ACTIONS,
                )

    @pytest.mark.asyncio
    async def test_api_timeout_raises_decision_model_error(self):
        """SDK TypeSafeAPITimeoutError → DecisionModelError (not propagated raw)."""
        from typesafe_sdk import TypeSafeAPITimeoutError

        service = self._make_service()

        with patch.object(
            service._client,
            "system_one",
            new=AsyncMock(side_effect=TypeSafeAPITimeoutError(30.0)),
        ):
            with pytest.raises(DecisionModelError, match="timed out"):
                await service.decide(
                    task="Do something",
                    observation={},
                    actions=_ACTIONS,
                )

    @pytest.mark.asyncio
    async def test_api_connection_error_raises_decision_model_error(self):
        """SDK TypeSafeAPIConnectionError → DecisionModelError."""
        from typesafe_sdk import TypeSafeAPIConnectionError

        service = self._make_service()

        with patch.object(
            service._client,
            "system_one",
            new=AsyncMock(side_effect=TypeSafeAPIConnectionError("Connection refused")),
        ):
            with pytest.raises(DecisionModelError, match="connection error"):
                await service.decide(
                    task="Do something",
                    observation={},
                    actions=_ACTIONS,
                )

    @pytest.mark.asyncio
    async def test_api_key_never_exposed_in_error(self):
        """API key must not appear in any raised exception message."""
        import httpx2
        from typesafe_sdk import TypeSafeAPIError

        service = self._make_service(typesafe_api_key=_FAKE_API_KEY)

        class _FakeAPIError(TypeSafeAPIError):
            def __init__(self) -> None:
                # Simulate an error that includes the key (e.g. in a raw body).
                # Use real signature: status, body, headers, message
                import httpx2 as _h
                super().__init__(
                    status=401,
                    body=f"Unauthorised — key={_FAKE_API_KEY}",
                    headers=_h.Headers(),
                    message=f"Unauthorised — key={_FAKE_API_KEY}",
                )

        with patch.object(
            service._client,
            "system_one",
            new=AsyncMock(side_effect=_FakeAPIError()),
        ):
            with pytest.raises(DecisionModelError) as exc_info:
                await service.decide(
                    task="Do something",
                    observation={},
                    actions=_ACTIONS,
                )

        assert _FAKE_API_KEY not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_empty_task_raises_value_error(self):
        """Blank task string → ValueError before any network call."""
        service = self._make_service()
        with pytest.raises(ValueError, match="non-empty"):
            await service.decide(task="   ", observation={}, actions=_ACTIONS)

    @pytest.mark.asyncio
    async def test_empty_actions_raises_value_error(self):
        """Empty actions list → ValueError before any network call."""
        service = self._make_service()
        with pytest.raises(ValueError, match="At least one action"):
            await service.decide(task="Do something", observation={}, actions=[])


# ---------------------------------------------------------------------------
# init_client / close_client lifecycle tests
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Tests for the application-scoped service lifecycle helpers."""

    @pytest.mark.asyncio
    async def test_init_raises_when_key_missing(self):
        """init_client() raises MissingConfigError when the API key is empty."""
        settings = _make_settings(typesafe_api_key="")
        with pytest.raises(MissingConfigError, match="TYPESAFE_API_KEY"):
            await init_client(settings=settings)

    @pytest.mark.asyncio
    async def test_get_service_raises_before_init(self):
        """get_service() raises MissingConfigError before init_client is called."""
        # Ensure the global is clear.
        import backend.services.decision_model as dm

        original = dm._service_instance
        dm._service_instance = None
        try:
            with pytest.raises(MissingConfigError):
                get_service()
        finally:
            dm._service_instance = original

    @pytest.mark.asyncio
    async def test_init_and_close_cycle(self):
        """init_client() sets the singleton; close_client() clears it."""
        import backend.services.decision_model as dm

        original = dm._service_instance
        try:
            settings = _make_settings()
            service = await init_client(settings=settings)
            assert dm._service_instance is service
            assert get_service() is service

            await close_client()
            assert dm._service_instance is None
        finally:
            dm._service_instance = original


# ---------------------------------------------------------------------------
# FastAPI endpoint integration tests (TestClient, no real SDK calls)
# ---------------------------------------------------------------------------


class TestDecisionEndpoint:
    """Integration tests for POST /decision via FastAPI TestClient."""

    @pytest.fixture()
    def _mock_service(self):
        """Provide a mock service injected via FastAPI dependency override."""
        from backend.main import app
        from backend.services.decision_model import get_service as _get_service

        mock_svc = MagicMock(spec=DecisionModelService)
        app.dependency_overrides[_get_service] = lambda: mock_svc
        yield mock_svc
        app.dependency_overrides.pop(_get_service, None)

    @pytest.fixture()
    def client(self):
        from backend.main import app

        return TestClient(app, raise_server_exceptions=False)

    def test_valid_decision_returns_200(self, client, _mock_service):
        """Valid request returns 200 with the selected action ID."""
        expected = DecisionResponse(
            selected_action_id="tap_login",
            done=False,
            confidence=0.9,
            probabilities={"tap_login": 0.9, "scroll_down": 0.1},
            model=_FAKE_MODEL,
            latency_ms=120.0,
        )
        _mock_service.decide = AsyncMock(return_value=expected)

        payload = {
            "task": "Log in",
            "observation": {"screen": "login"},
            "actions": [
                {"id": "tap_login", "description": "Tap login"},
                {"id": "scroll_down", "description": "Scroll"},
            ],
        }
        resp = client.post("/decision", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["selectedActionId"] == "tap_login"
        assert data["done"] is False
        assert data["confidence"] == pytest.approx(0.9)

    def test_done_true_returns_null_action(self, client, _mock_service):
        """When done=True the endpoint returns selectedActionId=null."""
        expected = DecisionResponse(
            selected_action_id=None,
            done=True,
            confidence=0.95,
            model=_FAKE_MODEL,
            latency_ms=80.0,
        )
        _mock_service.decide = AsyncMock(return_value=expected)

        payload = {
            "task": "Open settings",
            "observation": {},
            "actions": [{"id": "tap_settings", "description": "Open settings"}],
        }
        resp = client.post("/decision", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["done"] is True
        assert data["selectedActionId"] is None

    def test_missing_api_key_returns_503(self, client, _mock_service):
        """MissingConfigError from the service → HTTP 503."""
        _mock_service.decide = AsyncMock(
            side_effect=MissingConfigError("TYPESAFE_API_KEY not set")
        )

        payload = {
            "task": "Do something",
            "observation": {},
            "actions": [{"id": "tap_x", "description": "Tap X"}],
        }
        resp = client.post("/decision", json=payload)
        assert resp.status_code == 503
        # Key must not appear in the response body.
        assert _FAKE_API_KEY not in resp.text

    def test_invalid_action_id_returns_422(self, client, _mock_service):
        """InvalidResponseError from the service → HTTP 422."""
        _mock_service.decide = AsyncMock(
            side_effect=InvalidResponseError("bad_action not in list")
        )

        payload = {
            "task": "Do something",
            "observation": {},
            "actions": [{"id": "tap_x", "description": "Tap X"}],
        }
        resp = client.post("/decision", json=payload)
        assert resp.status_code == 422

    def test_sdk_error_returns_502(self, client, _mock_service):
        """Generic DecisionModelError → HTTP 502."""
        _mock_service.decide = AsyncMock(
            side_effect=DecisionModelError("TypeSafe API connection error: Network unreachable")
        )

        payload = {
            "task": "Do something",
            "observation": {},
            "actions": [{"id": "tap_x", "description": "Tap X"}],
        }
        resp = client.post("/decision", json=payload)
        assert resp.status_code == 502

    def test_missing_task_returns_validation_error(self, client, _mock_service):
        """Missing required 'task' field → FastAPI 422 validation error."""
        payload = {
            "observation": {},
            "actions": [{"id": "tap_x", "description": "Tap X"}],
        }
        resp = client.post("/decision", json=payload)
        assert resp.status_code == 422

    def test_empty_actions_returns_validation_error(self, client, _mock_service):
        """Empty actions list → FastAPI 422 validation error (min_length=1)."""
        payload = {
            "task": "Do something",
            "observation": {},
            "actions": [],
        }
        resp = client.post("/decision", json=payload)
        assert resp.status_code == 422
