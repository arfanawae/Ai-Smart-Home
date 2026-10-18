"""The full NEXUS voice loop (Blueprint §8.3), wired in-process to the backend Assistant.

    IDLE ─ "Nexus" ─▶ WAKE_DETECTED ("ว่าไงครับ") ─▶ LISTENING ─ VAD ─▶ TRANSCRIBING ─▶ Assistant.handle()
      ▲                                                  ▲                     (fast path / Gemini → device)
      └── 8 s of silence / "ขอบคุณ Nexus" ───────────────┴──────── SPEAKING ◀── to_speech(reply) → TTS

Half-duplex: while NEXUS speaks, microphone frames are discarded so it never hears itself.
`process_frame` holds all the logic and never touches the audio hardware, so the loop can be
driven by recorded audio in tests (see test_voice_loop.py and `python -m nexus_voice simulate`).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

from nexus.assistant import Assistant
from nexus.events import AIState, EventSink, StateChanged
from nexus.replies import to_speech

from . import sounds
from .corrections import correct
from .speech import THAI, STTProvider, TTSProvider
from .vad import UtteranceSegmenter
from .wakeword import WakeWordDetector

CONVERSATION_WINDOW_S = 8.0
STOP_PHRASES = ("ขอบคุณ nexus", "แค่นี้แหละ", "แค่นี้ก่อน", "thank you nexus", "that's all", "goodbye")

Player = Callable[[bytes], Awaitable[None]]


class VoiceLoop:
    def __init__(
        self,
        assistant: Assistant,
        stt: STTProvider,
        tts: TTSProvider,
        detector: WakeWordDetector,
        play: Player,
        emit: EventSink,
        segmenter: UtteranceSegmenter | None = None,
        window_s: float = CONVERSATION_WINDOW_S,
        on_heard: Callable[[str, str | None], None] | None = None,
    ) -> None:
        self.assistant = assistant
        self._stt, self._tts = stt, tts
        self._detector = detector
        self._segmenter = segmenter or UtteranceSegmenter()
        self._play = play
        self._emit = emit
        self._window_s = window_s
        self._on_heard = on_heard
        self.state = AIState.IDLE
        self.language = THAI  # language of the last thing the user said; used for canned prompts
        self._deadline = 0.0
        self.speaking = False  # the mic reader drops frames while True (half-duplex)

    def _set(self, state: AIState, detail: str = "") -> None:
        self.state = state
        self._emit(StateChanged(state, detail))

    async def _say(self, pcm: bytes) -> None:
        self.speaking = True
        self._set(AIState.SPEAKING)
        try:
            await self._play(pcm)
        finally:
            self.speaking = False
            self._detector.reset()
            self._segmenter.reset()

    def _listen(self) -> None:
        self._deadline = time.monotonic() + self._window_s
        self._set(AIState.LISTENING, f"คุยต่อได้ {self._window_s:g} วินาทีโดยไม่ต้องเรียกชื่อ")

    def _sleep(self, detail: str = "") -> None:
        self._detector.reset()
        self._segmenter.reset()
        self._set(AIState.IDLE, detail or 'พูด "Nexus" เพื่อเรียก')

    async def process_frame(self, frame: bytes) -> None:
        if self.state == AIState.IDLE:
            if self._detector.process(frame):
                self._set(AIState.WAKE_DETECTED)
                await self._say(sounds.load_prompt(random.choice(("wake_1", "wake_2")), self.language))
                self._listen()
            return

        utterance = self._segmenter.push(frame)
        if utterance is not None:
            await self._handle_utterance(utterance)
        elif not self._segmenter.in_speech and time.monotonic() >= self._deadline:
            self._sleep("หมดเวลาคุยต่อ")

    async def _handle_utterance(self, pcm: bytes) -> None:
        self._set(AIState.TRANSCRIBING, f"{len(pcm) / 32000:.1f} วินาที")
        stt = await asyncio.to_thread(self._stt.transcribe, pcm)
        if stt.status != "ok":
            self._set(AIState.ERROR, stt.error or "ถอดเสียงไม่ได้")
            await self._say(sounds.load_prompt("not_heard", self.language))
            self._listen()
            return

        self.language = stt.language or THAI
        text = correct(stt.text)
        if self._on_heard:
            self._on_heard(text, stt.language)
        if any(phrase in text.lower() for phrase in STOP_PHRASES):
            await self._say(sounds.load_prompt("goodbye", self.language))
            self._sleep()
            return

        turn = await self.assistant.handle(text)  # emits THINKING/EXECUTING/… and the full reply
        spoken = to_speech(turn.reply)
        tts = await asyncio.to_thread(self._tts.synthesize, spoken)
        if tts.status == "ok":
            await self._say(tts.pcm)
        else:
            self._set(AIState.ERROR, f"พูดตอบไม่ได้: {tts.error}")
        self._listen()
