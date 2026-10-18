"""Which language is the user writing/speaking in? Same rule as voice/nexus_voice/speech.detect_language."""

from __future__ import annotations

import re
from typing import Literal

Language = Literal["th", "en"]

_THAI_LETTER = re.compile(r"[ก-ฮะ-ฺเ-๎]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> Language:
    """Majority script wins; ties and text without letters count as Thai (the household's main language)."""
    thai = len(_THAI_LETTER.findall(text))
    latin = len(_LATIN_LETTER.findall(text))
    return "en" if latin > thai else "th"
