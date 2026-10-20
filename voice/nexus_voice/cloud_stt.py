"""Speech-to-text for audio recorded in the web dashboard (16 kHz mono WAV from the browser).

    BROWSER_STT=auto (default) → Azure Speech first when AZURE_SPEECH_KEY is set, then the providers below
    BROWSER_STT=provider       → skip Azure
    VOICE_PROVIDER=groq   → Groq Whisper (whisper-large-v3-turbo), then Gemini as fallback
    VOICE_PROVIDER=gemini → Gemini (audio in, transcript out), then Groq Whisper as fallback

Why Azure first (measured 2026-10-20, Thai TTS clips): Gemini heard "ปิดไฟ" as "เปิดไฟ" once in 7
on/off commands — the opposite action — and "หรี่ไฟ" as "Reel ไฟ"; Azure (already tuned for Thai,
free F0) got every on/off word right and was faster. Gemini transcription also spends LLM quota.

Both are free tiers. The transcript then goes through the normal assistant turn (fast path, tools,
claim guard), exactly like speech from the laptop microphone.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import wave
from dataclasses import dataclass

import httpx
from nexus.config import Settings
from nexus.language import detect_language

from .speech import ENGLISH, THAI

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GEMINI_PROMPT = (
    "Transcribe exactly what is said in this audio. The speaker uses Thai or English. "
    "Output only the transcript in the language spoken, with no notes or translation. "
    "If nothing intelligible is said, output nothing."
)


class TranscriptionError(Exception):
    pass


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str  # "th-TH" | "en-US"
    provider: str


def _locale(text: str, hint: str | None = None) -> str:
    if hint:
        h = hint.lower()
        if h.startswith("th"):
            return THAI
        if h.startswith("en"):
            return ENGLISH
    return ENGLISH if detect_language(text) == "en" else THAI


class GroqWhisper:
    name = "groq"

    def __init__(self, api_key: str, model: str, timeout_s: float = 20.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout_s, transport=transport)
        self.model = model

    async def transcribe(self, wav: bytes) -> Transcript:
        try:
            response = await self._client.post(
                GROQ_URL,
                data={"model": self.model, "response_format": "verbose_json", "temperature": "0"},
                files={"file": ("speech.wav", wav, "audio/wav")},
            )
        except httpx.HTTPError as e:
            raise TranscriptionError(f"เชื่อมต่อ Groq ไม่ได้ ({type(e).__name__})") from e
        if response.status_code != 200:
            raise TranscriptionError(f"Groq Whisper ตอบ HTTP {response.status_code}: {response.text[:160]}")
        body = response.json()
        text = str(body.get("text", "")).strip()
        return Transcript(text, _locale(text, body.get("language")), self.name)


class GeminiAudio:
    name = "gemini"

    def __init__(self, api_key: str, model: str, quota=None) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self.model = model
        self._quota = quota  # the same daily counter the assistant uses

    async def transcribe(self, wav: bytes) -> Transcript:
        from google.genai import errors, types

        if self._quota is not None:
            self._quota.record()
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=[types.Part.from_bytes(data=wav, mime_type="audio/wav"), GEMINI_PROMPT],
            )
        except errors.APIError as e:
            raise TranscriptionError(f"Gemini ถอดเสียงไม่ได้ ({e.code}): {e.message}") from e
        except httpx.HTTPError as e:
            raise TranscriptionError(f"เชื่อมต่อ Gemini ไม่ได้ ({type(e).__name__})") from e
        text = (response.text or "").strip()
        return Transcript(text, _locale(text), self.name)


class AzureEngine:
    name = "azure"

    def __init__(self, speech) -> None:
        self._speech = speech  # nexus_voice.speech.AzureSpeech

    async def transcribe(self, wav: bytes) -> Transcript:
        try:
            with wave.open(io.BytesIO(wav)) as w:
                pcm = w.readframes(w.getnframes())
        except (wave.Error, EOFError) as e:
            raise TranscriptionError("ไฟล์เสียงจากหน้าเว็บอ่านไม่ได้") from e
        result = await asyncio.to_thread(self._speech.transcribe, pcm)
        if result.status == "error":
            raise TranscriptionError(f"Azure ถอดเสียงไม่ได้: {result.error}")
        return Transcript(result.text.strip(), result.language or _locale(result.text), self.name)


class CloudSTT:
    """Tries the VOICE_PROVIDER first, then the other provider that has a key."""

    def __init__(self, engines: list) -> None:
        self.engines = engines

    @classmethod
    def from_settings(cls, settings: Settings, quota=None) -> CloudSTT:
        groq = GroqWhisper(settings.groq_api_key, settings.groq_stt_model) if settings.groq_api_key else None
        gemini = GeminiAudio(settings.gemini_api_key, settings.llm_model, quota) if settings.gemini_api_key else None
        order: list = [groq, gemini] if settings.voice_provider == "groq" else [gemini, groq]
        if os.getenv("BROWSER_STT", "auto").strip().lower() != "provider":
            order.insert(0, _azure_engine())
        return cls([e for e in order if e is not None])

    async def transcribe(self, wav: bytes) -> Transcript:
        if not self.engines:
            raise TranscriptionError("ยังไม่มี GEMINI_API_KEY หรือ GROQ_API_KEY สำหรับถอดเสียงจากหน้าเว็บ")
        errors = []
        for engine in self.engines:
            try:
                return await engine.transcribe(wav)
            except TranscriptionError as e:
                log.warning("speech-to-text via %s failed: %s", engine.name, e)
                errors.append(str(e))
        raise TranscriptionError(" · ".join(errors))


def _azure_engine() -> AzureEngine | None:
    from .config import load_settings
    from .speech import AzureSpeech

    voice = load_settings()
    if not (voice.azure_key and voice.azure_region):
        return None
    return AzureEngine(AzureSpeech(voice.azure_key, voice.azure_region, voice.stt_languages, voice.tts_voices, voice.stt_language_mode))
