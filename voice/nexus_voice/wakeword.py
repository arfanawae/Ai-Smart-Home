"""Wake word detection, fully on-device: no audio leaves the laptop before "NEXUS" is heard.

VoskWakeWord uses an offline English model restricted to a tiny grammar, so it can only output
"nexus", "hey nexus" or "[unk]". It needs no account or training. If its false-wake rate turns out
too high in real use, Porcupine (custom keyword, needs a Picovoice AccessKey) can implement the
same WakeWordDetector protocol.

Measured 2026-10-19 with Azure TTS clips (voice/test_wake.py selftest, 6 wake + 10 non-wake, TH/EN):
  trigger="partial"                → 6/6 wakes, but 7/10 false wakes ("Next time", "Netflix", "แน่นอน"…)
  trigger="final", conf ≥ 0.9      → 6/6 wakes, 0/10 false wakes (real "nexus" scored 1.0;
                                     "Next time" 0.66, "The next bus" 0.85)
"final" waits for Vosk to close the phrase, so say "Nexus" and pause briefly before the command.
Synthetic speech is cleaner than a real room: re-check the threshold with real voices.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal, Protocol

import vosk

from .audio_io import SAMPLE_RATE

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
DEFAULT_VOSK_MODEL = MODELS_DIR / "vosk-model-small-en-us-0.15"
KEYWORD = "nexus"


class WakeWordDetector(Protocol):
    name: str

    def process(self, frame: bytes) -> bool:
        """Feed one audio frame; True exactly once per detected wake word."""
        ...

    def reset(self) -> None: ...


class VoskWakeWord:
    name = "vosk"

    def __init__(
        self,
        model_path: Path = DEFAULT_VOSK_MODEL,
        trigger: Literal["partial", "final"] = "final",
        min_confidence: float = 0.9,
        cooldown_s: float = 1.5,
    ) -> None:
        if not model_path.exists():
            raise FileNotFoundError(
                f"ไม่พบโมเดล Vosk ที่ {model_path} — ดาวน์โหลด vosk-model-small-en-us-0.15 จาก "
                "https://alphacephei.com/vosk/models แล้วแตกไฟล์ไว้ใน voice/models/"
            )
        vosk.SetLogLevel(-1)
        self._model = vosk.Model(str(model_path))
        self._grammar = json.dumps(["hey nexus", "nexus", "[unk]"])
        self.trigger = trigger  # "partial" reacts sooner but has no confidence score → many false wakes
        self.min_confidence = min_confidence
        self._cooldown_s = cooldown_s
        self._last_wake = 0.0
        self.reset()

    def reset(self) -> None:
        self._recognizer = vosk.KaldiRecognizer(self._model, SAMPLE_RATE, self._grammar)
        self._recognizer.SetWords(True)  # per-word confidence in final results

    def _heard_keyword(self, frame: bytes) -> bool:
        if self._recognizer.AcceptWaveform(frame):
            words = json.loads(self._recognizer.Result()).get("result", [])
            return any(w["word"] == KEYWORD and w.get("conf", 0.0) >= self.min_confidence for w in words)
        if self.trigger == "partial":
            return KEYWORD in json.loads(self._recognizer.PartialResult()).get("partial", "").split()
        return False

    def process(self, frame: bytes) -> bool:
        if not self._heard_keyword(frame):
            return False
        self.reset()  # start fresh so the same utterance can't fire twice
        now = time.monotonic()
        if now - self._last_wake < self._cooldown_s:
            return False
        self._last_wake = now
        return True
