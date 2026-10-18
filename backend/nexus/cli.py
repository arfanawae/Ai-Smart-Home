"""Terminal chat for M1 step 1.1 — type Thai text, watch AI states and simulated device results."""

from __future__ import annotations

import asyncio
import os
import sys

from .assistant import Assistant
from .config import Settings, load_settings
from .devices.drivers.mock import FAILURE_MODES, MockDriver
from .devices.service import DeviceService
from .events import STATE_LABELS_TH, AIState, AssistantReply, CommandUpdate, Event, EventSink, StateChanged, UserMessage
from .llm.base import LLMProvider
from .quota import QuotaTracker
from .replies import action_th

DIM, CYAN, GREEN, YELLOW, RED, BOLD, RESET = (
    "\033[2m", "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[1m", "\033[0m",
)
_STATE_COLOR = {AIState.SUCCESS: GREEN, AIState.ERROR: RED, AIState.WAITING_FOR_DEVICE: YELLOW}
_STATUS_TH = {
    "succeeded": (GREEN, "สำเร็จ"),
    "failed": (RED, "ล้มเหลว"),
    "timeout": (YELLOW, "ไม่ตอบกลับ"),
    "rejected": (RED, "ถูกปฏิเสธ"),
}

HELP = f"""{DIM}คำสั่งพิเศษ:
  /status            ดูสถานะไฟ (จำลอง)
  /mock <โหมด>       จำลองความผิดพลาด: {' | '.join(FAILURE_MODES)}
  /quota             ดูการใช้โควตา Gemini วันนี้
  /reset             ล้างประวัติบทสนทนา
  /help              แสดงคำสั่งนี้
  /quit              ออก{RESET}"""


def print_event(event: Event) -> None:
    match event:
        case StateChanged(state=AIState.IDLE):
            return
        case StateChanged(state=state, detail=detail):
            color = _STATE_COLOR.get(state, DIM)
            extra = f" · {detail}" if detail else ""
            print(f"  {color}◌ {state.value} — {STATE_LABELS_TH[state]}{extra}{RESET}")
        case CommandUpdate(result=r, route=route):
            color, status = _STATUS_TH[r.status.value]
            if r.simulated and r.ok:
                status = "จำลองสำเร็จ"
            mock = f" {YELLOW}[MOCK]{RESET}" if r.simulated else ""
            state = f" · ไฟ: {r.reported.describe_th()}" if r.reported else ""
            print(
                f"  ⚙ {action_th(r)} ({r.tool} {r.args}) via {route} → "
                f"{color}{status}{RESET}{mock} {r.latency_ms}ms{state}"
            )
        case AssistantReply(text=text, route=route):
            tag = {"fastpath": "fast path", "llm": "Gemini", "system": "ระบบ"}[route]
            print(f"{CYAN}{BOLD}NEXUS{RESET} {DIM}({tag}){RESET} {text}")
        case UserMessage(text=text, source="voice", language=language):
            print(f"{BOLD}คุณ 🎙{RESET} {DIM}[{language}]{RESET} {text}")
        case UserMessage():
            return  # typed text is already on screen


def build_assistant(settings: Settings, emit: EventSink = print_event) -> tuple[Assistant, MockDriver]:
    driver = MockDriver()
    llm: LLMProvider | None = None
    if settings.gemini_api_key:
        from .llm.gemini import GeminiProvider

        llm = GeminiProvider(settings.gemini_api_key, settings.llm_model, settings.llm_timeout_s)
    quota = QuotaTracker(settings.data_dir / "llm_usage.json", settings.llm_daily_limit)
    return Assistant(DeviceService(driver), llm, quota, emit), driver


async def chat() -> None:
    settings = load_settings()
    assistant, driver = build_assistant(settings)
    llm = f"Gemini · {settings.llm_model}" if assistant.llm else f"{YELLOW}ยังไม่ได้ตั้งค่า (Fast path อย่างเดียว){RESET}"
    print(f"{CYAN}{BOLD}N E X U S{RESET}  {DIM}M1 · ขั้น 1.1{RESET}")
    print(f"  อุปกรณ์: {YELLOW}โหมดจำลอง (MOCK){RESET} — ยังไม่ได้เชื่อมต่ออุปกรณ์จริง")
    print(f"  LLM: {llm}")
    print(f"  โควตา: {assistant.quota.summary_th()}")
    print(HELP)

    while True:
        try:
            text = (await asyncio.to_thread(input, f"\n{BOLD}คุณ ›{RESET} ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.startswith("/"):
            command, _, arg = text[1:].partition(" ")
            match command:
                case "quit" | "exit":
                    break
                case "help":
                    print(HELP)
                case "reset":
                    assistant.reset()
                    print(f"{DIM}ล้างประวัติบทสนทนาแล้ว{RESET}")
                case "quota":
                    print(f"{DIM}{assistant.quota.summary_th()}{RESET}")
                case "status":
                    result = await assistant.devices.execute("get_device_status", {"device_id": "main_light"})
                    print_event(CommandUpdate(result, "manual"))
                case "mock" if arg.strip() in FAILURE_MODES:
                    driver.failure = arg.strip()  # type: ignore[assignment]
                    print(f"{YELLOW}จำลองความผิดพลาด: {driver.failure}{RESET}")
                case _:
                    print(f"{RED}ไม่รู้จักคำสั่ง{RESET}\n{HELP}")
            continue
        await assistant.handle(text)
        if assistant.quota.near_limit:
            print(f"{YELLOW}  ⚠ {assistant.quota.summary_th()}{RESET}")
    print(f"{DIM}ลาก่อนครับ{RESET}")


async def list_models() -> None:
    settings = load_settings()
    if not settings.gemini_api_key:
        print("ยังไม่ได้ใส่ GEMINI_API_KEY ใน backend/.env")
        return
    from .llm.gemini import GeminiProvider

    provider = GeminiProvider(settings.gemini_api_key, settings.llm_model)
    for name in await provider.list_models():
        marker = " ← LLM_MODEL" if name == settings.llm_model else ""
        print(f"{name}{marker}")


def main() -> None:
    if sys.platform == "win32":
        os.system("")  # enable ANSI colours in the Windows console
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]  # Thai over pipes on Windows
    command = sys.argv[1] if len(sys.argv) > 1 else "chat"
    if command == "models":
        asyncio.run(list_models())
    elif command == "chat":
        asyncio.run(chat())
    else:
        print("ใช้งาน: python -m nexus [chat|models]")
