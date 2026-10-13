"""Sanity tests for backend models and FastAPI application."""

from fastapi.testclient import TestClient
from backend.main import app
from backend.models.device import Bounds, ScreenBounds, Element, PhoneState, Observation
from backend.services.ui_parser import parse_ui_xml

client = TestClient(app)


def test_app_created():
    """Verify FastAPI application initializes and mounts routes."""
    assert app.title == "Jev Mobile Agent Backend"


def test_models_instantiation():
    """Verify Pydantic models initialize correctly."""
    bounds = Bounds(left=100, top=200, right=500, bottom=300)
    screen = ScreenBounds(width=1080, height=2400)
    assert screen.width == 1080
    assert screen.height == 2400

    elem = Element(
        id="el-1",
        text="Submit",
        bounds=bounds,
        clickable=True,
    )
    assert elem.text == "Submit"
    assert elem.clickable is True

    obs = Observation(
        device_id="dev-1",
        phone=PhoneState(package_name="com.android.settings"),
        screen=screen,
        elements=[elem],
    )
    assert len(obs.elements) == 1
    assert obs.phone.package_name == "com.android.settings"


def test_ui_parser_offline():
    """Verify uiautomator XML parser extracts a root dictionary."""
    sample_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
    <hierarchy rotation="0">
      <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.android.settings" bounds="[0,0][1080,2400]">
        <node index="0" text="Wi-Fi" resource-id="android:id/title" class="android.widget.TextView" package="com.android.settings" content-desc="" clickable="true" bounds="[100,200][500,300]" />
      </node>
    </hierarchy>
    """
    root_dict = parse_ui_xml(sample_xml)
    assert root_dict is not None
    assert root_dict["className"] == "android.widget.FrameLayout"
    assert len(root_dict["children"]) == 1
    assert root_dict["children"][0]["text"] == "Wi-Fi"
