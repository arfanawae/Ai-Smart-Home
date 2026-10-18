"""Tool contract (Blueprint §12.3). The same specs will be exposed by the MCP server in M4.

The LLM only ever sees these intent-level tools — never pins, PWM or GPIO.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEVICE_IDS = ("main_light",)

LEVEL_MIN, LEVEL_MAX = 0, 100
DELTA_LIMIT = 50
ADJUST_FLOOR = 10  # relative dimming never goes below this — "dimmer" should not mean "off"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


def _schema(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "device_id": {"type": "string", "enum": list(DEVICE_IDS), "description": "main_light = ไฟห้องหลัก"},
    }
    properties.update(extra or {})
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "get_device_status",
        "Read the current reported state of a device (online, on/off, brightness %).",
        _schema(),
    ),
    ToolSpec("turn_light_on", "Turn the light on (keeps its last brightness).", _schema()),
    ToolSpec("turn_light_off", "Turn the light off.", _schema()),
    ToolSpec(
        "set_brightness",
        "Set the light to an absolute brightness percentage. 0 turns it off.",
        _schema({"level": {"type": "integer", "minimum": LEVEL_MIN, "maximum": LEVEL_MAX}}),
    ),
    ToolSpec(
        "adjust_brightness",
        "Change brightness relative to the current reported level, e.g. -20 when the user says it is "
        f"too bright. The server clamps the result to {ADJUST_FLOOR}-{LEVEL_MAX}% and reports the value used.",
        _schema({"delta": {"type": "integer", "minimum": -DELTA_LIMIT, "maximum": DELTA_LIMIT}}),
    ),
)

TOOL_NAMES = frozenset(spec.name for spec in TOOL_SPECS)
