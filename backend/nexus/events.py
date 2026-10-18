"""AI states (Blueprint §7.1) and the events the assistant emits. The CLI prints them now;
the WebSocket server will forward the same events to the web UI later."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .devices.models import CommandResult


class AIState(StrEnum):
    IDLE = "IDLE"
    WAKE_DETECTED = "WAKE_DETECTED"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    WAITING_FOR_DEVICE = "WAITING_FOR_DEVICE"
    SPEAKING = "SPEAKING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    DISCONNECTED = "DISCONNECTED"


STATE_LABELS_TH = {
    AIState.IDLE: "พร้อมรับคำสั่ง",
    AIState.WAKE_DETECTED: "ว่าไงครับ",
    AIState.LISTENING: "กำลังฟัง…",
    AIState.TRANSCRIBING: "กำลังแปลงเสียง…",
    AIState.THINKING: "กำลังคิด…",
    AIState.EXECUTING: "กำลังส่งคำสั่ง…",
    AIState.WAITING_FOR_DEVICE: "รออุปกรณ์ยืนยัน…",
    AIState.SPEAKING: "กำลังพูด…",
    AIState.SUCCESS: "สำเร็จ",
    AIState.ERROR: "เกิดข้อผิดพลาด",
    AIState.DISCONNECTED: "ขาดการเชื่อมต่อ",
}


@dataclass(frozen=True)
class StateChanged:
    state: AIState
    detail: str = ""


@dataclass(frozen=True)
class CommandUpdate:
    result: CommandResult
    route: str  # "fastpath" | "llm"


@dataclass(frozen=True)
class AssistantReply:
    text: str
    route: str  # "fastpath" | "llm" | "system"


@dataclass(frozen=True)
class UserMessage:
    text: str
    source: str  # "voice" | "text"
    language: str | None = None


Event = StateChanged | CommandUpdate | AssistantReply | UserMessage
EventSink = Callable[[Event], None]


def to_dict(event: Event) -> dict[str, Any]:
    """JSON-ready form of an event — the wire format for the WebSocket UI feed."""
    match event:
        case StateChanged(state=state, detail=detail):
            return {"type": "state", "state": state.value, "label": STATE_LABELS_TH[state], "detail": detail}
        case CommandUpdate(result=result, route=route):
            return {"type": "command", "route": route, **result.to_dict()}
        case AssistantReply(text=text, route=route):
            return {"type": "reply", "text": text, "route": route}
        case UserMessage(text=text, source=source, language=language):
            return {"type": "user", "text": text, "source": source, "language": language}
    raise TypeError(f"unknown event {event!r}")
