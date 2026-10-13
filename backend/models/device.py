"""Pydantic models for device state, UI elements, and observations.

The shapes intentionally match what the original TypeScript ``summarizeState()``
produces so that every downstream consumer (actions, policy, staleness) can
operate on the same data contract without translation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Bounds(BaseModel):
    left: int
    top: int
    right: int
    bottom: int

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class Element(BaseModel):
    """A single actionable or informational UI node."""

    id: str
    text: str = ""
    label: str = ""
    resource_id: str = Field("", alias="resourceId")
    class_name: str = Field("", alias="className")
    hint: str = ""
    bounds: Bounds
    clickable: bool = False
    editable: bool = False
    scrollable: bool = False
    enabled: bool = True
    focused: bool = False
    password: bool = False
    checkable: bool = False
    checked: bool = False
    selected: bool = False

    model_config = {"populate_by_name": True}

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        for field_name, field_info in type(self).model_fields.items():
            if field_info.alias == item:
                return getattr(self, field_name)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except KeyError:
            return default


class ScreenBounds(BaseModel):
    width: int
    height: int

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


class FocusedElement(BaseModel):
    resource_id: str = Field("", alias="resourceId")
    class_name: str = Field("", alias="className")

    model_config = {"populate_by_name": True}

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        for field_name, field_info in type(self).model_fields.items():
            if field_info.alias == item:
                return getattr(self, field_name)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except KeyError:
            return default


class PhoneState(BaseModel):
    """Mirrors the ``phone`` sub-object of a summarised observation."""

    package_name: str = Field("", alias="packageName")
    current_app: str = Field("", alias="currentApp")
    is_editable: bool = Field(False, alias="isEditable")
    input_element_id: str | None = Field(None, alias="inputElementId")
    focus_evidence: str = Field("none", alias="focusEvidence")
    keyboard_visible: bool = Field(False, alias="keyboardVisible")
    focused_element: FocusedElement = Field(
        default_factory=FocusedElement, alias="focusedElement"
    )

    model_config = {"populate_by_name": True}

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        for field_name, field_info in type(self).model_fields.items():
            if field_info.alias == item:
                return getattr(self, field_name)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except KeyError:
            return default


class Observation(BaseModel):
    """Complete summarised device state – the central data contract."""

    device_id: str = Field("", alias="deviceId")
    phone: PhoneState = Field(default_factory=PhoneState)
    screen: ScreenBounds
    elements: list[Element] = Field(default_factory=list)
    fingerprint: str = ""
    observed_at: float = Field(0.0, alias="observedAt")
    screenshot: bytes | None = None
    device_info: DeviceInfo | None = Field(None, alias="deviceInfo")

    model_config = {"populate_by_name": True}

    def __getitem__(self, item: str) -> Any:
        if hasattr(self, item):
            return getattr(self, item)
        for field_name, field_info in type(self).model_fields.items():
            if field_info.alias == item:
                return getattr(self, field_name)
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except KeyError:
            return default

    def __contains__(self, item: str) -> bool:
        if hasattr(self, item):
            return True
        for field_info in type(self).model_fields.values():
            if field_info.alias == item:
                return True
        return False


class DeviceInfo(BaseModel):
    """Information returned by ``GET /device``."""

    id: str
    name: str
    state: str
    stream_url: str | None = Field(None, alias="streamUrl")
    stream_token: str | None = Field(None, alias="streamToken")
    environment: str = "Local"

    model_config = {"populate_by_name": True}


class InstalledApp(BaseModel):
    package_name: str = Field(..., alias="packageName")
    label: str

    model_config = {"populate_by_name": True}
