"""Runtime settings loaded from backend/.env (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "gemini-flash-lite-latest"


PROVIDERS = ("gemini", "groq")
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_GROQ_STT_MODEL = "whisper-large-v3-turbo"


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    llm_model: str
    llm_daily_limit: int | None
    llm_timeout_s: float
    voice_provider: str  # "gemini" | "groq": the LLM (and browser-voice STT) tried first; the other is the fallback
    groq_api_key: str | None
    groq_model: str
    groq_stt_model: str
    device_mode: str
    device_backend: str  # "mcp": devices via the MCP server subprocess · "local": in-process
    unoq_url: str | None  # DEVICE_MODE=unoq: the NEXUS relay the board polls, e.g. http://127.0.0.1:8770
    unoq_token: str | None
    data_dir: Path


def _optional_int(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw else None


def _device_backend() -> str:
    backend = os.getenv("DEVICE_BACKEND", "mcp").strip() or "mcp"
    if backend not in ("mcp", "local"):
        raise ValueError(f"DEVICE_BACKEND={backend!r} ต้องเป็น mcp หรือ local")
    return backend


def load_settings() -> Settings:
    load_dotenv(BACKEND_DIR / ".env")
    device_mode = os.getenv("DEVICE_MODE", "mock").strip() or "mock"
    if device_mode not in ("mock", "unoq"):
        raise ValueError(f"DEVICE_MODE={device_mode!r} ต้องเป็น mock หรือ unoq")
    voice_provider = (os.getenv("VOICE_PROVIDER", "gemini").strip() or "gemini").lower()
    if voice_provider not in PROVIDERS:
        raise ValueError(f"VOICE_PROVIDER={voice_provider!r} ต้องเป็น gemini หรือ groq")
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip() or None,
        llm_model=os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL,
        llm_daily_limit=_optional_int(os.getenv("LLM_DAILY_LIMIT")),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "20") or 20),
        voice_provider=voice_provider,
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip() or None,
        groq_model=os.getenv("GROQ_MODEL", "").strip() or DEFAULT_GROQ_MODEL,
        groq_stt_model=os.getenv("GROQ_STT_MODEL", "").strip() or DEFAULT_GROQ_STT_MODEL,
        device_mode=device_mode,
        device_backend=_device_backend(),
        unoq_url=os.getenv("UNOQ_URL", "").strip() or None,
        unoq_token=os.getenv("UNOQ_TOKEN", "").strip() or None,
        data_dir=BACKEND_DIR / ".nexus",
    )
