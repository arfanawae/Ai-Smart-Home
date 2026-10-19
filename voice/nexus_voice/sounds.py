"""Pre-rendered prompts, played instantly (no API call) — e.g. right after the wake word.

Thai files stay at voice/sounds/<name>.wav (unchanged location); English ones live in voice/sounds/en/.
A missing English file falls back to Thai, so the wake response never fails silently.
Regenerate with: python voice/test_speech.py prompts
"""

from __future__ import annotations

import array
from functools import lru_cache
from pathlib import Path

from . import audio_io
from .audio_io import SAMPLE_RATE
from .speech import ENGLISH, THAI

# Azure TTS pads its output with up to 1.2 s of silence at the end. The microphone stays muted while a
# prompt plays, so that silence delayed listening after "Nexus" by more than a second.
SILENCE_LEVEL = 300  # 16-bit amplitude below which a sample counts as silence
EDGE_PAD_S = 0.05

SOUNDS_DIR = Path(__file__).resolve().parents[1] / "sounds"

PROMPTS: dict[str, dict[str, str]] = {
    THAI: {
        "wake_1": "ว่าไงครับ",
        "wake_2": "ครับผม",
        "wake_3": "ครับ มีอะไรให้ช่วยครับ",
        "goodbye": "ได้ครับ ถ้าต้องการอะไร เรียก NEXUS ได้เลยครับ",
        "llm_quota": "ตอนนี้โควตา AI ของวันนี้หมดแล้วครับ แต่ยังสั่งเปิดปิดไฟได้ตามปกติครับ",
        "offline": "ตอนนี้ผมเชื่อมต่ออินเทอร์เน็ตไม่ได้ครับ",
    },
    ENGLISH: {
        "wake_1": "Yes?",
        "wake_2": "I'm listening.",
        "wake_3": "How can I help?",
        "goodbye": "Okay. Just call NEXUS if you need anything.",
        "llm_quota": "My AI quota for today has run out, but you can still turn the light on and off.",
        "offline": "I can't connect to the internet right now.",
    },
}


def prompt_path(name: str, language: str = THAI) -> Path:
    if language == THAI:
        return SOUNDS_DIR / f"{name}.wav"
    return SOUNDS_DIR / language.split("-")[0] / f"{name}.wav"


def trim_silence(pcm: bytes) -> bytes:
    samples = array.array("h", pcm[: len(pcm) // 2 * 2])
    loud = [i for i, v in enumerate(samples) if abs(v) > SILENCE_LEVEL]
    if not loud:
        return pcm
    pad = int(EDGE_PAD_S * SAMPLE_RATE)
    return samples[max(0, loud[0] - pad) : loud[-1] + pad].tobytes()


@lru_cache(maxsize=32)
def load_prompt(name: str, language: str = THAI) -> bytes:
    path = prompt_path(name, language)
    if not path.exists() and language != THAI:
        path = prompt_path(name, THAI)
    return trim_silence(audio_io.load_wav(path))
