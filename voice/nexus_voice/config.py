"""Voice settings. Secrets live in the single backend/.env shared with the backend (git-ignored)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .speech import DEFAULT_VOICES, ENGLISH, THAI, LanguageMode
from .vad import VadConfig

REPO_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_DIR / "backend" / ".env"


@dataclass(frozen=True)
class VoiceSettings:
    azure_key: str | None
    azure_region: str | None
    stt_languages: tuple[str, ...]
    stt_language_mode: LanguageMode
    tts_voices: dict[str, str]
    input_device: int | None
    output_device: int | None
    vad_end_silence_ms: int
    conversation_window_s: float


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


def _env(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def load_settings() -> VoiceSettings:
    load_dotenv(ENV_FILE)
    languages = tuple(lang.strip() for lang in _env("STT_LANGUAGES", f"{THAI},{ENGLISH}").split(",") if lang.strip())
    # AZURE_TTS_VOICE is the pre-bilingual name of the Thai voice setting; still honoured.
    thai_voice = _env("AZURE_TTS_VOICE_TH", _env("AZURE_TTS_VOICE", DEFAULT_VOICES[THAI]))
    mode = _env("STT_LANGUAGE_MODE", "parallel")
    if mode not in ("parallel", "auto"):
        raise ValueError(f"STT_LANGUAGE_MODE={mode!r} ต้องเป็น parallel หรือ auto")
    return VoiceSettings(
        azure_key=os.getenv("AZURE_SPEECH_KEY", "").strip() or None,
        azure_region=os.getenv("AZURE_SPEECH_REGION", "").strip() or None,
        stt_languages=languages,
        stt_language_mode=mode,  # type: ignore[arg-type]
        tts_voices={THAI: thai_voice, ENGLISH: _env("AZURE_TTS_VOICE_EN", DEFAULT_VOICES[ENGLISH])},
        input_device=_optional_int("AUDIO_INPUT_DEVICE"),
        output_device=_optional_int("AUDIO_OUTPUT_DEVICE"),
        vad_end_silence_ms=_optional_int("VAD_END_SILENCE_MS") or VadConfig().end_silence_ms,
        conversation_window_s=float(_env("CONVERSATION_WINDOW_S", "15")),
    )
