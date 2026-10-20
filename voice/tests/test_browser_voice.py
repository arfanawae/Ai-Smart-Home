"""Voice recorded in the dashboard: message validation and cloud speech-to-text with fallback."""

import asyncio
import base64
import io
import json
import wave

import httpx
import pytest

from nexus_voice.broadcast import InvalidMessage, parse_client_message
from nexus_voice.cloud_stt import CloudSTT, GroqWhisper, TranscriptionError, Transcript


def wav_bytes(seconds: float = 0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * seconds))
    return buf.getvalue()


def audio_message(wav: bytes) -> str:
    return json.dumps({"type": "audio", "data": base64.b64encode(wav).decode()})


def test_valid_wav_is_accepted():
    assert parse_client_message(audio_message(wav_bytes()))["wav"][:4] == b"RIFF"


@pytest.mark.parametrize("bad", [b"not a wav file at all, definitely not", wav_bytes(21)], ids=["not-wav", "21-seconds"])
def test_non_wav_or_too_long_is_rejected(bad):
    with pytest.raises(InvalidMessage):
        parse_client_message(audio_message(bad))


def test_large_frames_are_only_allowed_for_audio():
    with pytest.raises(InvalidMessage):
        parse_client_message(json.dumps({"type": "text", "text": "x" * 5000}))


def test_groq_whisper_reads_text_and_language():
    def handler(request: httpx.Request) -> httpx.Response:
        assert b"whisper-large-v3-turbo" in request.content and b"speech.wav" in request.content
        return httpx.Response(200, json={"text": " ปิดไฟให้หน่อย ", "language": "thai"})

    stt = GroqWhisper("k", "whisper-large-v3-turbo", transport=httpx.MockTransport(handler))
    assert asyncio.run(stt.transcribe(wav_bytes())) == Transcript("ปิดไฟให้หน่อย", "th-TH", "groq")


class Engine:
    def __init__(self, name, result):
        self.name, self.result = name, result

    async def transcribe(self, wav):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_falls_back_to_the_other_provider():
    ok = Transcript("turn off the light", "en-US", "gemini")
    stt = CloudSTT([Engine("groq", TranscriptionError("429")), Engine("gemini", ok)])
    assert asyncio.run(stt.transcribe(wav_bytes())) == ok


def test_no_keys_is_a_clear_error():
    with pytest.raises(TranscriptionError, match="API_KEY"):
        asyncio.run(CloudSTT([]).transcribe(wav_bytes()))


def test_azure_engine_reads_the_browser_wav():
    from nexus_voice.cloud_stt import AzureEngine
    from nexus_voice.speech import STTResult

    class FakeAzure:
        def transcribe(self, pcm):
            assert len(pcm) == 16000  # 0.5 s of 16-bit samples, header stripped
            return STTResult("ok", "ปิดไฟ", "th-TH", 0.9)

    assert asyncio.run(AzureEngine(FakeAzure()).transcribe(wav_bytes())) == Transcript("ปิดไฟ", "th-TH", "azure")
