"""Gemini adapter (google-genai SDK). Automatic function calling is disabled:
the assistant executes tools itself so every call passes the validator and the device service."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence

import httpx
from google import genai
from google.genai import errors, types

from ..tools import ToolSpec
from .base import ChatMessage, LLMError, LLMResponse, ToolCall


class GeminiProvider:
    provider = "gemini"

    def __init__(self, api_key: str, model: str, timeout_s: float = 20.0) -> None:
        self.model = model
        self._timeout_s = timeout_s
        self._client = genai.Client(api_key=api_key)

    @staticmethod
    def _to_content(message: ChatMessage) -> types.Content:
        if message.raw is not None:
            return message.raw
        if message.role == "user":
            return types.Content(role="user", parts=[types.Part(text=message.text)])
        if message.role == "assistant":
            parts = [types.Part(text=message.text)] if message.text else []
            parts += [
                types.Part(function_call=types.FunctionCall(id=c.id, name=c.name, args=c.args))
                for c in message.tool_calls
            ]
            return types.Content(role="model", parts=parts)
        return types.Content(
            role="user",
            parts=[
                types.Part(function_response=types.FunctionResponse(id=r.call_id, name=r.name, response=r.payload))
                for r in message.tool_results
            ],
        )

    @staticmethod
    def _to_tool(tools: Sequence[ToolSpec]) -> types.Tool:
        return types.Tool(
            function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
                for t in tools
            ]
        )

    def _config(self, system: str, tools: Sequence[ToolSpec]) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            tools=[self._to_tool(tools)] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    async def generate(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
    ) -> LLMResponse:
        response: LLMResponse | None = None
        async for event in self.generate_stream(system, messages, tools):
            if isinstance(event, LLMResponse):
                response = event
        assert response is not None
        return response

    async def generate_stream(
        self,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[str | LLMResponse]:
        """Yield text deltas as Gemini produces them, then the full LLMResponse (with every part kept,
        so thought signatures on function calls are replayed exactly)."""
        parts: list[types.Part] = []
        finish_reason = None
        try:
            async with asyncio.timeout(self._timeout_s):
                stream = await self._client.aio.models.generate_content_stream(
                    model=self.model,
                    contents=[self._to_content(m) for m in messages],
                    config=self._config(system, tools),
                )
                async for chunk in stream:
                    candidate = chunk.candidates[0] if chunk.candidates else None
                    if candidate is None:
                        continue
                    finish_reason = candidate.finish_reason or finish_reason
                    for part in (candidate.content.parts if candidate.content else None) or []:
                        parts.append(part)
                        if part.text and not part.thought:
                            yield part.text
        except TimeoutError as e:
            raise LLMError("timeout", f"Gemini ไม่ตอบภายใน {self._timeout_s:g} วินาที") from e
        except errors.APIError as e:
            raise _map_api_error(e) from e
        except httpx.HTTPError as e:
            raise LLMError("unavailable", f"เชื่อมต่อ Gemini ไม่ได้: {e}") from e

        text = "".join(p.text for p in parts if p.text and not p.thought).strip()
        calls = [
            ToolCall(id=p.function_call.id, name=p.function_call.name or "", args=dict(p.function_call.args or {}))
            for p in parts
            if p.function_call
        ]
        if not text and not calls:
            raise LLMError("bad_request", f"Gemini ไม่ได้ส่งคำตอบกลับมา (finish_reason={finish_reason})")
        yield LLMResponse(text=text, tool_calls=calls, raw=types.Content(role="model", parts=parts))

    async def list_models(self) -> list[str]:
        """Model names usable with generateContent — use this to pick LLM_MODEL instead of guessing."""
        pager = await self._client.aio.models.list()
        names = []
        async for model in pager:
            if "generateContent" in (model.supported_actions or []):
                names.append((model.name or "").removeprefix("models/"))
        return sorted(names)


_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')
DEFAULT_RETRY_S = 30.0


def _rate_error(e: errors.APIError, detail: str) -> LLMError:
    """A 429 is either the daily quota (until the reset) or a per-minute limit (seconds). Gemini says
    which in QuotaFailure.violations[].quotaId ("...PerDay..." / "...PerMinute...") and how long to
    wait in RetryInfo.retryDelay. Unknown → per-minute: one burst must never switch the AI off all day."""
    raw = json.dumps(e.details, default=str) if e.details else detail
    if "PerDay" in raw:
        return LLMError("quota", f"โควตา Gemini ของวันนี้หมดแล้ว: {detail}")
    match = _RETRY_DELAY.search(raw)
    wait = float(match.group(1)) if match else DEFAULT_RETRY_S
    return LLMError("rate_limit", f"เรียก Gemini ถี่เกินไป — รอประมาณ {wait:.0f} วินาที", retry_after_s=wait)


def _map_api_error(e: errors.APIError) -> LLMError:
    detail = e.message or str(e)
    match e.code:
        case 429:
            return _rate_error(e, detail)
        case 401 | 403:
            return LLMError("auth", f"API key ของ Gemini ไม่ถูกต้องหรือไม่มีสิทธิ์: {detail}")
        case 404:
            return LLMError("model_not_found", f"ไม่พบโมเดลนี้ — ตรวจ LLM_MODEL ด้วย `python -m nexus models`: {detail}")
        case 400:
            return LLMError("bad_request", f"Gemini ปฏิเสธคำขอ: {detail}")
    return LLMError("unavailable", f"Gemini ขัดข้อง ({e.code}): {detail}")
