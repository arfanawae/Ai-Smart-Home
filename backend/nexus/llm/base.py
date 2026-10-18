"""Provider-neutral LLM interface, so Gemini can be swapped for another provider without touching the assistant."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ..tools import ToolSpec

LLMErrorKind = Literal["quota", "auth", "model_not_found", "bad_request", "timeout", "unavailable"]


@dataclass(frozen=True)
class ToolCall:
    id: str | None
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str | None
    name: str
    payload: dict[str, Any]


@dataclass
class ChatMessage:
    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # Provider-native message, replayed verbatim (Gemini needs its thought signatures back).
    raw: Any = None


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    raw: Any = None


class LLMError(Exception):
    def __init__(self, kind: LLMErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class LLMProvider(Protocol):
    provider: str
    model: str

    async def generate(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
    ) -> LLMResponse: ...
