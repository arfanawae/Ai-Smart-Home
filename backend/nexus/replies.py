"""Deterministic Thai replies for the fast path and for system messages (no LLM involved)."""

from __future__ import annotations

import re

from .devices.models import DEVICE_NAMES_TH, CommandResult, CommandStatus
from .language import Language

SIMULATION_TAG = "(โหมดจำลอง — ยังไม่ได้สั่งอุปกรณ์จริง)"

_ACTION_TH = {
    "turn_light_on": "เปิด{device}",
    "turn_light_off": "ปิด{device}",
    "set_brightness": "ตั้งความสว่าง{device}เป็น {level}%",
    "adjust_brightness": "ปรับความสว่าง{device}",
    "get_device_status": "ตรวจสถานะ{device}",
}


def action_th(result: CommandResult) -> str:
    template = _ACTION_TH.get(result.tool, result.tool)
    device = DEVICE_NAMES_TH.get(result.device_id or "", "อุปกรณ์")
    return template.format(device=device, level=result.args.get("level", "?"))


def describe_result(result: CommandResult) -> str:
    action = action_th(result)
    state = f" ตอนนี้{DEVICE_NAMES_TH.get(result.device_id or '', 'อุปกรณ์')}{result.reported.describe_th()}" if result.reported else ""
    match result.status:
        case CommandStatus.SUCCEEDED if result.tool == "get_device_status":
            prefix = "จำลอง: " if result.simulated else ""
            return f"{prefix}{state.strip()}ครับ" if state else "ไม่ทราบสถานะครับ"
        case CommandStatus.SUCCEEDED if result.simulated:
            return f"จำลองการ{action}แล้วครับ{state} {SIMULATION_TAG}"
        case CommandStatus.SUCCEEDED:
            return f"{action}แล้วครับ (ยืนยันจากอุปกรณ์){state}"
        case CommandStatus.REJECTED:
            return f"ทำคำสั่ง{action}ไม่ได้ครับ: {result.error}"
        case CommandStatus.TIMEOUT:
            return f"ส่งคำสั่ง{action}แล้ว แต่อุปกรณ์ไม่ตอบกลับครับ สถานะไฟตอนนี้ไม่แน่ชัด"
    return f"{action}ไม่สำเร็จครับ: {result.error}"


# System messages in the user's language. (The fast path only matches Thai, so describe_result stays Thai.)
MESSAGES: dict[Language, dict[str, str]] = {
    "th": {
        "simulation_tag": SIMULATION_TAG,
        "no_llm": "ตอนนี้ยังไม่ได้ตั้งค่า Gemini API จึงตอบได้เฉพาะคำสั่งที่ชัดเจนครับ "
        "เช่น \"เปิดไฟ\", \"ปิดไฟ\", \"หรี่ไฟเหลือ 30\"",
        "quota": "ตอนนี้โควตา AI ของวันนี้หมดแล้วครับ ยังสั่งไฟด้วยคำสั่งตรง ๆ ได้ เช่น \"เปิดไฟ\" — รีเซ็ตเวลา {reset} น.",
        "llm_failed": "ขอโทษครับ ตอนนี้ติดต่อ AI ไม่ได้ ({reason}) คำสั่งตรง ๆ เช่น \"เปิดไฟ\" ยังใช้ได้ครับ",
        "too_many_steps": "ขอโทษครับ คำขอนี้ซับซ้อนเกินไป ลองพูดใหม่ให้สั้นลงได้ไหมครับ",
        "no_command": "(หมายเหตุจากระบบ: ในรอบนี้ยังไม่มีการส่งคำสั่งไปยังอุปกรณ์)",
        "command_failed": "(หมายเหตุจากระบบ: คำสั่งในรอบนี้ไม่สำเร็จ สถานะอุปกรณ์ไม่เปลี่ยนตามที่บอก)",
    },
    "en": {
        "simulation_tag": "(Simulation mode — no real device was controlled.)",
        "no_llm": "Gemini isn't configured yet, so I can only handle simple Thai commands like \"เปิดไฟ\" right now.",
        "quota": "My AI quota for today has run out. Simple commands like \"เปิดไฟ\" still work. It resets at {reset}.",
        "llm_failed": "Sorry, I can't reach the AI right now ({reason}). Simple commands like \"เปิดไฟ\" still work.",
        "too_many_steps": "Sorry, that request was too complex. Could you say it more simply?",
        "no_command": "(System note: no command was sent to any device this turn.)",
        "command_failed": "(System note: the command failed — the device did not change as described.)",
    },
}

# Spoken stand-ins for the bracketed notes above. A truth-critical note is never dropped from speech:
# if the LLM said "เปิดไฟแล้ว" but nothing was sent, staying silent would make NEXUS say something false.
_SPOKEN: dict[Language, dict[str, str]] = {
    "th": {
        "simulation": "นี่เป็นการจำลองนะครับ",
        "no_command": "แต่จริง ๆ ยังไม่ได้ส่งคำสั่งไปที่อุปกรณ์นะครับ",
        "command_failed": "แต่คำสั่งไม่สำเร็จนะครับ",
    },
    "en": {
        "simulation": "This was only a simulation.",
        "no_command": "But note that no command was actually sent.",
        "command_failed": "But the command did not go through.",
    },
}
_MARKDOWN = str.maketrans("", "", "*#`_~>")


def to_speech(text: str) -> str:
    """The full reply stays in the log; this is what NEXUS says out loud."""
    spoken = text
    for language, messages in MESSAGES.items():
        words = _SPOKEN[language]
        for key in ("no_command", "command_failed"):
            spoken = spoken.replace(messages[key], f" {words[key]}")
        if messages["simulation_tag"] in spoken:
            already_said = "จำลอง" in spoken.replace(messages["simulation_tag"], "") or "simulat" in spoken.lower().replace(
                messages["simulation_tag"].lower(), ""
            )
            spoken = spoken.replace(messages["simulation_tag"], "" if already_said else f" {words['simulation']}")
    spoken = spoken.translate(_MARKDOWN)
    return re.sub(r"\s+", " ", spoken).strip()


NO_LLM = MESSAGES["th"]["no_llm"]
QUOTA_EXHAUSTED = MESSAGES["th"]["quota"]
LLM_FAILED = MESSAGES["th"]["llm_failed"]
TOO_MANY_STEPS = MESSAGES["th"]["too_many_steps"]
NO_COMMAND_NOTE = MESSAGES["th"]["no_command"]
COMMAND_FAILED_NOTE = MESSAGES["th"]["command_failed"]
