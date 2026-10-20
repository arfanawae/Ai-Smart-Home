import asyncio

from nexus.banner import render
from nexus.llm.base import LLMError, LLMResponse
from nexus.llm.fallback import FallbackLLM


def test_banner_names_the_provider():
    lines = render("ACTIVE PROVIDER: GROQ", "openai/gpt-oss-120b | AI request")
    assert lines[0] == lines[-1] == "=" * 64
    assert "=== ACTIVE PROVIDER: GROQ ===" in lines[1] and "openai/gpt-oss-120b" in lines[2]


class Scripted:
    def __init__(self, name, outcome):
        self.provider, self.model, self.outcome = name, name + "-model", outcome

    async def generate(self, system, messages, tools):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_each_turn_and_each_fallback_prints_a_banner(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_PROVIDER_BANNER", "1")
    from tests.test_assistant import make  # the real assistant with a mock light

    llm = FallbackLLM(Scripted("gemini", LLMError("rate_limit", "429", retry_after_s=60)), Scripted("groq", LLMResponse("สวัสดีครับ", [])))
    assistant, _, _ = make(llm, tmp_path)
    asyncio.run(assistant.handle("วันนี้ร้อนจัง"))
    out = capsys.readouterr().out
    assert "=== ACTIVE PROVIDER: GEMINI ===" in out and "=== FALLBACK -> GROQ ===" in out
    asyncio.run(assistant.handle("เปิดไฟ"))  # Gemini is now skipped for a while
    out = capsys.readouterr().out
    assert "=== ACTIVE PROVIDER: GROQ ===" in out and "fast path" in out


def test_banner_can_be_turned_off(capsys, monkeypatch):
    from nexus.banner import provider_banner

    monkeypatch.setenv("LOG_PROVIDER_BANNER", "0")
    provider_banner("groq", "m", "x")
    assert capsys.readouterr().out == ""
