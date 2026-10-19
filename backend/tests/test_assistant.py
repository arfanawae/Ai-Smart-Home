import asyncio

from nexus.assistant import Assistant
from nexus.devices.drivers.mock import MockDriver
from nexus.devices.service import DeviceService
from nexus.events import AIState, StateChanged
from nexus.llm.base import LLMError, LLMResponse, ToolCall
from nexus.quota import QuotaTracker
from nexus import replies
from nexus.replies import COMMAND_FAILED_NOTE, NO_COMMAND_NOTE, SIMULATION_TAG


class ScriptedLLM:
    """Returns pre-scripted responses and records what it was sent."""

    provider = "fake"
    model = "fake"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.systems = []

    async def generate(self, system, messages, tools):
        self.calls.append(list(messages))
        self.systems.append(system)
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
    assert COMMAND_FAILED_NOTE in asyncio.run(assistant.handle("ช่วยดูเรื่องไฟให้หน่อย")).reply


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
    turn = asyncio.run(assistant.handle("ช่วยจัดการเรื่องไฟให้หน่อย"))  # not a fast-path command
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


# ── bilingual + claim guard cases found in the live Gemini test (2026-10-19) ──────────────
from nexus.assistant import _claims_completion
from nexus.language import detect_language
from nexus.replies import MESSAGES


def test_offer_phrased_as_question_is_not_a_claim():
    # real Gemini reply that the first guard wrongly flagged
    assert not _claims_completion("ไฟห้องหลักปิดอยู่ครับ ต้องการให้ NEXUS เปิดไฟแล้วปรับความสว่างลงไหมครับ")
    assert not _claims_completion("Would you like me to turn on the light and dim it?")


def test_real_claims_are_still_caught():
    assert _claims_completion("เปิดไฟแล้วครับ ต้องการปรับความสว่างไหมครับ")
    assert _claims_completion("I've turned on the main light.")
    assert _claims_completion("Done! The light is at 50%.")
    assert not _claims_completion("I couldn't turn on the light.")
    assert not _claims_completion("The light is on right now.")


def test_detect_user_language():
    assert detect_language("It's too bright in here") == "en"
    assert detect_language("สวัสดี NEXUS") == "th"
    assert detect_language("30") == "th"


def test_english_user_gets_english_instruction_and_notes(tmp_path):
    llm = ScriptedLLM(LLMResponse("I've turned on the light for you.", []))
    assistant, _, _ = make(llm, tmp_path)
    reply = asyncio.run(assistant.handle("I need some light in here")).reply
    assert MESSAGES["en"]["no_command"] in reply
    # the system prompt sent to Gemini asks for English this turn
    assert "Reply in English only" in llm.systems[0]


def test_english_system_messages_without_llm(tmp_path):
    assistant, _, _ = make(None, tmp_path)
    assert asyncio.run(assistant.handle("Hello there")).reply == MESSAGES["en"]["no_llm"]


def test_english_simulation_tag(tmp_path):
    llm = ScriptedLLM(
        LLMResponse("", [ToolCall("c1", "turn_light_on", {"device_id": "main_light"})]),
        LLMResponse("The main light is on now.", []),
    )
    assistant, _, _ = make(llm, tmp_path)
    reply = asyncio.run(assistant.handle("I could use some light in here")).reply  # LLM path
    assert reply.endswith(MESSAGES["en"]["simulation_tag"])


# ── manual control + turn lock (web UI) ──────────────────────────────────────────────────────
from nexus.events import CommandUpdate


def test_manual_command_skips_llm_and_emits_update(tmp_path):
    llm = ScriptedLLM()
    assistant, events, driver = make(llm, tmp_path)
    result = asyncio.run(assistant.run_manual("set_brightness", {"device_id": "main_light", "level": 40}))
    assert result.ok and llm.calls == []
    assert [e.route for e in events if isinstance(e, CommandUpdate)] == ["manual"]
    assert asyncio.run(driver.get_state("main_light")).brightness == 40


def test_manual_command_is_still_validated(tmp_path):
    assistant, _, _ = make(None, tmp_path)
    result = asyncio.run(assistant.run_manual("set_brightness", {"device_id": "main_light", "level": 500}))
    assert result.status.value == "rejected"


def test_concurrent_turns_run_one_at_a_time(tmp_path):
    order = []

    class SlowLLM(ScriptedLLM):
        async def generate(self, system, messages, tools):
            order.append("start")
            await asyncio.sleep(0.05)
            order.append("end")
            return LLMResponse("ครับ", [])

    assistant, _, _ = make(SlowLLM(), tmp_path)

    async def both():
        await asyncio.gather(assistant.handle("สวัสดี"), assistant.handle("วันนี้ร้อนจัง"))

    asyncio.run(both())
    assert order == ["start", "end", "start", "end"]


# ── streaming speech + language context (latency work) ──────────────────────────────────────
class StreamingLLM(ScriptedLLM):
    """Streams each scripted response's text in small deltas, like Gemini's generate_stream."""

    async def generate_stream(self, system, messages, tools):
        response = await self.generate(system, messages, tools)
        for i in range(0, len(response.text), 4):
            yield response.text[i : i + 4]
        yield response


def test_sentences_are_spoken_while_the_reply_streams(tmp_path):
    llm = StreamingLLM(LLMResponse("วันนี้อากาศร้อนจริงครับ ผมยังควบคุมพัดลมไม่ได้ครับ", []))
    assistant, _, _ = make(llm, tmp_path)
    spoken = []
    asyncio.run(assistant.handle("วันนี้ร้อนจัง", on_speech=spoken.append))
    assert spoken == ["วันนี้อากาศร้อนจริงครับ", "ผมยังควบคุมพัดลมไม่ได้ครับ"]


def test_a_success_claim_is_not_spoken_before_the_device_confirms(tmp_path):
    # The model claims success before (and without) any command: it must be held, then corrected.
    llm = StreamingLLM(LLMResponse("เปิดไฟให้เรียบร้อยแล้วครับ", []))
    assistant, _, _ = make(llm, tmp_path)
    spoken = []
    asyncio.run(assistant.handle("ช่วยดูเรื่องไฟให้หน่อย", on_speech=spoken.append))
    assert spoken[0] == "เปิดไฟให้เรียบร้อยแล้วครับ"
    assert spoken[1] == "แต่จริง ๆ ยังไม่ได้ส่งคำสั่งไปที่อุปกรณ์นะครับ"


def test_tool_preface_is_spoken_and_simulation_is_said_at_the_end(tmp_path):
    llm = StreamingLLM(
        LLMResponse("Sure, dimming it a little.", [ToolCall("c1", "adjust_brightness", {"device_id": "main_light", "delta": 20})]),
        LLMResponse("The light is now at 20%.", []),
    )
    assistant, _, _ = make(llm, tmp_path)
    spoken = []
    turn = asyncio.run(assistant.handle("I could use some light in here", "en-US", on_speech=spoken.append))
    assert spoken == ["Sure, dimming it a little.", "The light is now at 20%.", "This was only a simulation."]
    assert turn.reply.startswith("Sure, dimming it a little. The light is now at 20%.")


def test_fast_path_speaks_acknowledgement_then_confirmation(tmp_path):
    assistant, _, _ = make(None, tmp_path)
    spoken = []
    asyncio.run(assistant.handle("Turn on the light", "en-US", on_speech=spoken.append))
    assert spoken == ["Got it, turning on the light.", "Simulated: The main light is now on."]  # tag not repeated


def test_acknowledgement_comes_before_the_command_and_never_claims_success(tmp_path):
    assistant, events, _ = make(None, tmp_path)
    timeline = []
    assistant._emit = lambda e: (events.append(e), timeline.append(type(e).__name__))
    asyncio.run(assistant.handle("เปิดไฟห้องหลัก", "th-TH", on_speech=lambda s: timeline.append(f"say:{s}")))
    assert timeline.index("say:ได้ครับ เดี๋ยวผมเปิดไฟให้ครับ") < timeline.index("CommandUpdate")
    assert timeline.index("CommandUpdate") < timeline.index("say:จำลองการเปิดไฟห้องหลักเรียบร้อยแล้วครับ")
    from nexus.assistant import _claims_completion

    for language in ("th", "en"):
        for tool, args in [("turn_light_on", {}), ("turn_light_off", {}), ("set_brightness", {"level": 30}),
                           ("adjust_brightness", {"delta": -20}), ("adjust_brightness", {"delta": 20})]:
            ack = replies.acknowledgement(tool, args, language)
            assert ack and not _claims_completion(ack), ack


def test_failed_command_after_acknowledgement_is_reported(tmp_path):
    assistant, _, driver = make(None, tmp_path)
    driver.failure = "error"
    spoken = []
    asyncio.run(assistant.handle("ปิดไฟ", "th-TH", on_speech=spoken.append))
    assert spoken[0] == "ได้ครับ เดี๋ยวผมปิดไฟให้ครับ"
    assert spoken[1].startswith("ปิดไฟห้องหลักไม่สำเร็จครับ")


def test_llm_tool_call_is_acknowledged_once(tmp_path):
    llm = StreamingLLM(
        LLMResponse("", [ToolCall("c1", "adjust_brightness", {"device_id": "main_light", "delta": -20})]),
        LLMResponse("หรี่ไฟลงแล้วครับ ตอนนี้ 80% เป็นการจำลองครับ", []),
    )
    assistant, _, driver = make(llm, tmp_path)
    asyncio.run(driver.apply("main_light", {"on": True, "brightness": 100}))
    spoken = []
    asyncio.run(assistant.handle("ไฟสว่างเกินไป", "th-TH", on_speech=spoken.append))
    assert spoken[0] == "ได้ครับ เดี๋ยวผมหรี่ไฟให้ครับ"
    assert spoken[1:] == ["หรี่ไฟลงแล้วครับ", "ตอนนี้ 80% เป็นการจำลองครับ"]


def test_stt_language_overrides_the_script_guess(tmp_path):
    llm = ScriptedLLM(LLMResponse("โอเคครับ", []))
    assistant, _, _ = make(llm, tmp_path)
    asyncio.run(assistant.handle("OK", "th-TH"))  # Latin text, but Thai speech
    assert 'user_language: "th-TH"' in llm.systems[0]
