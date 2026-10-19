"""Pre-rendered prompts, played instantly (no API call) — e.g. right after the wake word.

Thai files stay at voice/sounds/<name>.wav (unchanged location); English ones live in voice/sounds/en/.
A missing English file falls back to Thai, so the wake response never fails silently.
Regenerate with: python voice/test_speech.py prompts
"""

from __future__ import annotations

from pathlib import Path

from . import audio_io
from .speech import ENGLISH, THAI

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


def load_prompt(name: str, language: str = THAI) -> bytes:
    path = prompt_path(name, language)
    if not path.exists() and language != THAI:
        path = prompt_path(name, THAI)
    return audio_io.load_wav(path)
