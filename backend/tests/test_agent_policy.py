"""Unit tests for TypeSafePolicy, agent loop integration, and completion verifier."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import Settings
from backend.models.decision import DecisionResponse
from backend.services.decision_model import DecisionModelService
from backend.services.policy import TypeSafePolicy
from backend.services.verifiers import verify_task_completion
from backend.services.agent import run_agent


# ── Sample Observation Fixtures ───────────────────────────────────────────

def _sample_obs(
    package_name: str = "com.android.launcher",
    texts: list[str] | None = None,
    clickable_id: str = "ui.0.1",
) -> dict[str, Any]:
    texts = texts or []
    elements = [
        {
            "id": clickable_id,
            "text": "Chrome",
            "label": "Open Chrome",
            "enabled": True,
            "clickable": True,
            "editable": False,
            "scrollable": False,
            "bounds": {"left": 100, "top": 100, "right": 200, "bottom": 200},
        }
    ]
    for idx, txt in enumerate(texts):
        elements.append({
            "id": f"ui.text.{idx}",
            "text": txt,
            "label": "",
            "enabled": True,
            "clickable": False,
            "editable": False,
            "scrollable": False,
            "bounds": {"left": 0, "top": 0, "right": 100, "bottom": 100},
        })

    return {
        "deviceId": "test-device",
        "phone": {
            "packageName": package_name,
            "currentApp": package_name.rsplit(".", 1)[-1],
            "isEditable": False,
        },
        "screen": {"width": 1080, "height": 2400},
        "elements": elements,
        "fingerprint": f"fp_{package_name}_{len(elements)}",
    }


# ── Tests for verify_task_completion ──────────────────────────────────────

def test_verify_task_completion_step_zero_rejected():
    """Premature done at step 0 without evidence on screen must be rejected."""
    obs = _sample_obs("com.android.launcher")
    verified, reason = verify_task_completion("Search Wikipedia", obs, history=[])
    assert verified is False
    assert "No actions executed yet" in reason


def test_verify_task_completion_app_launch():
    """Open Chrome is verified only when Chrome is the foreground app."""
    obs_launcher = _sample_obs("com.android.launcher")
    verified, reason = verify_task_completion("Open Chrome", obs_launcher, history=[{"type": "tap"}])
    assert verified is False
    assert "not in foreground" in reason

    obs_chrome = _sample_obs("com.android.chrome")
    verified, reason = verify_task_completion("Open Chrome", obs_chrome, history=[{"type": "tap"}])
    assert verified is True
    assert "is in foreground" in reason


def test_verify_task_completion_keywords():
    """Goal with key terms is verified when term appears in visible text."""
    obs_with_wiki = _sample_obs("com.android.chrome", texts=["Wikipedia: The Free Encyclopedia"])
    verified, reason = verify_task_completion("Search for Wikipedia", obs_with_wiki, history=[{"type": "type"}])
    assert verified is True
    assert "wikipedia" in reason.lower()


# ── Tests for TypeSafePolicy ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_policy_selects_valid_action():
    """Policy transforms candidates, queries DecisionModelService, and returns action dict."""
    mock_service = MagicMock(spec=DecisionModelService)
    mock_service.decide = AsyncMock(
        return_value=DecisionResponse(
            selected_action_id="tap_ui.0.1",
            done=False,
            confidence=0.95,
            model="test-model",
            latency_ms=100.0,
        )
    )

    policy = TypeSafePolicy(decision_service=mock_service)
    obs = _sample_obs("com.android.launcher")

    decision = await policy.decide(
        goal="Tap the Chrome button",
        observation=obs,
        history=[],
    )

    assert decision["status"] == "action"
    assert decision["choice"] == "tap_ui.0.1"
    assert decision["action"]["type"] == "tap-element"
    assert decision["action"]["elementId"] == "ui.0.1"
    assert "Chrome" in decision["label"]
    assert decision["confidence"] == 0.95


@pytest.mark.asyncio
async def test_policy_rejects_unknown_action_id():
    """If the model selects an ID not in candidate actions, policy blocks instead of crashing."""
    mock_service = MagicMock(spec=DecisionModelService)
    mock_service.decide = AsyncMock(
        return_value=DecisionResponse(
            selected_action_id="hallucinated_action_123",
            done=False,
            confidence=0.8,
            model="test-model",
        )
    )

    policy = TypeSafePolicy(decision_service=mock_service)
    obs = _sample_obs()

    decision = await policy.decide(goal="Do something", observation=obs)
    assert decision["status"] == "blocked"
    assert "unknown action ID" in decision["reason"]


@pytest.mark.asyncio
async def test_policy_premature_done_rejected_fallback():
    """Premature done at step 0 falls back to highest-probability valid candidate action."""
    mock_service = MagicMock(spec=DecisionModelService)
    mock_service.decide = AsyncMock(
        return_value=DecisionResponse(
            selected_action_id=None,
            done=True,
            confidence=0.2,
            probabilities={"tap_ui.0.1": 0.7, "wait": 0.1},
            model="test-model",
        )
    )

    policy = TypeSafePolicy(decision_service=mock_service)
    obs = _sample_obs("com.android.launcher")

    # Step 0: "Open Chrome" cannot be done while on launcher
    decision = await policy.decide(
        goal="Open Chrome",
        observation=obs,
        history=[],
    )

    assert decision["status"] == "action"
    assert decision["choice"] == "tap_ui.0.1"
    assert "Premature completion rejected" in decision["reason"]


@pytest.mark.asyncio
async def test_policy_verified_done_accepted():
    """When model claims done and state verification passes, policy returns status=done."""
    mock_service = MagicMock(spec=DecisionModelService)
    mock_service.decide = AsyncMock(
        return_value=DecisionResponse(
            selected_action_id=None,
            done=True,
            confidence=0.98,
            model="test-model",
        )
    )

    policy = TypeSafePolicy(decision_service=mock_service)
    obs_chrome = _sample_obs("com.android.chrome")

    decision = await policy.decide(
        goal="Open Chrome",
        observation=obs_chrome,
        history=[{"action": {"type": "open-app"}}],
    )

    assert decision["status"] == "done"
    assert decision["operation"] == "DONE"
    assert decision["choice"] == "__done__"


# ── Configuration & Normalization Tests ────────────────────────────────────

def test_config_model_synchronization():
    """Settings synchronizes typesafe_default_model and typesafe_model."""
    s1 = Settings(typesafe_model="inception/mercury-decide:free", typesafe_default_model="jev-latest")
    assert s1.typesafe_default_model == "inception/mercury-decide:free"

    s2 = Settings(typesafe_default_model="custom/model:latest", typesafe_model="jev-latest")
    assert s2.typesafe_model == "custom/model:latest"


def test_decision_service_base_url_normalization():
    """DecisionModelService strips /alpha/decisions and /v1/systemone from base_url."""
    s = Settings(
        typesafe_api_key="test-key",
        typesafe_base_url="https://openrouter.ai/api/alpha/decisions",
        typesafe_default_model="test-model",
    )
    svc = DecisionModelService(settings=s)
    # The client's configured base_url should be normalized to https://openrouter.ai/api
    assert svc._client._config.base_url == "https://openrouter.ai/api"


# ── Full Agent Loop Integration Test (Mocked) ─────────────────────────────

@pytest.mark.asyncio
async def test_run_agent_with_policy_success():
    """run_agent executes observe -> decide -> act -> observe -> done sequence."""
    obs_launcher = _sample_obs("com.android.launcher")
    obs_chrome = _sample_obs("com.android.chrome")

    # Mock device transitioning from launcher to chrome after act()
    device = MagicMock()
    device.assert_ready = AsyncMock(return_value={"id": "test", "state": "ready"})
    device.list_apps = AsyncMock(return_value=[{"packageName": "com.android.chrome", "label": "Chrome"}])

    observations = [obs_launcher, obs_chrome]
    device.observe = AsyncMock(side_effect=lambda: observations.pop(0) if observations else obs_chrome)
    device.act = AsyncMock(return_value=None)

    # Mock decision service: step 0 -> tap Chrome, step 1 -> done
    mock_service = MagicMock(spec=DecisionModelService)
    mock_service.decide = AsyncMock(
        side_effect=[
            DecisionResponse(
                selected_action_id="open_com.android.chrome",
                done=False,
                confidence=0.95,
                model="test-model",
            ),
            DecisionResponse(
                selected_action_id=None,
                done=True,
                confidence=0.99,
                model="test-model",
            ),
        ]
    )

    policy = TypeSafePolicy(decision_service=mock_service)

    result = await run_agent(
        device=device,
        policy=policy,
        goal="Open Chrome",
        execute=True,
        max_steps=5,
    )

    assert result["status"] == "done"
    assert result["steps"] == 1
    device.act.assert_awaited_once()
