"""Parse ``uiautomator dump`` XML into the observation format.

This module transforms the XML output of ``adb shell uiautomator dump
/dev/tty`` into the same structured JSON that MobileRun's
``GET /devices/{id}/ui-state`` endpoint returned, so that
``summarize_state()`` (and all downstream logic) works unchanged.

The XML schema emitted by ``uiautomator`` looks like::

    <hierarchy rotation="0">
      <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
            package="com.android.settings" content-desc="" checkable="false"
            checked="false" clickable="false" enabled="true" focusable="false"
            focused="false" scrollable="false" long-clickable="false"
            password="false" selected="false" bounds="[0,0][1080,2400]">
        <node ...>
          ...
        </node>
      </node>
    </hierarchy>
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

# Matches the ``bounds="[left,top][right,bottom]"`` attribute.
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _parse_bounds(bounds_str: str) -> dict[str, int] | None:
    """Convert ``[left,top][right,bottom]`` into a bounds dict."""
    m = _BOUNDS_RE.match(bounds_str)
    if not m:
        return None
    return {
        "left": int(m.group(1)),
        "top": int(m.group(2)),
        "right": int(m.group(3)),
        "bottom": int(m.group(4)),
    }


def _bool(element: ET.Element, attr: str) -> bool:
    return element.get(attr, "false").lower() == "true"


def _xml_node_to_a11y(element: ET.Element) -> dict[str, Any]:
    """Convert a single ``<node>`` XML element into the a11y tree dict
    that ``summarize_state()`` expects (matching MobileRun's JSON shape).
    """
    bounds = _parse_bounds(element.get("bounds", ""))
    node: dict[str, Any] = {
        "className": element.get("class", ""),
        "text": element.get("text", ""),
        "contentDescription": element.get("content-desc", ""),
        "resourceId": element.get("resource-id", ""),
        "hint": element.get("hint", ""),
        "isClickable": _bool(element, "clickable"),
        "isEditable": _bool(element, "editable"),
        "isScrollable": _bool(element, "scrollable"),
        "isVisibleToUser": True,  # uiautomator only dumps visible nodes
        "isEnabled": _bool(element, "enabled"),
        "isFocused": _bool(element, "focused"),
        "isPassword": _bool(element, "password"),
        "isCheckable": _bool(element, "checkable"),
        "isChecked": _bool(element, "checked"),
        "isSelected": _bool(element, "selected"),
    }

    # The "editable" attribute is not always present in uiautomator XML.
    # Fall back to class-name heuristic matching the JS logic.
    if not node["isEditable"] and node["className"] in (
        "android.widget.EditText",
        "android.widget.AutoCompleteTextView",
        "android.widget.MultiAutoCompleteTextView",
    ):
        node["isEditable"] = True

    if bounds:
        node["boundsInScreen"] = bounds

    children = []
    for child in element:
        children.append(_xml_node_to_a11y(child))
    if children:
        node["children"] = children

    return node


def parse_ui_xml(xml_text: str) -> dict[str, Any]:
    """Parse the full ``uiautomator dump`` output into an a11y tree dict.

    Returns the root node in MobileRun's ``a11y_tree`` format.
    """
    # ``uiautomator dump /dev/tty`` prefixes with a message line.
    # Strip everything before the first ``<``.
    start = xml_text.find("<")
    if start < 0:
        raise ValueError("No XML content found in uiautomator output.")
    xml_text = xml_text[start:]

    root = ET.fromstring(xml_text)

    # The root is ``<hierarchy>``; the first ``<node>`` is the actual tree.
    nodes = list(root)
    if not nodes:
        raise ValueError("Empty UI hierarchy — no nodes found.")

    # If there is a single root node, return it directly.
    # Otherwise wrap children under a synthetic FrameLayout.
    if len(nodes) == 1:
        return _xml_node_to_a11y(nodes[0])
    else:
        return {
            "className": "android.widget.FrameLayout",
            "text": "",
            "contentDescription": "",
            "resourceId": "",
            "isClickable": False,
            "isEditable": False,
            "isScrollable": False,
            "isVisibleToUser": True,
            "isEnabled": True,
            "boundsInScreen": {"left": 0, "top": 0, "right": 0, "bottom": 0},
            "children": [_xml_node_to_a11y(n) for n in nodes],
        }
