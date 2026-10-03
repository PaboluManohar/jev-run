"""Pydantic models for action variants dispatched to the device."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class TapAction(BaseModel):
    type: Literal["tap"] = "tap"
    x: int
    y: int


class TapElementAction(BaseModel):
    type: Literal["tap-element"] = "tap-element"
    element_id: str = Field(..., alias="elementId")

    model_config = {"populate_by_name": True}


class SwipeAction(BaseModel):
    type: Literal["swipe"] = "swipe"
    start_x: int = Field(..., alias="startX")
    start_y: int = Field(..., alias="startY")
    end_x: int = Field(..., alias="endX")
    end_y: int = Field(..., alias="endY")
    duration: int = 300
    region_id: str | None = Field(None, alias="regionId")

    model_config = {"populate_by_name": True}


class TypeAction(BaseModel):
    type: Literal["type"] = "type"
    text: str
    clear: bool = False


class ClearAction(BaseModel):
    type: Literal["clear"] = "clear"


class KeyAction(BaseModel):
    type: Literal["key"] = "key"
    key: str


class GlobalAction(BaseModel):
    type: Literal["global"] = "global"
    name: str


class OpenAppAction(BaseModel):
    type: Literal["open-app"] = "open-app"
    package_name: str = Field(..., alias="packageName")
    app_label: str = Field("", alias="appLabel")

    model_config = {"populate_by_name": True}


class WaitAction(BaseModel):
    type: Literal["wait"] = "wait"


# Union type for dispatching.
Action = (
    TapAction
    | TapElementAction
    | SwipeAction
    | TypeAction
    | ClearAction
    | KeyAction
    | GlobalAction
    | OpenAppAction
    | WaitAction
)

# Key-name → Android keycode mapping (matches the JS ``KEYS`` constant).
KEYS: dict[str, int] = {
    "back": 4,
    "tab": 61,
    "enter": 66,
    "delete": 67,
    "forward_delete": 112,
}

# Global action name → Android keycode.
GLOBAL_KEYCODES: dict[str, int] = {
    "back": 4,
    "home": 3,
    "recent": 187,
}
