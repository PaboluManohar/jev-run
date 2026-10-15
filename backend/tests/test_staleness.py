"""Unit and regression tests for staleness validation (assert_fresh)."""

import time
import pytest

from backend.models.device import Bounds, Element, Observation, PhoneState, ScreenBounds
from backend.services.staleness import StaleObservationError, assert_fresh, _normalize


def _make_models():
    now = time.time() * 1000
    screen = ScreenBounds(width=1080, height=2400)
    phone = PhoneState(
        packageName="com.android.chrome",
        isEditable=True,
        inputElementId="ui.input",
    )
    elem_tap = Element(
        id="ui.1",
        text="Search Google",
        clickable=True,
        bounds=Bounds(left=100, top=200, right=900, bottom=300),
    )
    elem_input = Element(
        id="ui.input",
        text="",
        editable=True,
        bounds=Bounds(left=100, top=200, right=900, bottom=300),
    )
    elem_scroll = Element(
        id="ui.scroll",
        scrollable=True,
        enabled=True,
        resourceId="com.android.chrome:id/recycler_view",
        bounds=Bounds(left=0, top=300, right=1080, bottom=2000),
    )
    obs = Observation(
        deviceId="f43dbe44",
        observedAt=now,
        fingerprint="fp1",
        screen=screen,
        phone=phone,
        elements=[elem_tap, elem_input, elem_scroll],
    )
    return obs


def test_assert_fresh_with_pydantic_models():
    """Verify that assert_fresh does not raise TypeError with ScreenBounds/Observation models."""
    obs1 = _make_models()
    obs2 = _make_models()

    # tap-element should not raise
    assert_fresh(obs2, obs1, {"type": "tap-element", "elementId": "ui.1"})

    # type should not raise
    assert_fresh(obs2, obs1, {"type": "type", "text": "Wikipedia"})

    # swipe should not raise
    assert_fresh(obs2, obs1, {"type": "swipe", "regionId": "ui.scroll"})

    # home global action should not raise
    assert_fresh(obs2, obs1, {"type": "global", "name": "home"})

    # back global action should not raise
    assert_fresh(obs2, obs1, {"type": "global", "name": "back"})


def test_assert_fresh_with_dict_inputs():
    """Verify that assert_fresh works with plain dictionary inputs."""
    now = time.time() * 1000
    before = {
        "deviceId": "phone1",
        "observedAt": now,
        "fingerprint": "screen1",
        "screen": {"width": 1080, "height": 2400},
        "phone": {"packageName": "com.android.chrome", "isEditable": True, "inputElementId": "ui.0"},
        "elements": [
            {
                "id": "ui.0",
                "text": "Search",
                "editable": True,
                "clickable": True,
                "enabled": True,
                "bounds": {"left": 0, "top": 0, "right": 100, "bottom": 100},
            }
        ],
    }
    after = {
        "deviceId": "phone1",
        "observedAt": now,
        "fingerprint": "screen1",
        "screen": {"width": 1080, "height": 2400},
        "phone": {"packageName": "com.android.chrome", "isEditable": True, "inputElementId": "ui.0"},
        "elements": [
            {
                "id": "ui.0",
                "text": "Search",
                "editable": True,
                "clickable": True,
                "enabled": True,
                "bounds": {"left": 0, "top": 0, "right": 100, "bottom": 100},
            }
        ],
    }

    assert_fresh(after, before, {"type": "tap-element", "elementId": "ui.0"})
    assert_fresh(after, before, {"type": "type", "text": "Wikipedia"})


def test_assert_fresh_detects_changed_package():
    obs1 = _make_models()
    obs2 = _make_models()
    obs2.phone.package_name = "com.other.app"

    with pytest.raises(StaleObservationError):
        assert_fresh(obs2, obs1, {"type": "tap-element", "elementId": "ui.1"})


def test_assert_fresh_detects_changed_screen():
    obs1 = _make_models()
    obs2 = _make_models()
    obs2.screen = ScreenBounds(width=720, height=1280)

    with pytest.raises(StaleObservationError):
        assert_fresh(obs2, obs1, {"type": "tap-element", "elementId": "ui.1"})


def test_assert_fresh_detects_changed_element_meaning():
    obs1 = _make_models()
    obs2 = _make_models()
    # Change the targeted element text
    obs2.elements[0].text = "Different Text"

    with pytest.raises(StaleObservationError):
        assert_fresh(obs2, obs1, {"type": "tap-element", "elementId": "ui.1"})


def test_assert_fresh_detects_expired_observation():
    obs1 = _make_models()
    obs1.observed_at = (time.time() - 40) * 1000  # 40 seconds ago (max_age is 30s)
    obs2 = _make_models()

    with pytest.raises(StaleObservationError):
        assert_fresh(obs2, obs1, {"type": "tap-element", "elementId": "ui.1"})
