"""STT / TTS provider interfaces + Azure AI Speech implementation (bilingual: Thai + English).

Audio is captured by our own code (audio_io) and pushed to Azure, so the wake word and VAD
can share the same microphone stream later. Calls are blocking; wrap with asyncio.to_thread
when used from the async service.

- STT picks th-TH or en-US per utterance (switching between utterances is seamless; switching
  mid-sentence is not). Two modes:
    "parallel" (default) — recognise in every language at once, keep the most confident result.
    "auto"               — Azure AutoDetectSourceLanguageConfig (at-start language identification).
  Measured 2026-10-19 (voice/test_speech.py roundtrip): "auto" labelled 5/8 short Thai commands as
  en-US and returned gibberish ("ปิดไฟห้องหลัก" → "Bitfi, Hongla."), with or without the phrase list.
  "parallel" chose correctly 8/8 with wide margins (right language 0.60–0.96 vs wrong 0.04–0.22).
  Cost: it bills STT audio once per language (2× with Thai + English); latency is unchanged.
- TTS: the voice is chosen from the script of the text being spoken (see detect_language).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal, Protocol

import azure.cognitiveservices.speech as speechsdk

from .audio_io import SAMPLE_RATE

Status = Literal["ok", "no_match", "error"]
LanguageMode = Literal["parallel", "auto"]

THAI, ENGLISH = "th-TH", "en-US"
DEFAULT_VOICES: Mapping[str, str] = {THAI: "th-TH-NiwatNeural", ENGLISH: "en-US-AndrewNeural"}

# Boosts recognition of the system's own vocabulary. Without it the live test heard
# "NEXUS" as "mexis", "หรี่ไฟ" as "รีไฟล์" and "ห้องหลัก" as "ห้องละ".
PHRASE_LIST = (
    "NEXUS",
    "สวัสดี NEXUS",
    "ไฟห้องหลัก",
    "เปิดไฟห้องหลัก",
    "ปิดไฟห้องหลัก",
    "หรี่ไฟ",
    "หรี่ไฟเหลือ",
    "ความสว่าง",
    "เปอร์เซ็นต์",
    "Hey NEXUS",
    "main light",
    "brightness",
)

_THAI_LETTER = re.compile(r"[ก-ฮะ-ฺเ-๎]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> str:
    """Pick the TTS language from the text itself. Thai wins ties, so "สวัสดี NEXUS" stays Thai."""
    thai = len(_THAI_LETTER.findall(text))
    latin = len(_LATIN_LETTER.findall(text))
    return THAI if thai >= latin and thai > 0 else ENGLISH if latin else THAI


@dataclass(frozen=True)
class STTResult:
    status: Status
    text: str = ""
    language: str | None = None  # detected locale, e.g. "th-TH"
    confidence: float | None = None  # 0–1 from Azure's detailed output
    error: str | None = None
    latency_ms: int = 0


@dataclass(frozen=True)
class TTSResult:
    status: Status
    pcm: bytes = b""  # 16 kHz mono int16, ready for audio_io.play
    voice: str = ""
    error: str | None = None
    latency_ms: int = 0
    characters: int = 0  # Azure bills TTS by character (free tier: 0.5M/month)


class STTProvider(Protocol):
    def transcribe(self, pcm: bytes) -> STTResult: ...


class TTSProvider(Protocol):
    def synthesize(self, text: str, language: str | None = None) -> TTSResult: ...


def _ms_since(start: float) -> int:
    return round((time.monotonic() - start) * 1000)


def _confidence(result_json: str) -> float | None:
    try:
        return float(json.loads(result_json)["NBest"][0]["Confidence"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None


# Confidence is not fully comparable across languages: the en-US model can be confidently wrong on
# short Thai speech ("วันนี้ร้อนจัง" → en "One neuron." 0.77 vs th 0.53), and returns near-zero
# gibberish when th-TH hears nothing ("ขอบคุณ" → en "Kopkun." 0.04). Measured on 2026-10-19;
# re-check against real voices before tuning.
PRIMARY_MARGIN = 0.3  # another language must beat the primary (first) language by this much
SECONDARY_MIN_CONFIDENCE = 0.3  # below this a non-primary result is treated as gibberish


def pick_most_confident(
    results: Sequence[STTResult],
    primary: str | None = None,
    margin: float = PRIMARY_MARGIN,
    secondary_min: float = SECONDARY_MIN_CONFIDENCE,
) -> STTResult:
    """Parallel mode: best biased confidence wins; otherwise no_match beats error."""

    def usable(r: STTResult) -> bool:
        return r.status == "ok" and (r.language == primary or (r.confidence or 0.0) >= secondary_min)

    def score(r: STTResult) -> float:
        return (r.confidence or 0.0) + (margin if r.language == primary else 0.0)

    recognized = [r for r in results if usable(r)]
    if recognized:
        return max(recognized, key=score)
    rejected = next((r for r in results if r.status == "ok"), None)
    if rejected:
        return STTResult("no_match", language=rejected.language, error="ไม่แน่ใจว่าได้ยินอะไร (ความมั่นใจต่ำ)")
    return next((r for r in results if r.status == "no_match"), results[0])


def _cancel_message(details: speechsdk.CancellationDetails | speechsdk.SpeechSynthesisCancellationDetails) -> str:
    message = f"{details.reason}: {details.error_details or ''}".strip(": ")
    if "401" in message or "Authentication" in message:
        return "Azure Speech key หรือ region ไม่ถูกต้อง — ตรวจ AZURE_SPEECH_KEY / AZURE_SPEECH_REGION"
    if "429" in message or "quota" in message.lower():
        return "โควตา Azure Speech หมดหรือเรียกถี่เกินไป"
    return message


class AzureSpeech:
    """Implements both STTProvider and TTSProvider with one Azure Speech resource."""

    def __init__(
        self,
        key: str,
        region: str,
        languages: Sequence[str] = (THAI, ENGLISH),
        voices: Mapping[str, str] = DEFAULT_VOICES,
        language_mode: LanguageMode = "parallel",
    ) -> None:
        if not languages:
            raise ValueError("ต้องมีอย่างน้อย 1 ภาษา")
        self._key, self._region = key, region
        self.languages = tuple(languages)
        self.voices = dict(voices)
        self.language_mode = language_mode

    def _config(self) -> speechsdk.SpeechConfig:
        config = speechsdk.SpeechConfig(subscription=self._key, region=self._region)
        config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm)
        return config

    def voice_for(self, language: str) -> str:
        return self.voices.get(language) or self.voices[self.languages[0]]

    def transcribe(self, pcm: bytes) -> STTResult:
        start = time.monotonic()
        if len(self.languages) == 1:
            result = self._recognize(pcm, self.languages[0])
        elif self.language_mode == "auto":
            result = self._recognize(pcm, None)
        else:
            with ThreadPoolExecutor(len(self.languages)) as pool:
                results = list(pool.map(lambda lang: self._recognize(pcm, lang), self.languages))
            result = pick_most_confident(results, primary=self.languages[0])
        return STTResult(result.status, result.text, result.language, result.confidence, result.error, _ms_since(start))

    def _recognize(self, pcm: bytes, language: str | None) -> STTResult:
        """One recognition pass; language=None uses Azure's auto-detection over self.languages."""
        config = self._config()
        config.output_format = speechsdk.OutputFormat.Detailed  # for NBest confidence
        stream_format = speechsdk.audio.AudioStreamFormat(samples_per_second=SAMPLE_RATE, bits_per_sample=16, channels=1)
        stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=stream)
        if language is None:
            auto_detect = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(languages=list(self.languages))
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=config, auto_detect_source_language_config=auto_detect, audio_config=audio_config
            )
        else:
            config.speech_recognition_language = language
            recognizer = speechsdk.SpeechRecognizer(speech_config=config, audio_config=audio_config)
        phrases = speechsdk.PhraseListGrammar.from_recognizer(recognizer)
        for phrase in PHRASE_LIST:
            phrases.addPhrase(phrase)
        stream.write(pcm)
        stream.close()
        result = recognizer.recognize_once_async().get()
        match result.reason:
            case speechsdk.ResultReason.RecognizedSpeech:
                detected = language or speechsdk.AutoDetectSourceLanguageResult(result).language
                return STTResult("ok", result.text.strip(), detected, _confidence(result.json))
            case speechsdk.ResultReason.NoMatch:
                return STTResult("no_match", language=language, error="ไม่ได้ยินคำพูดที่ถอดเป็นข้อความได้")
        return STTResult("error", language=language, error=_cancel_message(result.cancellation_details))

    def synthesize(self, text: str, language: str | None = None) -> TTSResult:
        start = time.monotonic()
        voice = self.voice_for(language or detect_language(text))
        config = self._config()
        config.speech_synthesis_voice_name = voice
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        result = synthesizer.speak_text_async(text).get()
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            return TTSResult("ok", bytes(result.audio_data), voice, latency_ms=_ms_since(start), characters=len(text))
        return TTSResult("error", voice=voice, error=_cancel_message(result.cancellation_details), latency_ms=_ms_since(start))

    def list_voices(self, locale: str) -> list[str]:
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=self._config(), audio_config=None)
        result = synthesizer.get_voices_async(locale=locale).get()
        return [f"{v.short_name} ({v.gender.name})" for v in result.voices]
