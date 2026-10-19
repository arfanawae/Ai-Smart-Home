"""STT / TTS provider interfaces + Azure AI Speech implementation (bilingual: Thai + English).

Audio is captured by our own code (audio_io) and pushed to Azure, so the wake word and VAD share
the same microphone stream. SDK calls block; the voice loop runs them in worker threads.

STT picks th-TH or en-US per utterance (switching between utterances is seamless; mid-sentence is not):
    "parallel" (default) — recognise in every language at once, keep the most confident result.
    "auto"               — Azure AutoDetectSourceLanguageConfig (at-start language identification).
  Measured 2026-10-19 (test_speech.py roundtrip): "auto" labelled 5/8 short Thai commands as en-US
  and returned gibberish ("ปิดไฟห้องหลัก" → "Bitfi, Hongla."). "parallel" chose correctly 8/8
  (right language 0.60–0.96 vs wrong 0.04–0.22). It bills STT audio once per language.

Latency (bench_latency.py, 2026-10-19):
  - STTStream starts recognising while the user is still speaking, so the result arrives
    0.05–0.25 s after the audio ends instead of ~1.6 s for a one-shot request afterwards.
  - Synthesizers are kept open per voice and audio is streamed: first chunk in 0.14–0.28 s
    instead of 1.3–1.4 s for a fresh connection waiting for the whole sentence.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import azure.cognitiveservices.speech as speechsdk

from .audio_io import SAMPLE_RATE

Status = Literal["ok", "no_match", "error"]
LanguageMode = Literal["parallel", "auto"]

THAI, ENGLISH = "th-TH", "en-US"
DEFAULT_VOICES: Mapping[str, str] = {THAI: "th-TH-NiwatNeural", ENGLISH: "en-US-AndrewNeural"}
TTS_CHUNK_BYTES = 3200  # 100 ms of 16 kHz int16 audio

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
    # fast-path vocabulary (v2): recognised more reliably → fewer turns fall through to the LLM
    "หรี่ไฟหน่อย",
    "ไฟเปิดอยู่ไหม",
    "สว่างเท่าไหร่",
    "turn on the light",
    "turn off the light",
    "dim the light",
)

_THAI_LETTER = re.compile(r"[ก-ฮะ-ฺเ-๎]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")


def script_counts(text: str) -> tuple[int, int]:
    return len(_THAI_LETTER.findall(text)), len(_LATIN_LETTER.findall(text))


def detect_language(text: str) -> str:
    """Pick the TTS language from the text itself. Thai wins ties, so "สวัสดี NEXUS" stays Thai."""
    thai, latin = script_counts(text)
    return THAI if thai >= latin and thai > 0 else ENGLISH if latin else THAI


# Characters a voice can actually pronounce: Thai, Latin, digits, and plain punctuation.
_UNSPEAKABLE = re.compile(r"[^฀-๿A-Za-z0-9\s.,!?%:;'\"/+\-]")


def speakable(text: str, language: str) -> str:
    """Clean text right before TTS: no emoji/symbols/brackets; the English voice gets no Thai script."""
    text = text.replace("—", ", ").replace("–", ", ").replace("…", ". ")
    text = _UNSPEAKABLE.sub(" ", text)
    if language == ENGLISH:
        text = re.sub(r"[฀-๿]", " ", text)
    text = re.sub(r"\s+([,.!?])", r"\1", re.sub(r"\s+", " ", text))
    return text.strip(" ,")


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


class TTSError(RuntimeError):
    pass


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
# gibberish for Thai it cannot match ("ขอบคุณ" → en "Kopkun." 0.04–0.06). Measured on 2026-10-19;
# re-check against real voices before tuning.
PRIMARY_MARGIN = 0.3  # another language must beat the primary (first) language by this much
SECONDARY_MIN_CONFIDENCE = 0.3  # below this a non-primary result is treated as gibberish
# The th-TH model writes English speech partly in Latin letters ("Hey NEXUS" → "ให้ naxis"): a Thai
# result that is mostly Latin is weak evidence for Thai, so its confidence is scaled by its Thai share.


def thai_script_share(text: str) -> float:
    thai, latin = script_counts(text)
    return 1.0 if thai + latin == 0 else thai / (thai + latin)


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
        confidence = r.confidence or 0.0
        if r.language == THAI:
            confidence *= thai_script_share(r.text)
        return confidence + (margin if r.language == primary else 0.0)

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


def _result_to_stt(result: speechsdk.SpeechRecognitionResult, language: str | None) -> STTResult:
    match result.reason:
        case speechsdk.ResultReason.RecognizedSpeech:
            detected = language or speechsdk.AutoDetectSourceLanguageResult(result).language
            return STTResult("ok", result.text.strip(), detected, _confidence(result.json))
        case speechsdk.ResultReason.NoMatch:
            return STTResult("no_match", language=language, error="ไม่ได้ยินคำพูดที่ถอดเป็นข้อความได้")
    return STTResult("error", language=language, error=_cancel_message(result.cancellation_details))


@dataclass
class _Recognition:
    stream: speechsdk.audio.PushAudioInputStream
    future: speechsdk.ResultFuture
    language: str | None
    # Must stay referenced: if the recognizer is garbage-collected while its stream is open,
    # its destructor waits for a result that can never arrive — the whole thread hangs.
    recognizer: speechsdk.SpeechRecognizer


class STTStream:
    """Recognition that runs while the user is still talking. write() never blocks: audio is buffered
    until the recognizers have connected (set up in a background thread)."""

    def __init__(self, speech: AzureSpeech) -> None:
        self._speech = speech
        self._lock = threading.Lock()
        self._pending: list[bytes] = []
        self._sessions: list[_Recognition] = []
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._started = time.monotonic()
        threading.Thread(target=self._connect, daemon=True).start()

    def _connect(self) -> None:
        try:
            languages: list[str | None] = (
                [None] if self._speech.language_mode == "auto" and len(self._speech.languages) > 1 else list(self._speech.languages)
            )
            sessions = [self._speech._start_recognition(language) for language in languages]
            with self._lock:
                self._sessions = sessions
                for chunk in self._pending:
                    for session in sessions:
                        session.stream.write(chunk)
                self._pending.clear()
        except Exception as e:  # noqa: BLE001 — reported by finish()
            self._error = e
        finally:
            self._ready.set()

    def write(self, pcm: bytes) -> None:
        with self._lock:
            if not self._sessions:
                self._pending.append(pcm)
                return
            for session in self._sessions:
                session.stream.write(pcm)

    def finish(self) -> STTResult:
        """Blocking: end the audio and wait for the result(s)."""
        self._ready.wait(10)
        if self._error is not None or not self._sessions:
            return STTResult("error", error=f"เชื่อมต่อ Azure STT ไม่ได้: {self._error}", latency_ms=_ms_since(self._started))
        for session in self._sessions:
            session.stream.close()
        results = [_result_to_stt(s.future.get(), s.language) for s in self._sessions]
        result = results[0] if len(results) == 1 else pick_most_confident(results, primary=self._speech.languages[0])
        return STTResult(result.status, result.text, result.language, result.confidence, result.error, _ms_since(self._started))

    def cancel(self) -> None:
        """A false start (noise): close the streams and drop the results."""
        self._ready.wait(10)
        for session in self._sessions:
            session.stream.close()


class AzureSpeech:
    """Implements both STTProvider and TTSProvider with one Azure Speech resource."""

    def __init__(
        self,
        key: str,
        region: str,
        languages: Sequence[str] = (THAI, ENGLISH),
        voices: Mapping[str, str] = DEFAULT_VOICES,
        language_mode: LanguageMode = "parallel",
        segmentation_silence_ms: int | None = None,
    ) -> None:
        if not languages:
            raise ValueError("ต้องมีอย่างน้อย 1 ภาษา")
        self._key, self._region = key, region
        self.languages = tuple(languages)
        self.voices = dict(voices)
        self.language_mode = language_mode
        # recognize_once returns only the FIRST segment: if Azure ended a segment on a shorter pause than
        # our VAD does, the rest of a sentence with a mid-sentence pause would be silently dropped.
        # So Azure's segmentation silence is set to the VAD's end-of-speech silence.
        self.segmentation_silence_ms = segmentation_silence_ms
        self._synthesizers: dict[str, tuple[speechsdk.SpeechSynthesizer, threading.Lock]] = {}
        self._synth_guard = threading.Lock()

    def _config(self) -> speechsdk.SpeechConfig:
        config = speechsdk.SpeechConfig(subscription=self._key, region=self._region)
        config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm)
        return config

    def voice_for(self, language: str) -> str:
        return self.voices.get(language) or self.voices[self.languages[0]]

    # ── STT ────────────────────────────────────────────────────────────────────────────────────
    def _start_recognition(self, language: str | None) -> _Recognition:
        """One recognizer on a push stream, already connected and listening. None = auto-detect."""
        config = self._config()
        config.output_format = speechsdk.OutputFormat.Detailed  # for NBest confidence
        if self.segmentation_silence_ms:
            config.set_property(
                speechsdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, str(self.segmentation_silence_ms)
            )
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
        speechsdk.Connection.from_recognizer(recognizer).open(False)
        return _Recognition(stream, recognizer.recognize_once_async(), language, recognizer)

    def start_stream(self) -> STTStream:
        return STTStream(self)

    def transcribe(self, pcm: bytes) -> STTResult:
        """One-shot recognition of a finished utterance (tests, roundtrip checks)."""
        stream = self.start_stream()
        stream.write(pcm)
        return stream.finish()

    # ── TTS ────────────────────────────────────────────────────────────────────────────────────
    def _synthesizer(self, voice: str) -> tuple[speechsdk.SpeechSynthesizer, threading.Lock]:
        """One open synthesizer per voice, reused: saves the connection setup on every sentence."""
        with self._synth_guard:
            if voice not in self._synthesizers:
                config = self._config()
                config.speech_synthesis_voice_name = voice
                synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
                speechsdk.Connection.from_speech_synthesizer(synthesizer).open(True)
                self._synthesizers[voice] = (synthesizer, threading.Lock())
            return self._synthesizers[voice]

    def warm_up(self) -> None:
        """Open the TTS connections ahead of the first reply."""
        for language in self.languages:
            self._synthesizer(self.voice_for(language))

    def synthesize_stream(self, text: str, language: str | None = None) -> Iterator[bytes]:
        """Yield audio as Azure produces it. Raises TTSError if Azure cancels the request."""
        language = language or detect_language(text)
        clean = speakable(text, language)
        if not clean:
            return
        synthesizer, lock = self._synthesizer(self.voice_for(language))
        with lock:  # one request at a time per synthesizer
            result = synthesizer.start_speaking_text_async(clean).get()
            if result.reason == speechsdk.ResultReason.Canceled:
                raise TTSError(_cancel_message(result.cancellation_details))
            stream = speechsdk.AudioDataStream(result)
            while True:
                # A fresh buffer every read: the SDK fills this `bytes` object in place, and a full-length
                # slice of it is the same object — reusing one buffer made every yielded chunk alias the last.
                buffer = bytes(TTS_CHUNK_BYTES)
                filled = stream.read_data(buffer)
                if filled == 0:
                    break
                yield buffer if filled == TTS_CHUNK_BYTES else buffer[:filled]
            if stream.status == speechsdk.StreamStatus.Canceled:
                raise TTSError(_cancel_message(stream.cancellation_details))

    def synthesize(self, text: str, language: str | None = None) -> TTSResult:
        start = time.monotonic()
        language = language or detect_language(text)
        voice = self.voice_for(language)
        try:
            pcm = b"".join(self.synthesize_stream(text, language))
        except TTSError as e:
            return TTSResult("error", voice=voice, error=str(e), latency_ms=_ms_since(start))
        return TTSResult("ok", pcm, voice, latency_ms=_ms_since(start), characters=len(text))

    def list_voices(self, locale: str) -> list[str]:
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=self._config(), audio_config=None)
        result = synthesizer.get_voices_async(locale=locale).get()
        return [f"{v.short_name} ({v.gender.name})" for v in result.voices]


__all__ = [
    "DEFAULT_VOICES",
    "ENGLISH",
    "THAI",
    "AzureSpeech",
    "STTProvider",
    "STTResult",
    "STTStream",
    "TTSError",
    "TTSProvider",
    "TTSResult",
    "detect_language",
    "pick_most_confident",
    "speakable",
]
