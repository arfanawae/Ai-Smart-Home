import asyncio

from nexus.assistant import Assistant
from nexus.devices.drivers.mock import MockDriver
from nexus.devices.service import DeviceService
from nexus.events import AIState, StateChanged
from nexus.llm.base import LLMError, LLMResponse, ToolCall
from nexus.quota import QuotaTracker
from nexus.replies import COMMAND_FAILED_NOTE, NO_COMMAND_NOTE, SIMULATION_TAG


class ScriptedLLM:
    """Returns pre-scripted responses and records what it was sent."""

    provider = "fake"
    model = "fake"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, system, messages, tools):
        self.calls.append(list(messages))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(llm=None, tmp_path=None, daily_limit=None):
    events = []
    quota = QuotaTracker(tmp_path / "usage.json", daily_limit)
    driver = MockDriver(latency_s=(0, 0))
    assistant = Assistant(DeviceService(driver, timeout_s=0.05), llm, quota, events.append)
    return assistant, events, driver


def states(events):
    return [e.state for e in events if isinstance(e, StateChanged)]


def test_fast_path_does_not_use_llm(tmp_path):
    llm = ScriptedLLM()
    assistant, events, _ = make(llm, tmp_path)
    turn = asyncio.run(assistant.handle("ปิดไฟห้องหลัก"))
    assert turn.route == "fastpath"
    assert llm.calls == []
    assert "จำลอง" in turn.reply
    assert states(events) == [AIState.EXECUTING, AIState.WAITING_FOR_DEVICE, AIState.SUCCESS, AIState.IDLE]


def test_negation_goes_to_llm_and_does_not_touch_device(tmp_path):
    llm = ScriptedLLM(LLMResponse("รับทราบครับ จะยังไม่เปิดไฟนะครับ", []))
    assistant, _, driver = make(llm, tmp_path)
    turn = asyncio.run(assistant.handle("อย่าเพิ่งเปิดไฟ"))
    assert turn.route == "llm" and turn.fastpath_reason == "negation"
    assert turn.commands == []
    assert not asyncio.run(driver.get_state("main_light")).on


def test_llm_tool_call_runs_through_device_service(tmp_path):
    llm = ScriptedLLM(
        LLMResponse("", [ToolCall("c1", "adjust_brightness", {"device_id": "main_light", "delta": 20})]),
        LLMResponse("เพิ่มความสว่างเป็น 20% แล้วครับ", []),
    )
    assistant, _, _ = make(llm, tmp_path)
    turn = asyncio.run(assistant.handle("มืดจัง"))
    assert [c.tool for c in turn.commands] == ["adjust_brightness"]
    assert turn.commands[0].reported.brightness == 20
    # the tool result is sent back to the LLM, flagged as a simulation
    tool_message = llm.calls[1][-1]
    assert tool_message.role == "tool" and tool_message.tool_results[0].payload["simulated"] is True
    # LLM forgot to say it was simulated → the guard adds it
    assert turn.reply.endswith(SIMULATION_TAG)


def test_success_claim_without_a_command_is_flagged(tmp_path):
    llm = ScriptedLLM(LLMResponse("เปิดไฟให้เรียบร้อยแล้วครับ", []))
    assistant, _, _ = make(llm, tmp_path)
    turn = asyncio.run(assistant.handle("ช่วยดูเรื่องไฟให้หน่อยได้ไหม"))
    assert NO_COMMAND_NOTE in turn.reply


def test_admitted_failure_is_not_flagged(tmp_path):
    # Real Gemini reply from the first live test: "ไม่สำเร็จ" must not count as a success claim
    llm = ScriptedLLM(
        LLMResponse("", [ToolCall("c1", "adjust_brightness", {"device_id": "main_light", "delta": 20})]),
        LLMResponse("ขออภัยครับ การจำลองหรี่ไฟครั้งนี้ไม่สำเร็จเนื่องจากอุปกรณ์จำลองเกิดข้อผิดพลาด", []),
    )
    assistant, _, driver = make(llm, tmp_path)
    driver.failure = "error"
    reply = asyncio.run(assistant.handle("ช่วยหรี่ไฟลงอีกหน่อย")).reply
    assert NO_COMMAND_NOTE not in reply and COMMAND_FAILED_NOTE not in reply


def test_false_success_after_failed_command_is_flagged(tmp_path):
    llm = ScriptedLLM(
        LLMResponse("", [ToolCall("c1", "turn_light_on", {"device_id": "main_light"})]),
        LLMResponse("เปิดไฟให้เรียบร้อยแล้วครับ", []),
    )
    assistant, _, driver = make(llm, tmp_path)
    driver.failure = "error"
    assert COMMAND_FAILED_NOTE in asyncio.run(assistant.handle("ช่วยเปิดไฟได้ไหม")).reply


def test_state_description_is_not_flagged(tmp_path):
    llm = ScriptedLLM(LLMResponse("ตอนนี้ไฟปิดอยู่แล้วครับ", []))
    assistant, _, _ = make(llm, tmp_path)
    assert NO_COMMAND_NOTE not in asyncio.run(assistant.handle("ไฟเปิดอยู่ไหม")).reply


def test_failed_device_is_reported_as_error(tmp_path):
    llm = ScriptedLLM(
        LLMResponse("", [ToolCall("c1", "turn_light_on", {"device_id": "main_light"})]),
        LLMResponse("อุปกรณ์ออฟไลน์ครับ เปิดไฟไม่ได้", []),
    )
    assistant, events, driver = make(llm, tmp_path)
    driver.failure = "error"
    turn = asyncio.run(assistant.handle("ช่วยเปิดไฟได้ไหม"))
    assert not turn.commands[0].ok
    assert AIState.ERROR in states(events) and AIState.SUCCESS not in states(events)


def test_quota_error_blocks_further_calls(tmp_path):
    llm = ScriptedLLM(LLMError("quota", "429"))
    assistant, _, _ = make(llm, tmp_path)
    assert asyncio.run(assistant.handle("วันนี้ร้อนจัง")).route == "system"
    assert assistant.quota.exhausted
    turn = asyncio.run(assistant.handle("เล่าเรื่องตลกหน่อย"))
    assert "โควตา" in turn.reply and len(llm.calls) == 1
    # clear commands still work without the LLM
    assert asyncio.run(assistant.handle("เปิดไฟ")).commands[0].ok


def test_local_daily_limit(tmp_path):
    llm = ScriptedLLM(LLMResponse("สวัสดีครับ", []))
    assistant, _, _ = make(llm, tmp_path, daily_limit=1)
    asyncio.run(assistant.handle("สวัสดี"))
    assert "โควตา" in asyncio.run(assistant.handle("สวัสดีอีกที")).reply
    assert len(llm.calls) == 1


def test_without_llm_only_fast_path_works(tmp_path):
    assistant, _, _ = make(None, tmp_path)
    assert asyncio.run(assistant.handle("วันนี้ร้อนจัง")).route == "system"
    assert asyncio.run(assistant.handle("เปิดไฟ")).commands[0].ok


def test_history_keeps_fast_path_context_for_llm(tmp_path):
    llm = ScriptedLLM(LLMResponse("ได้ครับ", []))
    assistant, _, _ = make(llm, tmp_path)
    asyncio.run(assistant.handle("เปิดไฟ"))
    asyncio.run(assistant.handle("ขอบคุณนะ"))
    sent = llm.calls[0]
    assert [m.role for m in sent] == ["user", "assistant", "user"]
