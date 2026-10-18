"""Hybrid fast path: handle only *unambiguous* light commands without calling the LLM.

Design rule: when in doubt, defer to the LLM. A false "execute" (turning the light on when the
user said "อย่าเพิ่งเปิดไฟ") is far worse than spending one LLM request.
So instead of "keyword found → act", a sentence must pass every check below:

1. short, single-clause command (long sentences usually carry context)
2. no negation, question, condition/time, hedging, reported-speech or state-description markers
3. talks about the main light only (no other room / appliance)
4. exactly one action (on XOR off XOR absolute brightness), with an explicit verb before "ไฟ"
5. brightness needs exactly one explicit number; relative changes ("ลดอีก 20") go to the LLM
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

MAX_LENGTH = 60

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

# Each group is checked as plain substrings on the normalized text. Order = reason reported.
_BLOCKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("negation", ("ไม่", "อย่า", "ห้าม", "เลิก", "หยุด")),
    ("question", ("ไหม", "มั้ย", "หรือ", "รึ", "ป่ะ", "?", "อะไร", "ยังไง", "เท่าไร", "เท่าไหร่", "ทำไม")),
    ("condition_or_time", (
        "ถ้า", "เมื่อ", "ตอน", "อีก", "นาที", "ชั่วโมง", "วินาที", "พรุ่งนี้", "คืนนี้", "เดี๋ยว",
        "ทีหลัง", "ค่อย", "หลังจาก", "ก่อน", "จนกว่า", "ตั้งเวลา", "ทุก",
    )),
    ("hedging", ("มั้ง", "ดีกว่า", "ลอง", "คิดว่า", "น่าจะ", "อาจ", "เผื่อ")),
    # not bare "ว่า": it is a substring of "สว่าง"
    ("reported_speech", ("บอก", "เขา", "สั่งให้")),
    ("state_description", ("แล้ว", "อยู่", "เคย", "ยัง")),
    ("other_device", ("แอร์", "พัดลม", "ทีวี", "ประตู", "ม่าน", "ปลั๊ก", "เพลง")),
)

_LIGHT_WORDS = ("ไฟ", "หลอด", "ความสว่าง")
_TARGET = r"(?:ไฟ|หลอดไฟ|หลอด)"
_ON_RE = re.compile(rf"เปิด\s*{_TARGET}")
_OFF_RE = re.compile(rf"(?<!เ)ปิด\s*{_TARGET}")  # "ปิด" is a substring of "เปิด"
_DIM_WORDS = ("หรี่", "ความสว่าง", "สว่าง", "ปรับ", "ตั้ง")
_RELATIVE_WORDS = ("ลด", "เพิ่ม", "ขึ้น", "ลง")
_ABSOLUTE_MARKERS = ("เหลือ", "เป็น", "ที่")
_ROOM_RE = re.compile(r"ห้อง(?!หลัก)")
_NUMBER_RE = re.compile(r"\d+")


@dataclass(frozen=True)
class FastPathDecision:
    action: Literal["execute", "defer"]
    reason: str
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)

    @property
    def execute(self) -> bool:
        return self.action == "execute"


def normalize(text: str) -> str:
    text = text.translate(_THAI_DIGITS).strip().lower()
    text = re.sub(r"\b(?:hey\s+)?nexus\b|เน็กซัส", " ", text)  # the wake word is not part of the command
    return re.sub(r"\s+", " ", text).strip(" .!,")


def _defer(reason: str) -> FastPathDecision:
    return FastPathDecision("defer", reason)


def _execute(tool: str, reason: str, **args: Any) -> FastPathDecision:
    return FastPathDecision("execute", reason, tool, {"device_id": "main_light", **args})


def decide(text: str) -> FastPathDecision:
    t = normalize(text)
    if not t:
        return _defer("empty")
    if len(t) > MAX_LENGTH:
        return _defer("too_long")
    for reason, markers in _BLOCKERS:
        if any(m in t for m in markers):
            return _defer(reason)
    if not any(w in t for w in _LIGHT_WORDS):
        return _defer("not_about_light")
    if _ROOM_RE.search(t):
        return _defer("other_room")

    wants_on = bool(_ON_RE.search(t))
    wants_off = bool(_OFF_RE.search(t))
    numbers = _NUMBER_RE.findall(t)
    relative_verb = any(w in t for w in _RELATIVE_WORDS)
    absolute_target = any(m in t for m in _ABSOLUTE_MARKERS)

    if wants_on and wants_off:
        return _defer("conflicting_actions")
    if relative_verb and not absolute_target:
        return _defer("relative_change")
    # "ลดไฟเหลือ 30" — a relative verb with an absolute target is a brightness command too
    mentions_dim = any(w in t for w in _DIM_WORDS) or relative_verb

    if numbers:
        if len(numbers) > 1:
            return _defer("multiple_numbers")
        if wants_off:
            return _defer("off_with_number")
        if not (mentions_dim or wants_on):
            return _defer("number_without_brightness_verb")
        # Out-of-range values are passed on on purpose: the validator rejects them with a clear message.
        return _execute("set_brightness", "explicit_brightness", level=int(numbers[0]))

    if mentions_dim:
        return _defer("brightness_without_number")
    if wants_on:
        return _execute("turn_light_on", "explicit_on")
    if wants_off:
        return _execute("turn_light_off", "explicit_off")
    return _defer("no_explicit_action")
