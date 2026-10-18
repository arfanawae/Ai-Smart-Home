"""Deterministic Thai replies for the fast path and for system messages (no LLM involved)."""

from __future__ import annotations

from .devices.models import DEVICE_NAMES_TH, CommandResult, CommandStatus

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


NO_LLM = (
    "ตอนนี้ยังไม่ได้ตั้งค่า Gemini API จึงตอบได้เฉพาะคำสั่งที่ชัดเจนครับ "
    "เช่น \"เปิดไฟ\", \"ปิดไฟ\", \"หรี่ไฟเหลือ 30\""
)
QUOTA_EXHAUSTED = "ตอนนี้โควตา AI ของวันนี้หมดแล้วครับ ยังสั่งไฟด้วยคำสั่งตรง ๆ ได้ เช่น \"เปิดไฟ\" — รีเซ็ตเวลา {reset} น."
LLM_FAILED = "ขอโทษครับ ตอนนี้ติดต่อ AI ไม่ได้ ({reason}) คำสั่งตรง ๆ เช่น \"เปิดไฟ\" ยังใช้ได้ครับ"
TOO_MANY_STEPS = "ขอโทษครับ คำขอนี้ซับซ้อนเกินไป ลองพูดใหม่ให้สั้นลงได้ไหมครับ"
NO_COMMAND_NOTE = "(หมายเหตุจากระบบ: ในรอบนี้ยังไม่มีการส่งคำสั่งไปยังอุปกรณ์)"
COMMAND_FAILED_NOTE = "(หมายเหตุจากระบบ: คำสั่งในรอบนี้ไม่สำเร็จ สถานะอุปกรณ์ไม่เปลี่ยนตามที่บอก)"
