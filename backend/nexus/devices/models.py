"""Device state and command result types shared by the assistant, (future) MCP server and UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

DEVICE_NAMES_TH = {"main_light": "ไฟห้องหลัก"}


class CommandStatus(StrEnum):
    SUCCEEDED = "succeeded"  # device reported the desired state
    FAILED = "failed"  # device error, offline, or reported state != desired
    TIMEOUT = "timeout"  # no reply — real state is unknown
    REJECTED = "rejected"  # blocked by validation / policy, never sent


@dataclass(frozen=True)
class LightState:
    """Reported state of a light. `brightness` is the last level (1–100), kept while off."""

    online: bool
    on: bool
    brightness: int
    updated_at: datetime
    source: str  # "mock" | "device"

    def to_dict(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "on": self.on,
            "brightness": self.brightness,
            "updated_at": self.updated_at.isoformat(timespec="seconds"),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LightState:
        return cls(
            online=bool(data["online"]),
            on=bool(data["on"]),
            brightness=int(data["brightness"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            source=str(data["source"]),
        )

    def describe_th(self) -> str:
        if not self.online:
            return "ออฟไลน์"
        return f"เปิด {self.brightness}%" if self.on else "ปิด"


@dataclass(frozen=True)
class CommandResult:
    tool: str
    args: dict[str, Any]
    status: CommandStatus
    simulated: bool
    command_id: str | None = None
    previous: LightState | None = None
    reported: LightState | None = None
    error: str | None = None
    latency_ms: int | None = None
    device_id: str | None = field(default=None)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandResult:
        """Inverse of to_dict() — how results come back from the MCP server."""

        def state(value: Any) -> LightState | None:
            return LightState.from_dict(value) if value else None

        return cls(
            tool=str(data["tool"]),
            args=dict(data.get("args") or {}),
            status=CommandStatus(data["status"]),
            simulated=bool(data["simulated"]),
            command_id=data.get("command_id"),
            previous=state(data.get("previous")),
            reported=state(data.get("reported")),
            error=data.get("error"),
            latency_ms=data.get("latency_ms"),
            device_id=data.get("device_id"),
        )

    @property
    def ok(self) -> bool:
        return self.status is CommandStatus.SUCCEEDED

    def to_dict(self) -> dict[str, Any]:
        """Full record for logs and the UI (the LLM gets the narrower to_tool_payload)."""
        return {
            "tool": self.tool,
            "args": self.args,
            "status": self.status.value,
            "simulated": self.simulated,
            "command_id": self.command_id,
            "device_id": self.device_id,
            "previous": self.previous.to_dict() if self.previous else None,
            "reported": self.reported.to_dict() if self.reported else None,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }

    def to_tool_payload(self) -> dict[str, Any]:
        """What the LLM sees as the tool result. It must never be able to mistake a simulation for reality."""
        payload: dict[str, Any] = {
            "status": self.status.value,
            "simulated": self.simulated,
            "device_id": self.device_id,
            "state_before": self.previous.to_dict() if self.previous else None,
            "state_after": self.reported.to_dict() if self.reported else None,
        }
        if self.error:
            payload["error"] = self.error
        if self.simulated:
            payload["note"] = (
                "SIMULATION ONLY: no real device was controlled. "
                "Tell the user this was a simulation (จำลอง)."
            )
        if self.status is CommandStatus.TIMEOUT:
            payload["note_timeout"] = "The real light state is UNKNOWN. Do not claim success."
        return payload
