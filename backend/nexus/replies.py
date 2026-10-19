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


_ACTION_EN = {
    "turn_light_on": "turn the main light on",
    "turn_light_off": "turn the main light off",
    "set_brightness": "set the main light to {level}%",
    "adjust_brightness": "adjust the main light's brightness",
    "get_device_status": "check the main light",
}


def _state_en(result: CommandResult) -> str:
    state = result.reported
    if state is None:
        return ""
    if not state.online:
        return "The main light is offline."
    return f"The main light is on at {state.brightness}%." if state.on else "The main light is off."


def _level_th(result: CommandResult) -> str:
    """Brightness changes say the level the device reported; on/off needs no extra words."""
    if result.tool in ("set_brightness", "adjust_brightness") and result.reported and result.reported.on:
        return f" ตอนนี้ความสว่าง {result.reported.brightness}%"
    return ""


def _now_en(result: CommandResult) -> str:
    state = result.reported
    if state is None:
        return "Done."
    if not state.on:
        return "The main light is now off."
    if result.tool == "turn_light_on":
        return "The main light is now on."
    return f"The main light is now at {state.brightness}%."


# Spoken the moment a device command is about to be sent (two-step feedback). Always "in progress",
# never "done": the confirmation comes only from the device's reported state.
_ACK = {
    "th": {
        "turn_light_on": "ได้ครับ เดี๋ยวผมเปิดไฟให้ครับ",
        "turn_light_off": "ได้ครับ เดี๋ยวผมปิดไฟให้ครับ",
        "set_brightness": "ได้ครับ เดี๋ยวผมปรับความสว่างเป็น {level} เปอร์เซ็นต์ให้ครับ",
        "adjust_down": "ได้ครับ เดี๋ยวผมหรี่ไฟให้ครับ",
        "adjust_up": "ได้ครับ เดี๋ยวผมเพิ่มความสว่างให้ครับ",
    },
    "en": {
        "turn_light_on": "Got it, turning on the light.",
        "turn_light_off": "Got it, turning off the light.",
        "set_brightness": "Got it, setting the light to {level} percent.",
        "adjust_down": "Got it, dimming the light.",
        "adjust_up": "Got it, brightening the light.",
    },
}


def acknowledgement(tool: str, args: dict, language: Language) -> str | None:
    if tool == "adjust_brightness":
        key = "adjust_down" if int(args.get("delta", 0) or 0) < 0 else "adjust_up"
    else:
        key = tool
    template = _ACK[language].get(key)
    return template.format(level=args.get("level", "?")) if template else None


def describe_result(result: CommandResult, language: Language = "th") -> str:
    """Fast-path reply. Every success says whether it was simulated or confirmed by a real device."""
    if language == "en":
        return _describe_en(result)
    action = action_th(result)
    device = DEVICE_NAMES_TH.get(result.device_id or "", "อุปกรณ์")
    state = f" ตอนนี้{device}{result.reported.describe_th()}" if result.reported else ""
    match result.status:
        case CommandStatus.SUCCEEDED if result.tool == "get_device_status":
            if not result.reported:
                return "ไม่ทราบสถานะครับ"
            now = result.reported
            status = "ออฟไลน์" if not now.online else (f"เปิดอยู่ {now.brightness}%" if now.on else "ปิดอยู่")
            return f"ตอนนี้{device}{status}ครับ{' (จำลอง)' if result.simulated else ''}"
        case CommandStatus.SUCCEEDED if result.simulated:
            return f"จำลองการ{action}เรียบร้อยแล้วครับ{_level_th(result)} {SIMULATION_TAG}"
        case CommandStatus.SUCCEEDED:
            return f"{action}เรียบร้อยแล้วครับ{_level_th(result)}"
        case CommandStatus.REJECTED:
            return f"ทำคำสั่ง{action}ไม่ได้ครับ: {result.error}"
        case CommandStatus.TIMEOUT:
            return f"ส่งคำสั่ง{action}แล้ว แต่อุปกรณ์ไม่ตอบกลับครับ สถานะไฟตอนนี้ไม่แน่ชัด"
    return f"{action}ไม่สำเร็จครับ: {result.error}"


def _describe_en(result: CommandResult) -> str:
    action = _ACTION_EN.get(result.tool, result.tool).format(level=result.args.get("level", "?"))
    state = _state_en(result)
    match result.status:
        case CommandStatus.SUCCEEDED if result.tool == "get_device_status":
            return f"{state} (simulated)" if result.simulated and state else state or "I don't know its state."
        case CommandStatus.SUCCEEDED if result.simulated:
            return f"Simulated: {_now_en(result)} {MESSAGES['en']['simulation_tag']}"
        case CommandStatus.SUCCEEDED:
            return _now_en(result)
        case CommandStatus.REJECTED:
            if result.tool == "set_brightness":
                return "Brightness must be a whole number from 0 to 100 percent."
            return f"I can't {action} right now."
        case CommandStatus.TIMEOUT:
            return "I sent the command, but the device didn't answer, so the light's state is uncertain."
    return f"I couldn't {action}: the device reported a problem."


# System messages in the user's language. (The fast path only matches Thai, so describe_result stays Thai.)
MESSAGES: dict[Language, dict[str, str]] = {
    "th": {
        "simulation_tag": SIMULATION_TAG,
        "no_llm": "ตอนนี้ยังไม่ได้ตั้งค่า Gemini API จึงตอบได้เฉพาะคำสั่งที่ชัดเจนครับ "
        "เช่น \"เปิดไฟ\", \"ปิดไฟ\", \"หรี่ไฟเหลือ 30\"",
        "quota": "ตอนนี้โควตา AI ของวันนี้หมดแล้วครับ ยังสั่งไฟด้วยคำสั่งตรง ๆ ได้ เช่น \"เปิดไฟ\" — รีเซ็ตเวลา {reset} น.",
        "llm_failed": "ขอโทษครับ ตอนนี้ติดต่อ AI ไม่ได้ ({reason}) คำสั่งตรง ๆ เช่น \"เปิดไฟ\" ยังใช้ได้ครับ",
        "rate_limit": "ตอนนี้เรียก AI ถี่เกินไปครับ รออีกประมาณ {seconds} วินาทีแล้วลองใหม่ได้เลย คำสั่งตรง ๆ เช่น \"เปิดไฟ\" ยังใช้ได้ครับ",
        "too_many_steps": "ขอโทษครับ คำขอนี้ซับซ้อนเกินไป ลองพูดใหม่ให้สั้นลงได้ไหมครับ",
        "no_command": "(หมายเหตุจากระบบ: ในรอบนี้ยังไม่มีการส่งคำสั่งไปยังอุปกรณ์)",
        "command_failed": "(หมายเหตุจากระบบ: คำสั่งในรอบนี้ไม่สำเร็จ สถานะอุปกรณ์ไม่เปลี่ยนตามที่บอก)",
    },
    "en": {
        "simulation_tag": "(Simulation mode — no real device was controlled.)",
        "no_llm": "Gemini isn't configured yet, so I can only handle simple Thai commands like \"เปิดไฟ\" right now.",
        "quota": "My AI quota for today has run out. Simple commands like \"เปิดไฟ\" still work. It resets at {reset}.",
        "llm_failed": "Sorry, I can't reach the AI right now ({reason}). Simple commands like \"เปิดไฟ\" still work.",
        "rate_limit": "I'm getting too many AI requests right now. Try again in about {seconds} seconds. Simple commands like \"เปิดไฟ\" still work.",
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
