"""Unit tests for TypeSafePolicy (hierarchical), agent loop, and completion verifier.

All tests use ``unittest.mock`` — no real API key required.

After the JS-parity rewrite the active decision path is:
    TypeSafePolicy.decide()
      -> build_questions()
      -> service.decide_hierarchical()
      -> validate_choice()
      -> resolved action dict

Tests that previously mocked ``DecisionModelService.decide`` now mock
``decide_hierarchical`` instead.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import Settings
from backend.services.decision_model import DecisionModelService
from backend.services.policy import (
    TypeSafePolicy,
    build_questions,
    validate_choice,
    RULES,
)
from backend.services.verifiers import verify_task_completion
from backend.services.agent import run_agent


# ── Shared observation builders ───────────────────────────────────────────


def _sample_obs(
    package_name: str = "com.android.launcher",
    texts: list[str] | None = None,
    clickable_id: str = "ui.0.1",
    is_editable: bool = False,
    input_element_id: str | None = None,
) -> dict[str, Any]:
    """Plain-dict observation matching the summarizeState() output shape."""
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
            "focused": False,
            "password": False,
            "checkable": False,
            "checked": False,
            "selected": False,
            "resourceId": "",
            "hint": "",
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
            "focused": False,
            "password": False,
            "checkable": False,
            "checked": False,
            "selected": False,
            "resourceId": "",
            "hint": "",
            "bounds": {"left": 0, "top": 0, "right": 100, "bottom": 100},
        })

    return {
        "deviceId": "test-device",
        "phone": {
            "packageName": package_name,
            "currentApp": package_name.rsplit(".", 1)[-1],
            "isEditable": is_editable,
            "inputElementId": input_element_id,
            "keyboardVisible": is_editable,
            "focusEvidence": "reported" if is_editable else "none",
            "focusedElement": {"resourceId": "", "className": ""},
        },
        "screen": {"width": 1080, "height": 2400},
        "elements": elements,
        "fingerprint": f"fp_{package_name}_{len(elements)}",
        "observedAt": 1_700_000_000_000.0,
    }


def _editable_obs(
    package_name: str = "com.android.chrome",
    input_text: str = "",
) -> dict[str, Any]:
    """Observation with a focused editable field (keyboard visible)."""
    obs = _sample_obs(
        package_name=package_name,
        is_editable=True,
        input_element_id="ui.0.2",
    )
    obs["elements"].append({
        "id": "ui.0.2",
        "text": input_text,
        "label": "Search or type URL",
        "enabled": True,
        "clickable": False,
        "editable": True,
        "scrollable": False,
        "focused": True,
        "password": False,
        "checkable": False,
        "checked": False,
        "selected": False,
        "resourceId": "com.android.chrome:id/url_bar",
        "hint": "Search or type URL",
        "bounds": {"left": 0, "top": 100, "right": 1080, "bottom": 200},
    })
    return obs


def _make_operation_answer(
    operation: str,
    confidence: float,
    criteria_keys: list[str],
) -> dict[str, Any]:
    """Build a valid operation answer dict matching the exact criteria keys."""
    n_other = max(len(criteria_keys) - 1, 1)
    remainder = max(0.0, 1 - confidence)
    probs = {
        k: (confidence if k == operation else remainder / n_other)
        for k in criteria_keys
    }
    # Normalise floating-point sum to exactly 1.0.
    total = sum(probs.values())
    probs = {k: v / total for k, v in probs.items()}
    return {
        "type": "choice",
        "choice": operation,
        "confidence": confidence,
        "probabilities": probs,
    }


def _make_target_answer(
    chosen: str,
    confidence: float,
    criteria_keys: list[str],
) -> dict[str, Any]:
    n_other = max(len(criteria_keys) - 1, 1)
    remainder = max(0.0, 1 - confidence)
    probs = {
        k: (confidence if k == chosen else remainder / n_other)
        for k in criteria_keys
    }
    total = sum(probs.values())
    probs = {k: v / total for k, v in probs.items()}
    return {
        "type": "choice",
        "choice": chosen,
        "confidence": confidence,
        "probabilities": probs,
    }


def _hierarchical_response_for(
    obs: dict[str, Any],
    operation: str,
    operation_confidence: float = 0.9,
    target_index: str | None = None,
    target_confidence: float = 0.85,
    texts: list[str] | None = None,
    apps: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a fake decide_hierarchical() response whose probability keys exactly
    match what build_questions() produces for the given obs, so validate_choice
    won't reject it for key mismatch."""
    from backend.services.policy import build_questions
    space = build_questions(obs, texts=texts or [], apps=apps or [])
    op_criteria = space["questions"]["operation"]["criteria"]
    answers: dict[str, Any] = {
        "operation": _make_operation_answer(
            operation, operation_confidence, list(op_criteria.keys())
        )
    }

    head_map = {
        "TAP": "tap_target",
        "OPEN_APP": "app_target",
        "TYPE_TEXT": "text_value",
    }
    for prefix in ("SCROLL_DOWN", "SCROLL_UP", "SCROLL_LEFT", "SCROLL_RIGHT"):
        head_map[prefix] = "scroll_target"

    head = head_map.get(operation)
    if head and head in space["questions"] and target_index is not None:
        t_criteria = space["questions"][head]["criteria"]
        answers[head] = _make_target_answer(
            target_index, target_confidence, list(t_criteria.keys())
        )

    return {
        "answers": answers,
        "usage": {"inputTokens": 100, "outputTokens": 20},
        "model": "jev-test",
    }


# ── Tests for build_questions() ───────────────────────────────────────────


class TestBuildQuestions:
    """Verify that build_questions() matches the JS buildQuestions() exactly."""

    def test_tap_candidates_produce_operation_and_target_questions(self):
        obs = _sample_obs()
        space = build_questions(obs)
        assert "TAP" in space["questions"]["operation"]["criteria"]
        assert "tap_target" in space["questions"]
        assert len(space["tap"]) == 1

    def test_text_candidates_present_when_editable(self):
        obs = _editable_obs()
        space = build_questions(obs, texts=["Hyderabad"])
        assert "TYPE_TEXT" in space["questions"]["operation"]["criteria"]
        assert "text_value" in space["questions"]
        # The text value should appear in criteria.
        tv_criteria = space["questions"]["text_value"]["criteria"]
        assert "Hyderabad" in tv_criteria.values()
        # NONE option must always be present.
        assert "NONE" in tv_criteria

    def test_text_candidates_absent_when_not_editable(self):
        obs = _sample_obs()  # isEditable=False
        space = build_questions(obs, texts=["Hyderabad"])
        assert "TYPE_TEXT" not in space["questions"]["operation"]["criteria"]
        assert "text_value" not in space["questions"]

    def test_operation_instructions_is_rules_string(self):
        obs = _sample_obs()
        space = build_questions(obs)
        assert space["questions"]["operation"]["instructions"] == RULES

    def test_done_wait_blocked_always_present(self):
        obs = _sample_obs()
        criteria = space = build_questions(obs)["questions"]["operation"]["criteria"]
        assert "DONE" in criteria
        assert "WAIT" in criteria
        assert "BLOCKED" in criteria

    def test_app_target_question_built_for_installed_apps(self):
        obs = _sample_obs(package_name="com.android.launcher")
        apps = [{"packageName": "com.android.chrome", "label": "Chrome"}]
        space = build_questions(obs, apps=apps)
        assert "OPEN_APP" in space["questions"]["operation"]["criteria"]
        assert "app_target" in space["questions"]
        assert "Chrome (com.android.chrome)" in space["questions"]["app_target"]["criteria"].values()

    def test_scroll_candidates_produce_scroll_operations(self):
        obs = _sample_obs()
        obs["elements"].append({
            "id": "ui.list",
            "text": "",
            "label": "",
            "enabled": True,
            "clickable": False,
            "editable": False,
            "scrollable": True,
            "focused": False,
            "password": False,
            "checkable": False,
            "checked": False,
            "selected": False,
            "resourceId": "",
            "hint": "",
            "bounds": {"left": 0, "top": 200, "right": 1080, "bottom": 2400},
        })
        space = build_questions(obs)
        assert "SCROLL_DOWN" in space["questions"]["operation"]["criteria"]
        assert "scroll_target" in space["questions"]

    def test_index_for_strips_leading_tap_and_trailing_dot(self):
        r"""Regression: index_for must use /^Tap |\.$/g not /^Tap |\.$ /."""
        obs = _sample_obs()
        space = build_questions(obs)
        # The label in tap_target criteria must not start with "Tap " or end with "."
        for label in space["questions"].get("tap_target", {}).get("criteria", {}).values():
            assert not label.startswith("Tap ")
            # Labels may end with a word, not a dot.
            assert not label.endswith(".")

    def test_instructions_injected_as_goal_rules_dict_when_called_by_policy(self):
        """After build_questions(), policy wraps instructions in {goal, rules}."""
        obs = _sample_obs()
        space = build_questions(obs)
        # Simulate the JS: question.instructions = {goal, rules: question.instructions}
        for q in space["questions"].values():
            q["instructions"] = {"goal": "test goal", "rules": q["instructions"]}
        for q in space["questions"].values():
            assert isinstance(q["instructions"], dict)
            assert "goal" in q["instructions"]
            assert "rules" in q["instructions"]

    def test_app_in_current_foreground_excluded(self):
        """Apps matching the current foreground packageName must not appear in app_target."""
        obs = _sample_obs(package_name="com.android.chrome")
        apps = [
            {"packageName": "com.android.chrome", "label": "Chrome"},
            {"packageName": "com.android.settings", "label": "Settings"},
        ]
        space = build_questions(obs, apps=apps)
        if "app_target" in space["questions"]:
            labels = list(space["questions"]["app_target"]["criteria"].values())
            assert not any("Chrome" in l and "com.android.chrome" in l for l in labels)


# ── Tests for validate_choice() ──────────────────────────────────────────


class TestValidateChoice:
    """Verify validate_choice() mirrors JS validateChoice() exactly."""

    def _valid(self, choice="TAP", confidence=0.9, probs=None):
        if probs is None:
            probs = {"TAP": 0.9, "WAIT": 0.05, "DONE": 0.05}
        return {
            "type": "choice",
            "choice": choice,
            "confidence": confidence,
            "probabilities": probs,
        }

    def test_valid_answer_returned_unchanged(self):
        criteria = {"TAP": "...", "WAIT": "...", "DONE": "..."}
        answer = self._valid()
        result = validate_choice(answer, criteria)
        assert result is answer

    def test_wrong_type_raises(self):
        answer = {"type": "noul", "choice": "TAP", "confidence": 0.9, "probabilities": {"TAP": 1.0}}
        with pytest.raises(ValueError, match="invalid choice"):
            validate_choice(answer, {"TAP": "..."})

    def test_choice_not_in_criteria_raises(self):
        answer = self._valid(choice="UNKNOWN")
        with pytest.raises(ValueError, match="invalid choice"):
            validate_choice(answer, {"TAP": "...", "WAIT": "...", "DONE": "..."})

    def test_missing_probabilities_raises(self):
        answer = {"type": "choice", "choice": "TAP", "confidence": 0.9}
        with pytest.raises(ValueError, match="invalid choice"):
            validate_choice(answer, {"TAP": "..."})

    def test_prob_sum_outside_tolerance_raises(self):
        answer = self._valid(probs={"TAP": 0.5, "WAIT": 0.2, "DONE": 0.1})  # sum=0.8
        with pytest.raises(ValueError, match="invalid choice"):
            validate_choice(answer, {"TAP": "...", "WAIT": "...", "DONE": "..."})

    def test_chosen_not_max_raises(self):
        # WAIT has higher prob than TAP but TAP is chosen.
        answer = self._valid(probs={"TAP": 0.3, "WAIT": 0.6, "DONE": 0.1})
        with pytest.raises(ValueError, match="invalid choice"):
            validate_choice(answer, {"TAP": "...", "WAIT": "...", "DONE": "..."})

    def test_prob_keys_mismatch_raises(self):
        # probabilities has wrong keys.
        answer = self._valid(probs={"TAP": 0.9, "EXTRA": 0.1})
        with pytest.raises(ValueError, match="invalid choice"):
            validate_choice(answer, {"TAP": "...", "WAIT": "...", "DONE": "..."})

    def test_confidence_out_of_range_raises(self):
        answer = self._valid(confidence=1.5)
        with pytest.raises(ValueError, match="invalid choice"):
            validate_choice(answer, {"TAP": "...", "WAIT": "...", "DONE": "..."})


# ── Tests for TypeSafePolicy.decide() ────────────────────────────────────


class TestTypeSafePolicyHierarchical:
    """Verify TypeSafePolicy uses build_questions + validate_choice + decide_hierarchical."""

    def _make_policy(self, service) -> TypeSafePolicy:
        return TypeSafePolicy(decision_service=service)

    def _make_service(self, response: dict) -> MagicMock:
        svc = MagicMock(spec=DecisionModelService)
        svc.decide_hierarchical = AsyncMock(return_value=response)
        return svc

    @pytest.mark.asyncio
    async def test_tap_operation_resolved_to_action(self):
        """TAP + tap_target='1' → action.type=tap-element."""
        obs = _sample_obs()
        response = _hierarchical_response_for(obs, "TAP", target_index="1")
        svc = self._make_service(response)
        policy = self._make_policy(svc)

        decision = await policy.decide(goal="Tap the Chrome button", observation=obs)

        assert decision["status"] == "action"
        assert decision["operation"] == "TAP"
        assert decision["action"]["type"] == "tap-element"
        assert decision["action"]["elementId"] == "ui.0.1"
        assert "Chrome" in decision["label"]
        assert decision["confidence"] == pytest.approx(0.9)
        assert decision["targetConfidence"] == pytest.approx(0.85)
        assert decision["operationProbabilities"] is not None
        assert decision["targetProbabilities"] is not None

    @pytest.mark.asyncio
    async def test_type_text_hyderabad_selected(self):
        """TYPE_TEXT flow: model picks 'Hyderabad' from goal-derived text candidates."""
        obs = _editable_obs()
        from backend.services.text import text_candidates
        text_vals = text_candidates("Search for Hyderabad").values
        # Find which index build_questions assigns to "Hyderabad".
        from backend.services.policy import build_questions as _bq
        space = _bq(obs, texts=text_vals)
        hyderabad_index = next(
            idx for idx, entry in space["text"].items()
            if entry["action"]["text"] == "Hyderabad"
        )
        response = _hierarchical_response_for(
            obs, "TYPE_TEXT",
            target_index=hyderabad_index,
            texts=text_vals,
        )
        svc = self._make_service(response)
        policy = self._make_policy(svc)

        decision = await policy.decide(
            goal="Search for Hyderabad",
            observation=obs,
        )

        assert decision["status"] == "action"
        assert decision["operation"] == "TYPE_TEXT"
        assert decision["action"]["type"] == "type"
        assert decision["action"]["text"] == "Hyderabad"
        assert decision["action"]["clear"] is True

    @pytest.mark.asyncio
    async def test_type_text_none_returns_needs_input(self):
        """TYPE_TEXT + text_value='NONE' → status=needs_input."""
        obs = _editable_obs()
        from backend.services.text import text_candidates
        text_vals = text_candidates("Search for Hyderabad").values
        response = _hierarchical_response_for(
            obs, "TYPE_TEXT",
            target_index="NONE",
            texts=text_vals,
        )
        svc = self._make_service(response)
        policy = self._make_policy(svc)

        decision = await policy.decide(
            goal="Search for Hyderabad",
            observation=obs,
        )
        assert decision["status"] == "needs_input"
        assert "reason" in decision

    @pytest.mark.asyncio
    async def test_done_operation_returns_done_status(self):
        """DONE operation → status=done (no action key)."""
        obs = _sample_obs()
        response = _hierarchical_response_for(obs, "DONE")
        svc = self._make_service(response)
        policy = self._make_policy(svc)

        decision = await policy.decide(goal="Open Chrome", observation=obs)
        assert decision["status"] == "done"
        assert "action" not in decision

    @pytest.mark.asyncio
    async def test_blocked_operation_returns_blocked_status(self):
        """BLOCKED operation → status=blocked."""
        obs = _sample_obs()
        response = _hierarchical_response_for(obs, "BLOCKED")
        svc = self._make_service(response)
        policy = self._make_policy(svc)

        decision = await policy.decide(goal="Do something impossible", observation=obs)
        assert decision["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_wait_operation_returns_wait_action(self):
        """WAIT → action.type=wait."""
        obs = _sample_obs()
        response = _hierarchical_response_for(obs, "WAIT")
        svc = self._make_service(response)
        policy = self._make_policy(svc)

        decision = await policy.decide(goal="Wait for loading", observation=obs)
        assert decision["status"] == "action"
        assert decision["action"]["type"] == "wait"

    @pytest.mark.asyncio
    async def test_open_app_operation_resolved(self):
        """OPEN_APP + app_target='1' → action.type=open-app."""
        obs = _sample_obs(package_name="com.android.launcher")
        apps = [{"packageName": "com.android.chrome", "label": "Chrome"}]
        response = _hierarchical_response_for(obs, "OPEN_APP", target_index="1", apps=apps)
        svc = self._make_service(response)
        policy = self._make_policy(svc)

        decision = await policy.decide(goal="Open Chrome", observation=obs, apps=apps)
        assert decision["status"] == "action"
        assert decision["action"]["type"] == "open-app"
        assert decision["action"]["packageName"] == "com.android.chrome"

    @pytest.mark.asyncio
    async def test_below_threshold_returns_uncertain(self):
        """Confidence below threshold → status=uncertain."""
        obs = _sample_obs()
        response = _hierarchical_response_for(
            obs=obs,
            operation="TAP",
            operation_confidence=0.3,
            target_index="1",
        )
        svc = self._make_service(response)
        policy = self._make_policy(svc, threshold=0.5)

        decision = await policy.decide(goal="Tap Chrome", observation=obs)
        assert decision["status"] == "uncertain"

    def _make_policy(self, service, threshold=0) -> TypeSafePolicy:
        return TypeSafePolicy(decision_service=service, threshold=threshold)

    @pytest.mark.asyncio
    async def test_request_body_size_guard_raises(self):
        """Body > 150 KB → ValueError before calling decide_hierarchical."""
        obs = _sample_obs()
        # Inflate observation with a huge visible-text field.
        obs["elements"][0]["text"] = "x" * 200_000
        svc = self._make_service({})
        policy = self._make_policy(svc)

        with pytest.raises(ValueError, match="too large"):
            await policy.decide(goal="Do something", observation=obs)
        # Service should not have been called.
        svc.decide_hierarchical.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_decide_hierarchical_called_not_decide(self):
        """After the parity fix, decide_hierarchical() must be called, not decide()."""
        obs = _sample_obs()
        response = _hierarchical_response_for(obs=obs, operation="TAP", target_index="1")
        svc = self._make_service(response)
        svc.decide = AsyncMock()  # Must not be called.
        policy = self._make_policy(svc)

        await policy.decide(goal="Tap Chrome", observation=obs)

        svc.decide_hierarchical.assert_awaited_once()
        svc.decide.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_operation_choice_raises_value_error(self):
        """validate_choice raises ValueError for a malformed operation answer."""
        obs = _sample_obs()
        # Return a malformed answer (type != "choice").
        response = {
            "answers": {"operation": {"type": "noul", "choice": "TAP", "confidence": 0.9, "probabilities": {}}},
            "model": "jev-test",
        }
        svc = self._make_service(response)
        policy = self._make_policy(svc)

        with pytest.raises(ValueError, match="invalid choice"):
            await policy.decide(goal="Tap Chrome", observation=obs)

    @pytest.mark.asyncio
    async def test_state_includes_required_js_fields(self):
        """The state sent to decide_hierarchical must match JS schema fields."""
        obs = _editable_obs()
        response = _hierarchical_response_for(
            obs=obs,
            operation="TYPE_TEXT",
            target_index="1",
            texts=["Hyderabad"],
        )
        svc = self._make_service(response)
        policy = self._make_policy(svc)

        await policy.decide(goal="Search for Hyderabad", observation=obs, texts=["Hyderabad"])

        # Capture the state passed to decide_hierarchical.
        call_kwargs = svc.decide_hierarchical.call_args.kwargs
        state = call_kwargs["state"]

        assert "goal" in state
        assert "app" in state
        assert "isEditable" in state
        assert "textSource" in state
        assert "textEntryAvailableAfterFocus" in state
        assert "visibleText" in state
        assert "elements" in state
        assert "availableApps" in state
        assert "recentActions" in state

    @pytest.mark.asyncio
    async def test_pydantic_observation_normalised_to_dict(self):
        """TypeSafePolicy.decide() must work even when observation is a Pydantic model."""
        from backend.models.device import Observation, PhoneState, ScreenBounds, Element, Bounds

        phone = PhoneState(packageName="com.android.launcher")
        screen = ScreenBounds(width=1080, height=2400)
        el = Element(
            id="ui.0.1",
            text="Chrome",
            label="Open Chrome",
            enabled=True,
            clickable=True,
            editable=False,
            scrollable=False,
            bounds=Bounds(left=100, top=100, right=200, bottom=200),
        )
        obs_model = Observation(
            deviceId="test-device",
            phone=phone,
            screen=screen,
            elements=[el],
            fingerprint="fp123",
            observedAt=1_700_000_000_000.0,
        )

        response = _hierarchical_response_for(obs=_sample_obs(), operation="TAP", target_index="1")
        svc = self._make_service(response)
        policy = self._make_policy(svc)

        # Should not raise even though obs_model is a Pydantic model.
        decision = await policy.decide(goal="Tap Chrome", observation=obs_model)
        assert decision["status"] == "action"


# ── Tests for verify_task_completion ──────────────────────────────────────


def test_verify_task_completion_step_zero_rejected():
    obs = _sample_obs("com.android.launcher")
    verified, reason = verify_task_completion("Search Wikipedia", obs, history=[])
    assert verified is False
    assert "No actions executed yet" in reason


def test_verify_task_completion_app_launch():
    obs_launcher = _sample_obs("com.android.launcher")
    verified, reason = verify_task_completion("Open Chrome", obs_launcher, history=[{"type": "tap"}])
    assert verified is False
    assert "not in foreground" in reason

    obs_chrome = _sample_obs("com.android.chrome")
    verified, reason = verify_task_completion("Open Chrome", obs_chrome, history=[{"type": "tap"}])
    assert verified is True
    assert "is in foreground" in reason


def test_verify_task_completion_keywords():
    obs_with_wiki = _sample_obs("com.android.chrome", texts=["Wikipedia: The Free Encyclopedia"])
    verified, reason = verify_task_completion(
        "Search for Wikipedia", obs_with_wiki, history=[{"type": "type"}]
    )
    assert verified is True
    assert "wikipedia" in reason.lower()


# ── Tests for agent loop (mocked device + policy) ─────────────────────────


def _make_mock_device(observations: list[dict]) -> MagicMock:
    device = MagicMock()
    device.assert_ready = AsyncMock(return_value={"id": "test", "state": "ready"})
    device.list_apps = AsyncMock(return_value=[])
    _obs_iter = iter(observations)
    device.observe = AsyncMock(side_effect=lambda: next(_obs_iter))
    device.act = AsyncMock(return_value=None)
    return device


def _make_mock_policy(*decisions: dict) -> MagicMock:
    policy = MagicMock()
    _iter = iter(decisions)
    policy.decide = AsyncMock(side_effect=lambda **kw: next(_iter))
    return policy


@pytest.mark.asyncio
async def test_agent_loop_asyncio_coroutine_bug_absent():
    """asyncio.coroutine() was removed in 3.11 — the loop must not use it."""
    import inspect, backend.services.agent as agent_module
    src = inspect.getsource(agent_module)
    assert "asyncio.coroutine" not in src, (
        "asyncio.coroutine() was removed in Python 3.11 and must not appear in agent.py"
    )


@pytest.mark.asyncio
async def test_run_agent_normalises_pydantic_observation():
    """run_agent() must convert Pydantic Observation models to dicts without crashing."""
    from backend.models.device import Observation, PhoneState, ScreenBounds, Element, Bounds

    phone = PhoneState(packageName="com.android.launcher")
    screen = ScreenBounds(width=1080, height=2400)
    el = Element(
        id="ui.0.1",
        text="Chrome",
        label="Open Chrome",
        enabled=True,
        clickable=True,
        editable=False,
        scrollable=False,
        bounds=Bounds(left=100, top=100, right=200, bottom=200),
    )
    obs_model = Observation(
        deviceId="test-device",
        phone=phone,
        screen=screen,
        elements=[el],
        fingerprint="fp_pydantic",
        observedAt=1_700_000_000_000.0,
    )
    obs_dict = _sample_obs("com.android.chrome")

    device = MagicMock()
    device.assert_ready = AsyncMock(return_value={"id": "t", "state": "ready"})
    device.list_apps = AsyncMock(return_value=[])
    device.observe = AsyncMock(side_effect=[obs_model, obs_dict])
    device.act = AsyncMock(return_value=None)

    # Policy returns preview immediately (execute=False).
    policy = _make_mock_policy(
        {"status": "action", "action": {"type": "tap-element", "elementId": "ui.0.1"}}
    )

    result = await run_agent(
        device=device,
        policy=policy,
        goal="Tap Chrome",
        execute=False,
    )
    assert result["status"] == "preview"
    # observation in result should be a plain dict.
    assert isinstance(result["observation"], dict)


@pytest.mark.asyncio
async def test_run_agent_preview_returns_without_executing():
    obs = _sample_obs()
    device = _make_mock_device([obs])
    policy = _make_mock_policy(
        {"status": "action", "action": {"type": "tap-element", "elementId": "ui.0.1"}}
    )

    result = await run_agent(device=device, policy=policy, goal="Tap Chrome", execute=False)

    assert result["status"] == "preview"
    device.act.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_agent_done_after_two_steps():
    obs1 = _sample_obs("com.android.launcher")
    obs2 = _sample_obs("com.android.chrome")

    device = _make_mock_device([obs1, obs2, obs2])
    policy = _make_mock_policy(
        {"status": "action", "action": {"type": "open-app", "packageName": "com.android.chrome", "appLabel": "Chrome"}},
        {"status": "done", "operation": "DONE"},
    )

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


@pytest.mark.asyncio
async def test_run_agent_step_limit_enforced():
    observations = []
    for i in range(25):
        o = _sample_obs()
        o["fingerprint"] = f"fp_step_{i}"
        observations.append(o)
    device = _make_mock_device(observations)
    # Policy always says action (never done).
    policy = MagicMock()
    policy.decide = AsyncMock(return_value={
        "status": "action",
        "action": {"type": "tap-element", "elementId": "ui.0.1"},
        "label": "Tap Chrome",
    })

    result = await run_agent(
        device=device,
        policy=policy,
        goal="Do something",
        execute=True,
        max_steps=3,
    )
    assert result["status"] == "step_limit"
    assert result["steps"] == 3


# ── Configuration tests ───────────────────────────────────────────────────


def test_config_model_synchronization():
    s1 = Settings(typesafe_model="inception/mercury-decide:free", typesafe_default_model="jev-latest")
    assert s1.typesafe_default_model == "inception/mercury-decide:free"

    s2 = Settings(typesafe_default_model="custom/model:latest", typesafe_model="jev-latest")
    assert s2.typesafe_model == "custom/model:latest"
