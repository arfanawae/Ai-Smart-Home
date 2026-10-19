"""Voice Activity Detection: cut a continuous 30 ms frame stream into utterances (Blueprint §8.3).

Speech starts when enough recent frames are voiced, and ends after a run of silence. A short
pre-roll is kept so the first syllable isn't clipped (the built-in AMD mic gates quiet sound).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import webrtcvad

from .audio_io import SAMPLE_RATE, SAMPLE_WIDTH

FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_WIDTH  # 960


@dataclass(frozen=True)
class VadConfig:
    aggressiveness: int = 2  # webrtcvad 0 (lenient) … 3 (strict)
    start_window_frames: int = 10  # look at the last 300 ms…
    start_voiced_frames: int = 6  # …and start when ≥ 180 ms of it is speech
    # 600 ms (was 800): with streaming STT this wait is most of the delay after the user stops talking.
    # Shorter risks cutting people who pause mid-sentence; tune with VAD_END_SILENCE_MS on real voices.
    end_silence_ms: int = 600
    pre_roll_ms: int = 300
    max_utterance_ms: int = 15_000


class UtteranceSegmenter:
    def __init__(self, config: VadConfig = VadConfig(), is_speech: Callable[[bytes], bool] | None = None) -> None:
        self.config = config
        if is_speech is None:
            vad = webrtcvad.Vad(config.aggressiveness)
            is_speech = lambda frame: vad.is_speech(frame, SAMPLE_RATE)  # noqa: E731
        self._is_speech = is_speech
        self._pre_roll: deque[bytes] = deque(maxlen=config.pre_roll_ms // FRAME_MS)
        self._window: deque[bool] = deque(maxlen=config.start_window_frames)
        self._buffer: list[bytes] = []
        self._silence_frames = 0
        self.in_speech = False

    @property
    def hearing_voice(self) -> bool:
        """Any voiced frame in the start window — speech may be starting but is not confirmed yet."""
        return self.in_speech or any(self._window)

    def buffered(self) -> bytes:
        """Audio of the utterance so far (pre-roll included) — fed to streaming STT at speech start."""
        return b"".join(self._buffer)

    def reset(self) -> None:
        self._pre_roll.clear()
        self._window.clear()
        self._buffer = []
        self._silence_frames = 0
        self.in_speech = False

    def push(self, frame: bytes) -> bytes | None:
        """Feed one 30 ms frame. Returns the whole utterance (PCM) when it has just ended."""
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"VAD ต้องการเฟรมละ {FRAME_BYTES} ไบต์ ({FRAME_MS} ms) แต่ได้ {len(frame)}")
        speech = self._is_speech(frame)
        if not self.in_speech:
            self._pre_roll.append(frame)
            self._window.append(speech)
            if sum(self._window) >= self.config.start_voiced_frames:
                self.in_speech = True
                self._buffer = list(self._pre_roll)
                self._silence_frames = 0
            return None

        self._buffer.append(frame)
        self._silence_frames = 0 if speech else self._silence_frames + 1
        ended = self._silence_frames * FRAME_MS >= self.config.end_silence_ms
        too_long = len(self._buffer) * FRAME_MS >= self.config.max_utterance_ms
        if not (ended or too_long):
            return None
        utterance = b"".join(self._buffer)
        self.reset()
        return utterance


def frames(pcm: bytes) -> list[bytes]:
    """Split PCM into whole 30 ms frames (a trailing partial frame is dropped)."""
    return [pcm[i : i + FRAME_BYTES] for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)]
