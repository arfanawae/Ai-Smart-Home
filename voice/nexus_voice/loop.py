"""The full NEXUS voice loop (Blueprint §8.3), wired in-process to the backend Assistant.

    IDLE ─ "Nexus" ─▶ WAKE_DETECTED ("ว่าไงครับ") ─▶ LISTENING ─ VAD ─▶ TRANSCRIBING ─▶ Assistant.handle()
      ▲                                                  ▲                     (fast path / Gemini → device)
      └── 60 s of silence / "ขอบคุณ Nexus" ───────────────┴──────── SPEAKING ◀── sentences → streaming TTS

Latency design (bench_latency.py):
  - STT starts when VAD hears speech, and audio is pushed while the user talks, so the transcript is
    ready ~0.1–0.3 s after the VAD decides the sentence has ended.
  - The assistant hands over speakable sentences as they are generated; each is synthesised as soon as
    it arrives (the next one while the current one plays) and played from its first audio chunk.

Half-duplex: while NEXUS speaks, microphone frames are discarded so it never hears itself.
`process_frame` holds all the logic and never touches audio hardware, so the loop can be driven by
recorded audio in tests (test_voice_loop.py) and benchmarks.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Protocol

from nexus.assistant import Assistant
from nexus.events import AIState, EventSink, StateChanged, UserMessage

from . import sounds
from .corrections import correct
from .speech import ENGLISH, THAI, STTResult
from .vad import UtteranceSegmenter
from .wakeword import WakeWordDetector

# Silence allowed after NEXUS answers before it goes back to waiting for "Nexus". It is extended every
# time the user starts speaking, so a real conversation never runs against a clock; it only ends the
# session (and stops sending audio to the cloud) once the room has been quiet for this long.
CONVERSATION_WINDOW_S = 60.0  # VOICE_SESSION_TIMEOUT_SEC in backend/.env
# After the speaker stops, the room still rings for a moment. Without this, the microphone heard that
# tail, VAD took it for speech, STT returned nothing, and "ไม่ได้ยิน" played again — an endless loop.
ECHO_TAIL_S = 0.4
# Two empty transcripts in a row (noise, a cough, the TV) end the session quietly.
MAX_EMPTY_TRANSCRIPTS = 2
LISTENING_HINT = "สามารถพูดต่อได้ทันที"
# Ends the session only when it is the whole utterance (after dropping "NEXUS", punctuation and polite
# particles), so "เปิดไฟหน่อย ขอบคุณครับ" or "stop the music" is still handled as a request.
STOP_PHRASES = frozenset({
    "ขอบคุณ", "ขอบคุณมาก", "แค่นี้แหละ", "แค่นี้ก่อน", "พอแล้ว", "บาย", "บายบาย",
    "stop", "bye", "bye bye", "goodbye", "exit", "thank you", "thanks", "that's all", "that is all",
})
_PUNCTUATION = re.compile(r"[.,!?;:\"()…\-]")
_THAI_GAP = re.compile(r"(?<=[฀-๿])\s+(?=[฀-๿])")  # STT may space Thai words
_POLITE_TAIL = re.compile(r"\s*(?:ครับผม|ครับ|คับ|ค่ะ|คะ|นะ|จ้ะ|จ้า)+$")
TURN_FAILED = {THAI: "ทำคำสั่งนี้ไม่สำเร็จครับ ลองพูดใหม่ได้เลยครับ", ENGLISH: "That didn't work. Please try again."}

log = logging.getLogger(__name__)


def is_departure(text: str) -> bool:
    phrase = " ".join(_PUNCTUATION.sub(" ", text.lower()).replace("nexus", " ").split())
    return _POLITE_TAIL.sub("", _THAI_GAP.sub("", phrase)).strip() in STOP_PHRASES

# Plays audio as it arrives (a whole prompt is just a one-chunk stream).
Player = Callable[[AsyncIterator[bytes]], Awaitable[None]]


class SpeechToText(Protocol):
    def transcribe(self, pcm: bytes) -> STTResult: ...


class StreamingSTT(Protocol):
    def start_stream(self) -> STTSession: ...


class STTSession(Protocol):
    def write(self, pcm: bytes) -> None: ...
    def finish(self) -> STTResult: ...
    def cancel(self) -> None: ...


class TextToSpeech(Protocol):
    def synthesize_stream(self, text: str, language: str | None = None) -> Iterator[bytes]: ...


async def _one_chunk(pcm: bytes) -> AsyncIterator[bytes]:
    yield pcm


class VoiceLoop:
    def __init__(
        self,
        assistant: Assistant,
        stt: SpeechToText,
        tts: TextToSpeech,
        detector: WakeWordDetector,
        play: Player,
        emit: EventSink,
        segmenter: UtteranceSegmenter | None = None,
        window_s: float = CONVERSATION_WINDOW_S,
        echo_tail_s: float = ECHO_TAIL_S,
    ) -> None:
        self.assistant = assistant
        self._stt, self._tts = stt, tts
        self._detector = detector
        self._segmenter = segmenter or UtteranceSegmenter()
        self._play = play
        self._emit = emit
        self._window_s = window_s
        self._echo_tail_s = echo_tail_s
        self._empty_transcripts = 0
        self.state = AIState.IDLE
        self.language = THAI  # language of the last thing the user said; used for canned prompts
        self._deadline = 0.0
        self._stt_session: STTSession | None = None
        self.speaking = False  # the mic reader drops frames while True (half-duplex)
        # +1 every time NEXUS finishes speaking. The mic reader compares it to flush audio it queued
        # before/while NEXUS spoke — that audio is stale and may contain the start of NEXUS's own voice.
        self.utterances_spoken = 0

    def _set(self, state: AIState, detail: str = "") -> None:
        self.state = state
        self._emit(StateChanged(state, detail))

    # ── speaking ────────────────────────────────────────────────────────────────────────────────
    async def _say(self, chunks: AsyncIterator[bytes]) -> None:
        """Play a stream. SPEAKING (and half-duplex) starts with the first real audio chunk."""
        started = False

        async def marked() -> AsyncIterator[bytes]:
            nonlocal started
            async for chunk in chunks:
                if not started:
                    started = True
                    self.speaking = True
                    self._set(AIState.SPEAKING)
                yield chunk

        try:
            await self._play(marked())
        finally:
            if started:
                await asyncio.sleep(self._echo_tail_s)  # still half-duplex while the room stops ringing
                self._detector.reset()
                self._segmenter.reset()
                self._drop_stt_session()
                self.utterances_spoken += 1
            self.speaking = False

    async def _say_prompt(self, name: str) -> None:
        await self._say(_one_chunk(sounds.load_prompt(name, self.language)))

    async def _speak_sentences(self, sentences: asyncio.Queue[str | None]) -> AsyncIterator[bytes]:
        """Audio for each sentence in order; every sentence is synthesised as soon as it arrives."""
        aio = asyncio.get_running_loop()
        order: asyncio.Queue[asyncio.Queue[bytes | Exception | None] | None] = asyncio.Queue()

        def synthesise(text: str, out: asyncio.Queue[bytes | Exception | None]) -> None:
            try:
                for chunk in self._tts.synthesize_stream(text):
                    aio.call_soon_threadsafe(out.put_nowait, chunk)
            except Exception as e:  # noqa: BLE001 — reported in the loop below
                aio.call_soon_threadsafe(out.put_nowait, e)
            finally:
                aio.call_soon_threadsafe(out.put_nowait, None)

        async def dispatch() -> None:
            while (text := await sentences.get()) is not None:
                out: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()
                order.put_nowait(out)
                aio.run_in_executor(None, synthesise, text, out)
            order.put_nowait(None)

        dispatcher = asyncio.create_task(dispatch())
        try:
            while (out := await order.get()) is not None:
                while (item := await out.get()) is not None:
                    if isinstance(item, Exception):
                        self._set(AIState.ERROR, f"พูดตอบไม่ได้: {item}")
                    else:
                        yield item
        finally:
            dispatcher.cancel()

    # ── listening ───────────────────────────────────────────────────────────────────────────────
    def start_listening(self) -> None:
        """Skip the wake word (benchmarks, push-to-talk)."""
        self._listen()

    def _listen(self) -> None:
        self._deadline = time.monotonic() + self._window_s
        self._empty_transcripts = 0
        self._set(AIState.LISTENING, LISTENING_HINT)

    def _sleep(self, detail: str = "") -> None:
        self._detector.reset()
        self._segmenter.reset()
        self._drop_stt_session()
        self._set(AIState.IDLE, detail or 'พูด "Nexus" เพื่อเรียก')

    def _drop_stt_session(self) -> None:
        if self._stt_session is not None:
            session, self._stt_session = self._stt_session, None
            asyncio.get_running_loop().run_in_executor(None, session.cancel)

    def _stream_to_stt(self, was_in_speech: bool, frame: bytes) -> None:
        """Feed the recogniser while the user is still talking (only if the STT supports streaming)."""
        start_stream = getattr(self._stt, "start_stream", None)
        if start_stream is None:
            return
        if not was_in_speech and self._segmenter.in_speech:  # speech just started: pre-roll + this frame
            self._drop_stt_session()
            self._stt_session = start_stream()
            self._stt_session.write(self._segmenter.buffered())
        elif was_in_speech and self._stt_session is not None:  # mid-utterance, or its very last frame
            self._stt_session.write(frame)

    async def process_frame(self, frame: bytes) -> None:
        if self.state == AIState.IDLE:
            if self._detector.process(frame):
                self._set(AIState.WAKE_DETECTED)
                await self._say_prompt(random.choice(("wake_1", "wake_2")))
                self._listen()
            return

        was_in_speech = self._segmenter.in_speech
        utterance = self._segmenter.push(frame)
        if not was_in_speech and self._segmenter.in_speech:
            self._deadline = time.monotonic() + self._window_s  # the user is talking: no time pressure
        self._stream_to_stt(was_in_speech, frame)
        if utterance is not None:
            await self._handle_utterance(utterance)
        # Not while VAD is still deciding: speech that began just before the deadline is not cut off.
        elif not self._segmenter.hearing_voice and time.monotonic() >= self._deadline:
            self._sleep("หมดเวลาคุยต่อ")

    async def _ignore(self, stt: STTResult) -> None:
        """Nothing usable was heard. Say nothing and do not restart the clock: noise must never keep a
        session alive or make NEXUS talk. Real errors are shown on screen, not spoken."""
        self._empty_transcripts += 1
        if stt.status == "error":
            self._set(AIState.ERROR, stt.error or "ถอดเสียงไม่ได้")
        if self._empty_transcripts >= MAX_EMPTY_TRANSCRIPTS:
            self._sleep()
        else:
            self._set(AIState.LISTENING, LISTENING_HINT)

    async def _transcribe(self, pcm: bytes) -> STTResult:
        if self._stt_session is not None:
            session, self._stt_session = self._stt_session, None
            return await asyncio.to_thread(session.finish)
        return await asyncio.to_thread(self._stt.transcribe, pcm)

    async def _handle_utterance(self, pcm: bytes) -> None:
        try:
            await self._run_utterance(pcm)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # one failed turn must never end the voice session or the process
            log.exception("voice turn failed")
            await self._recover(e)

    async def _recover(self, error: Exception) -> None:
        """Show what broke, say so briefly (if TTS still works), and keep listening."""
        self._drop_stt_session()
        self._set(AIState.ERROR, f"ทำคำสั่งไม่สำเร็จ ({type(error).__name__}: {error})"[:200])
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        sentences.put_nowait(TURN_FAILED.get(self.language, TURN_FAILED[THAI]))
        sentences.put_nowait(None)
        try:
            await self._say(self._speak_sentences(sentences))
        except Exception:
            log.exception("could not speak the error message")
        self._listen()

    async def _run_utterance(self, pcm: bytes) -> None:
        self._set(AIState.TRANSCRIBING, f"{len(pcm) / 32000:.1f} วินาที")
        stt = await self._transcribe(pcm)
        if stt.status != "ok" or not stt.text.strip():
            await self._ignore(stt)
            return

        self.language = stt.language or THAI
        text = correct(stt.text)
        self._emit(UserMessage(text, "voice", stt.language))
        if is_departure(text):
            await self._say_prompt("goodbye")
            self._sleep()
            return

        # Speak while the assistant is still working: sentences go to TTS the moment they exist.
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        speaking = asyncio.create_task(self._say(self._speak_sentences(sentences)))
        try:
            await self.assistant.handle(text, stt.language, on_speech=sentences.put_nowait)
        finally:
            sentences.put_nowait(None)
            await speaking
        self._listen()
