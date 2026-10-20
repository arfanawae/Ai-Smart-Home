"""Groq (OpenAI-format) provider, the Gemini⇄Groq fallback, and VOICE_PROVIDER selection — no network."""

import asyncio
import dataclasses
import json

import httpx
import pytest

from nexus.cli import build_llm
from nexus.config import load_settings
from nexus.llm.base import ChatMessage, LLMError, LLMResponse, ToolCall, ToolResult
from nexus.llm.fallback import FallbackLLM
from nexus.llm.groq import GroqProvider
from nexus.tools import TOOL_SPECS


def groq_with(handler) -> GroqProvider:
    return GroqProvider("test-key", "openai/gpt-oss-120b", transport=httpx.MockTransport(handler))


def test_tool_call_round_trip_in_openai_format():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-key"
        call = {"id": "call_1", "type": "function", "function": {"name": "turn_light_off", "arguments": '{"device_id": "main_light"}'}}
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]})

    history = [
        ChatMessage("user", "เปิดไฟ"),
        ChatMessage("assistant", "", [ToolCall(None, "turn_light_on", {"device_id": "main_light"})]),  # e.g. from Gemini: no id
        ChatMessage("tool", tool_results=[ToolResult(None, "turn_light_on", {"status": "succeeded"})]),
        ChatMessage("user", "ปิดไฟ"),
    ]
    response = asyncio.run(groq_with(handler).generate("system", history, TOOL_SPECS))
    assert response.tool_calls == [ToolCall("call_1", "turn_light_off", {"device_id": "main_light"})]
    roles = [m["role"] for m in seen["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    # a tool result without an id is matched to the call before it
    assert seen["messages"][3]["tool_call_id"] == seen["messages"][2]["tool_calls"][0]["id"]
    assert {t["function"]["name"] for t in seen["tools"]} >= {"turn_light_on", "turn_light_off", "set_brightness"}


@pytest.mark.parametrize(
    ("status", "body", "headers", "kind", "retry"),
    [
        (429, {"error": {"message": "Rate limit reached for model on requests per minute (RPM)"}}, {"retry-after": "7"}, "rate_limit", 7),
        (429, {"error": {"message": "Rate limit reached on tokens per day (TPD)"}}, {}, "quota", None),
        (401, {"error": {"message": "Invalid API Key"}}, {}, "auth", None),
        (503, {"error": {"message": "over capacity"}}, {}, "unavailable", None),
    ],
)
def test_errors_are_classified(status, body, headers, kind, retry):
    provider = groq_with(lambda _r: httpx.Response(status, json=body, headers=headers))
    with pytest.raises(LLMError) as e:
        asyncio.run(provider.generate("s", [ChatMessage("user", "hi")], []))
    assert e.value.kind == kind and e.value.retry_after_s == retry


class Scripted:
    def __init__(self, name, *outcomes):
        self.provider, self.model, self.outcomes, self.calls = name, name, list(outcomes), 0

    async def generate(self, system, messages, tools):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_fallback_on_rate_limit_then_skips_the_primary_for_a_while():
    primary = Scripted("gemini", LLMError("rate_limit", "429", retry_after_s=60))
    secondary = Scripted("groq", LLMResponse("ok 1", []), LLMResponse("ok 2", []))
    llm = FallbackLLM(primary, secondary)
    assert asyncio.run(llm.generate("s", [], [])).text == "ok 1"
    assert asyncio.run(llm.generate("s", [], [])).text == "ok 2"
    assert (primary.calls, secondary.calls) == (1, 2)


def test_no_fallback_for_a_bad_request():
    llm = FallbackLLM(Scripted("gemini", LLMError("bad_request", "400")), Scripted("groq"))
    with pytest.raises(LLMError, match="400"):
        asyncio.run(llm.generate("s", [], []))


def test_voice_provider_selects_order_and_missing_key_falls_back(monkeypatch):
    base = dataclasses.replace(load_settings(), gemini_api_key="g", groq_api_key="q")
    llm = build_llm(dataclasses.replace(base, voice_provider="groq"))
    assert isinstance(llm, FallbackLLM) and (llm.primary.provider, llm.secondary.provider) == ("groq", "gemini")
    warnings = []
    only_gemini = build_llm(dataclasses.replace(base, voice_provider="groq", groq_api_key=None), warn=warnings.append)
    assert only_gemini.provider == "gemini" and "GROQ_API_KEY" in warnings[0]
    assert build_llm(dataclasses.replace(base, gemini_api_key=None, groq_api_key=None), warn=warnings.append) is None
