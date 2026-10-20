"""Groq Cloud (free tier) as an LLM provider (function calling). Default model openai/gpt-oss-120b:
measured 2026-10-20 on Thai/English NEXUS turns: intent 5/5, reply language 5/5, ~2 s.
(llama-3.3-70b-versatile is no longer offered.)

Groq speaks the OpenAI chat-completions format, so this is plain HTTP (httpx) — no extra SDK.
Messages are rebuilt from the provider-neutral ChatMessage every time, so a turn that started on
Gemini can continue here (fallback) and vice versa.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

import httpx

from ..tools import ToolSpec
from .base import ChatMessage, LLMError, LLMResponse, ToolCall

BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_RETRY_S = 20.0


class GroqProvider:
    provider = "groq"

    def __init__(self, api_key: str, model: str, timeout_s: float = 20.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
            transport=transport,  # tests inject a mock transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── message conversion ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _to_messages(system: str, messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        last_ids: list[str] = []  # ids of the previous assistant's tool calls, for results without one
        for n, m in enumerate(messages):
            if m.role == "user":
                out.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                last_ids = [c.id or f"call_{n}_{i}" for i, c in enumerate(m.tool_calls)]
                message: dict[str, Any] = {"role": "assistant", "content": m.text or None}
                if m.tool_calls:
                    message["tool_calls"] = [
                        {"id": cid, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}}
                        for cid, c in zip(last_ids, m.tool_calls)
                    ]
                out.append(message)
            else:
                for i, r in enumerate(m.tool_results):
                    cid = r.call_id or (last_ids[i] if i < len(last_ids) else f"call_{n}_{i}")
                    out.append({"role": "tool", "tool_call_id": cid, "content": json.dumps(r.payload, ensure_ascii=False)})
        return out

    @staticmethod
    def _to_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]

    # ── call ────────────────────────────────────────────────────────────────────────────────────
    async def generate(self, system: str, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> LLMResponse:
        body: dict[str, Any] = {"model": self.model, "messages": self._to_messages(system, messages), "temperature": 0.3}
        if tools:
            body["tools"] = self._to_tools(tools)
            body["tool_choice"] = "auto"
        try:
            response = await self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException as e:
            raise LLMError("timeout", "Groq ไม่ตอบภายในเวลาที่กำหนด") from e
        except httpx.HTTPError as e:
            raise LLMError("unavailable", f"เชื่อมต่อ Groq ไม่ได้: {e}") from e
        if response.status_code != 200:
            raise _map_error(response)
        try:
            message = response.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError("bad_request", "Groq ส่งคำตอบที่อ่านไม่ได้") from e

        calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}  # DeviceService validation rejects it with a clear reason
            calls.append(ToolCall(id=call.get("id"), name=fn.get("name", ""), args=args if isinstance(args, dict) else {}))
        text = (message.get("content") or "").strip()
        if not text and not calls:
            raise LLMError("bad_request", "Groq ไม่ได้ส่งคำตอบกลับมา")
        return LLMResponse(text=text, tool_calls=calls)


def _map_error(response: httpx.Response) -> LLMError:
    try:
        detail = str(response.json().get("error", {}).get("message", ""))
    except ValueError:
        detail = response.text[:200]
    match response.status_code:
        case 401 | 403:
            return LLMError("auth", f"GROQ_API_KEY ไม่ถูกต้องหรือไม่มีสิทธิ์: {detail}")
        case 404:
            return LLMError("model_not_found", f"ไม่พบโมเดลบน Groq — ตรวจ GROQ_MODEL: {detail}")
        case 429:
            # Groq limits per minute (RPM/TPM) and per day (RPD/TPD); the message names which one.
            if re.search(r"per day|\b(?:RPD|TPD)\b", detail, re.IGNORECASE):
                return LLMError("quota", f"โควตา Groq ของวันนี้หมดแล้ว: {detail}")
            try:
                wait = float(response.headers.get("retry-after", DEFAULT_RETRY_S))
            except ValueError:
                wait = DEFAULT_RETRY_S
            return LLMError("rate_limit", f"เรียก Groq ถี่เกินไป — รอประมาณ {wait:.0f} วินาที", retry_after_s=wait)
        case 400 | 422:
            return LLMError("bad_request", f"Groq ปฏิเสธคำขอ: {detail}")
    return LLMError("unavailable", f"Groq ขัดข้อง ({response.status_code}): {detail}")
