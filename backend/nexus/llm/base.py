"""Provider-neutral LLM interface, so Gemini can be swapped for another provider without touching the assistant."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ..tools import ToolSpec

# "quota": the daily quota is used up (until the reset) · "rate_limit": too many requests per minute (seconds)
LLMErrorKind = Literal["quota", "rate_limit", "auth", "model_not_found", "bad_request", "timeout", "unavailable"]


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
    def __init__(self, kind: LLMErrorKind, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_after_s = retry_after_s  # rate_limit: how long Gemini asked us to wait


class LLMProvider(Protocol):
    provider: str
    model: str

    async def generate(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
    ) -> LLMResponse: ...


async def stream_reply(
    llm: LLMProvider, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
) -> AsyncIterator[str | LLMResponse]:
    """Text deltas as they are generated, then the complete LLMResponse last.
    Providers without a `generate_stream` method yield their whole text as one delta."""
    stream = getattr(llm, "generate_stream", None)
    if stream is not None:
        async for event in stream(system, messages, tools):
            yield event
        return
    response = await llm.generate(system, messages, tools)
    if response.text:
        yield response.text
    yield response
