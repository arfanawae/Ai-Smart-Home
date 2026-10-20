"""Big terminal banner naming the AI provider that handles each request (Gemini / Groq / fallback).

Plain ASCII so it reads the same in any console; coloured when the terminal supports ANSI.
LOG_PROVIDER_BANNER=0 in backend/.env turns it off.
"""

from __future__ import annotations

import os
import sys

WIDTH = 64
_COLOURS = {"gemini": "\033[36m", "groq": "\033[35m", "fallback": "\033[33m", "none": "\033[90m"}
BOLD, RESET = "\033[1m", "\033[0m"


def enabled() -> bool:
    return os.getenv("LOG_PROVIDER_BANNER", "1").strip().lower() not in ("0", "false", "no", "off")


def render(title: str, detail: str = "") -> list[str]:
    rule = "=" * WIDTH
    lines = [rule, f"=== {title} ===".center(WIDTH)]
    if detail:
        lines.append(detail.center(WIDTH))
    lines.append(rule)
    return lines


def provider_banner(provider: str | None, model: str | None = None, route: str = "", *, kind: str | None = None) -> None:
    """Print e.g. '=== ACTIVE PROVIDER: GROQ ===' with the model and how the request is handled."""
    if not enabled():
        return
    name = (provider or "none").upper()
    title = f"ACTIVE PROVIDER: {name}" if kind != "fallback" else f"FALLBACK -> {name}"
    detail = " | ".join(part for part in (model or "", route) if part)
    colour = _COLOURS.get(kind or (provider or "none"), "")
    use_colour = sys.stdout.isatty() and colour
    out = "\n".join(render(title, detail))
    print(f"\n{BOLD}{colour}{out}{RESET}" if use_colour else f"\n{out}", flush=True)
