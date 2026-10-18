"""Runtime settings loaded from backend/.env (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "gemini-flash-lite-latest"


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    llm_model: str
    llm_daily_limit: int | None
    llm_timeout_s: float
    device_mode: str
    data_dir: Path


def _optional_int(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw else None


def load_settings() -> Settings:
    load_dotenv(BACKEND_DIR / ".env")
    device_mode = os.getenv("DEVICE_MODE", "mock").strip() or "mock"
    if device_mode != "mock":
        raise ValueError(f"DEVICE_MODE={device_mode!r} ยังไม่รองรับในขั้นนี้ (รองรับเฉพาะ mock)")
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip() or None,
        llm_model=os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL,
        llm_daily_limit=_optional_int(os.getenv("LLM_DAILY_LIMIT")),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "20") or 20),
        device_mode=device_mode,
        data_dir=BACKEND_DIR / ".nexus",
    )
