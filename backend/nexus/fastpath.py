"""Hybrid fast path: handle *unambiguous* light commands and status questions without the LLM.

Design rule: when in doubt, defer to the LLM. A false "execute" (turning the light on when the user
said "อย่าเพิ่งเปิดไฟ" / "don't turn on the light yet") is far worse than spending one LLM request.
So instead of "keyword found → act", a sentence must pass every check, in this order:

1. short, single-clause (long sentences usually carry context)
2. no negation — checked first, so "ไฟไม่ได้เปิดอยู่ใช่ไหม" is never read as a status question
3. a status question about the main light → get_device_status (read-only, always safe)
4. a polite request form is reduced to the command ("ช่วยเปิดไฟได้ไหม", "can you turn off the light?")
5. no question, condition/time, hedging, reported-speech, state-description or other-device markers
6. exactly one action: on XOR off XOR absolute brightness XOR relative brightness
   (relative = clear direction words such as "หรี่ไฟหน่อย" / "dim the light"; *indirect* complaints
   such as "ไฟสว่างเกินไป" still go to the LLM, which can ask)

Thai and English have separate rule sets; the script of the text picks one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

MAX_LENGTH = 60
RELATIVE_STEP = 20  # one "a bit dimmer/brighter" step, same as the LLM policy (Blueprint §8.5)

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
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
    thai = len(re.findall(r"[฀-๿]", t))
    latin = len(re.findall(r"[a-z]", t))
    return _decide_en(t) if latin > thai else _decide_th(t)


# ── Thai ─────────────────────────────────────────────────────────────────────────────────────────
_TH_LIGHT = ("ไฟ", "หลอด", "ความสว่าง")
_TH_TARGET = r"(?:ไฟ|หลอดไฟ|หลอด)"
_TH_ON = re.compile(rf"เปิด\s*{_TH_TARGET}")
_TH_OFF = re.compile(rf"(?<!เ)ปิด\s*{_TH_TARGET}")  # "ปิด" is a substring of "เปิด"
_TH_ROOM = re.compile(r"ห้อง(?!หลัก)")
_TH_NEGATION = ("ไม่", "อย่า", "ห้าม", "เลิก", "หยุด")
_TH_STATUS = (
    re.compile(r"ไฟ\S{0,12}?(?:เปิด|ปิด)\s*(?:อยู่)?\s*(?:ไหม|มั้ย|หรือเปล่า|รึเปล่า|หรือยัง|รึยัง|ป่ะ)"),
    re.compile(r"(?:สว่าง|ความสว่าง)\S{0,8}?(?:เท่าไหร่|เท่าไร|กี่เปอร์เซ็นต์|กี่%)"),
    re.compile(r"สถานะ\S{0,6}ไฟ"),
)
_TH_POLITE = re.compile(r"\s*ได้\s*(?:ไหม|มั้ย|ป่ะ|หรือเปล่า|รึเปล่า)(?:\s*(?:ครับ|ค่ะ|คะ))?$")
# "ลองปิดไฟให้หน่อย" asks for the action (a polite "please"); "ลองเปิดไฟดู" alone stays hedging
_TH_TRY_REQUEST = re.compile(r"(?:งั้น\s*)?ลอง\s*(?=(?:ช่วย\s*)?(?:เปิด|ปิด|หรี่|ปรับ|ลด|เพิ่ม)\S*.*หน่อย)")
_TH_TIME = re.compile(r"อีก\s*\d|อีก\s*(?:สัก)?\s*(?:ครึ่ง)?\s*(?:นาที|ชั่วโมง|วินาที|วัน)")
_TH_BLOCKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("question", ("ไหม", "มั้ย", "หรือ", "รึ", "ป่ะ", "?", "อะไร", "ยังไง", "เท่าไร", "เท่าไหร่", "ทำไม")),
    ("condition_or_time", (
        "ถ้า", "เมื่อ", "ตอน", "นาที", "ชั่วโมง", "วินาที", "พรุ่งนี้", "คืนนี้", "เดี๋ยว",
        "ทีหลัง", "ค่อย", "หลังจาก", "ก่อน", "จนกว่า", "ตั้งเวลา", "ทุก",
    )),
    ("hedging", ("มั้ง", "ดีกว่า", "ลอง", "คิดว่า", "น่าจะ", "อาจ", "เผื่อ")),
    # not bare "ว่า": it is a substring of "สว่าง"
    ("reported_speech", ("บอก", "เขา", "สั่งให้")),
    ("state_description", ("แล้ว", "อยู่", "เคย", "ยัง")),
    ("other_device", ("แอร์", "พัดลม", "ทีวี", "ประตู", "ม่าน", "ปลั๊ก", "เพลง")),
)
_TH_DIM_WORDS = ("หรี่", "ความสว่าง", "สว่าง", "ปรับ", "ตั้ง")
_TH_DOWN = re.compile(r"หรี่|ลด(?:ความสว่าง|แสง|ไฟ)|มืดลง")
_TH_UP = re.compile(r"สว่างขึ้น|เพิ่ม(?:ความสว่าง|แสง)|เร่งไฟ")
_TH_ABSOLUTE = ("เหลือ", "เป็น", "ที่")
_TH_BY_DOWN = ("ลด", "ลง")
_TH_BY_UP = ("เพิ่ม", "ขึ้น")


def _decide_th(t: str) -> FastPathDecision:
    t = t.replace("หรือไม่", "ไหม")  # "...หรือไม่" is a question, not a negation
    if any(m in t for m in _TH_NEGATION):
        return _defer("negation")
    if not any(w in t for w in _TH_LIGHT):
        return _defer("not_about_light")
    if _TH_ROOM.search(t):
        return _defer("other_room")
    wants_on, wants_off = bool(_TH_ON.search(t)), bool(_TH_OFF.search(t))
    # "ทำไมไฟเปิดอยู่" asks for a reason, which a status read cannot answer
    if not (wants_on or wants_off) and "ทำไม" not in t and any(p.search(t) for p in _TH_STATUS):
        return _execute("get_device_status", "status_question")
    if wants_on or wants_off or _TH_DOWN.search(t) or _TH_UP.search(t):
        t = _TH_POLITE.sub("", t)  # "ช่วยเปิดไฟได้ไหม" is a request, not a question
        t = _TH_TRY_REQUEST.sub("", t)
    if _TH_TIME.search(t):
        return _defer("condition_or_time")
    for reason, markers in _TH_BLOCKERS:
        if any(m in t for m in markers):
            return _defer(reason)
    if wants_on and wants_off:
        return _defer("conflicting_actions")

    numbers = _NUMBER_RE.findall(t)
    down, up = bool(_TH_DOWN.search(t)), bool(_TH_UP.search(t))
    absolute = any(m in t for m in _TH_ABSOLUTE)
    if len(numbers) > 1:
        return _defer("multiple_numbers")
    if numbers:
        n = int(numbers[0])
        if wants_off:
            return _defer("off_with_number")
        # "ลดความสว่างลง 20" = by 20; "หรี่ไฟเหลือ 30" / "ปรับความสว่าง 50" = to that level
        lower, higher = any(w in t for w in _TH_BY_DOWN), any(w in t for w in _TH_BY_UP)
        if (lower or higher) and not absolute:
            if lower and higher:
                return _defer("conflicting_actions")
            return _execute("adjust_brightness", "relative_with_number", delta=-n if lower else n)
        if not (any(w in t for w in _TH_DIM_WORDS) or wants_on or down or up):
            return _defer("number_without_brightness_verb")
        # Out-of-range values are passed on on purpose: the validator rejects them with a clear message.
        return _execute("set_brightness", "explicit_brightness", level=n)
    if down and up:
        return _defer("conflicting_actions")
    if down:
        return _execute("adjust_brightness", "relative_down", delta=-RELATIVE_STEP)
    if up:
        return _execute("adjust_brightness", "relative_up", delta=RELATIVE_STEP)
    if any(w in t for w in _TH_DIM_WORDS):
        return _defer("brightness_without_number")  # "ไฟสว่างเกินไป" — indirect, the LLM can ask
    if wants_on:
        return _execute("turn_light_on", "explicit_on")
    if wants_off:
        return _execute("turn_light_off", "explicit_off")
    return _defer("no_explicit_action")


# ── English ──────────────────────────────────────────────────────────────────────────────────────
_EN_TARGET = r"(?:the\s+)?(?:main\s+)?(?:light|lights|lamp)"
_EN_LIGHT = re.compile(r"\b(?:light|lights|lamp|brightness)\b")
_EN_NEGATION = re.compile(r"\b(?:don'?t|do\s+not|not|never|stop|no\s+need|cancel)\b|n't\b")
_EN_STATUS = (
    re.compile(rf"\b(?:is|are)\s+{_EN_TARGET}\s+(?:on|off|still\s+on|still\s+off)\b"),
    re.compile(r"\bhow\s+bright\b|\bwhat(?:'s|\s+is)\s+the\s+brightness\b"),
    re.compile(r"\b(?:light|lamp)\s+status\b|\bstatus\s+of\s+" + _EN_TARGET),
)
_EN_WHY = re.compile(r"\b(?:why|how\s+come)\b")
_EN_POLITE = re.compile(r"^(?:can|could|would|will)\s+you\s+(?:please\s+)?|\bplease\b|\?$")
_EN_BLOCKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("question", re.compile(r"\?|^(?:is|are|do|does|did|can|could|would|will|should|what|why|how|when|where|who)\b")),
    ("condition_or_time", re.compile(
        r"\b(?:if|when|after|before|until|later|tomorrow|tonight|every|minutes?|hours?|seconds?)\b|\bin\s+\d"
    )),
    ("hedging", re.compile(r"\b(?:maybe|perhaps|might|probably|i\s+think|i\s+guess)\b")),
    ("reported_speech", re.compile(r"\b(?:said|says|told|wants?\s+me)\b")),
    ("other_device", re.compile(
        r"\b(?:fan|ac|aircon|air\s*con\w*|tv|television|door|curtains?|blinds|music|speaker|"
        r"bedroom|kitchen|bathroom|garage|living\s+room)\b"
    )),
)
_EN_ON = re.compile(rf"\b(?:turn|switch|put)\s+(?:on\s+{_EN_TARGET}|{_EN_TARGET}\s+on)\b|^(?:the\s+)?lights?\s+on$")
_EN_OFF = re.compile(rf"\b(?:turn|switch|put)\s+(?:off\s+{_EN_TARGET}|{_EN_TARGET}\s+off)\b|^(?:the\s+)?lights?\s+off$")
_EN_SET = re.compile(r"\b(?:set|dim|change|put|make|turn)\b.*\bto\s+(\d+)|\bbrightness\s+(?:to\s+|at\s+)?(\d+)")
_EN_DOWN = re.compile(r"\bdim\b|\bdarker\b|\bturn\s+(?:\w+\s+){0,3}?down\b|\bdecrease\b|\blower\b|\bless\s+light\b")
_EN_UP = re.compile(r"\bbrighten\b|\bbrighter\b|\bturn\s+(?:\w+\s+){0,3}?up\b|\bincrease\b|\bmore\s+light\b")
_EN_BY = re.compile(r"\bby\s+(\d+)")


def _decide_en(t: str) -> FastPathDecision:
    if _EN_NEGATION.search(t):
        return _defer("negation")
    if not _EN_LIGHT.search(t):
        return _defer("not_about_light")
    wants_on, wants_off = bool(_EN_ON.search(t)), bool(_EN_OFF.search(t))
    # "why is the light on?" asks for a reason, which a status read cannot answer
    if not (wants_on or wants_off) and not _EN_WHY.search(t) and any(p.search(t) for p in _EN_STATUS):
        return _execute("get_device_status", "status_question")
    t = _EN_POLITE.sub("", t).strip()  # "can you turn off the light?" is a request
    for reason, pattern in _EN_BLOCKERS:
        if pattern.search(t):
            return _defer(reason)
    if wants_on and wants_off:
        return _defer("conflicting_actions")

    numbers = _NUMBER_RE.findall(t)
    down, up = bool(_EN_DOWN.search(t)), bool(_EN_UP.search(t))
    if down and up:
        return _defer("conflicting_actions")
    if len(numbers) > 1:
        return _defer("multiple_numbers")
    if (by := _EN_BY.search(t)) and (down or up):
        n = int(by.group(1))
        return _execute("adjust_brightness", "relative_with_number", delta=-n if down else n)
    if (absolute := _EN_SET.search(t)) and not wants_off:
        return _execute("set_brightness", "explicit_brightness", level=int(absolute.group(1) or absolute.group(2)))
    if numbers:
        return _defer("number_without_brightness_verb")
    if wants_on:
        return _execute("turn_light_on", "explicit_on")
    if wants_off:
        return _execute("turn_light_off", "explicit_off")
    if down:
        return _execute("adjust_brightness", "relative_down", delta=-RELATIVE_STEP)
    if up:
        return _execute("adjust_brightness", "relative_up", delta=RELATIVE_STEP)
    return _defer("no_explicit_action")
