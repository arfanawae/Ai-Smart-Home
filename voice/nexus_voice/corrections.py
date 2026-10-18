"""Fix known Azure th-TH mistranscriptions of NEXUS's own vocabulary, before the text reaches the assistant.

Only patterns that make no sense in this domain are corrected, and each rule comes from an observed
error (voice/test_speech.py roundtrip). Add rules with a test, never speculatively.

Bilingual safety: the Thai rules only match Thai script, so English transcriptions pass through
untouched; the only rule that touches Latin text normalises misheard forms of the name "NEXUS".
"""

from __future__ import annotations

import re

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # "NEXUS" → "mexis" / "nexis" / Thai transliterations
    (re.compile(r"\b(?:[mn]e[kx]+[iu]s+|nexus)\b", re.IGNORECASE), "NEXUS"),
    (re.compile(r"เน็?กซ์?ซัส|เน็กซัส|เนกซัส"), "NEXUS"),
    # "หรี่ไฟ" → "รีไฟ" / "รีไฟล์" ("รีไฟ" is not a Thai word; "ไฟล์" = file never fits a light command)
    (re.compile(r"(?<!ห)รีไฟ(?:ล์)?"), "หรี่ไฟ"),
    # "ไฟห้องหลัก" → "ไฟห้องละ" at the end of a command
    (re.compile(r"ไฟห้องละ(?=\s*(?:ครับ|ค่ะ|คะ|นะ|หน่อย|ด้วย)?\s*$)"), "ไฟห้องหลัก"),
)


def correct(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text.strip()
