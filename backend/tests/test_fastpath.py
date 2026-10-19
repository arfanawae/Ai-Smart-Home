import pytest

from nexus.fastpath import RELATIVE_STEP, decide

LIGHT = {"device_id": "main_light"}


@pytest.mark.parametrize(
    ("text", "tool", "args"),
    [
        # Thai commands
        ("เปิดไฟ", "turn_light_on", {}),
        ("ปิดไฟห้องหลัก", "turn_light_off", {}),
        ("ช่วยเปิดไฟห้องหลักให้หน่อยครับ", "turn_light_on", {}),
        ("Nexus ปิดไฟ", "turn_light_off", {}),
        ("ปิดหลอดไฟหน่อย", "turn_light_off", {}),
        ("งั้นลองปิดไฟให้หน่อย", "turn_light_off", {}),  # "ลอง...หน่อย" is a polite request
        ("ลองเปิดไฟให้หน่อยครับ", "turn_light_on", {}),
        ("หรี่ไฟเหลือ 30", "set_brightness", {"level": 30}),
        ("ลดไฟเหลือ 30%", "set_brightness", {"level": 30}),
        ("ตั้งไฟ 70 เปอร์เซ็นต์", "set_brightness", {"level": 70}),
        ("ปรับความสว่าง ๕๐", "set_brightness", {"level": 50}),  # Thai digits
        ("เปิดไฟ 50%", "set_brightness", {"level": 50}),
        ("ตั้งความสว่าง 150", "set_brightness", {"level": 150}),  # validator rejects, not the fast path
        # Thai polite requests (v2)
        ("เปิดไฟได้ไหม", "turn_light_on", {}),
        ("ช่วยปิดไฟได้ไหมครับ", "turn_light_off", {}),
        # Thai relative brightness (v2)
        ("หรี่ไฟหน่อย", "adjust_brightness", {"delta": -RELATIVE_STEP}),
        ("หรี่ไฟลงอีกหน่อย", "adjust_brightness", {"delta": -RELATIVE_STEP}),
        ("เพิ่มความสว่างหน่อย", "adjust_brightness", {"delta": RELATIVE_STEP}),
        ("ไฟสว่างขึ้นอีกนิด", "adjust_brightness", {"delta": RELATIVE_STEP}),
        ("ลดความสว่างลง 20", "adjust_brightness", {"delta": -20}),
        ("เพิ่มความสว่าง 30", "adjust_brightness", {"delta": 30}),
        # Thai status questions (v2) — read-only
        ("ไฟเปิดอยู่ไหม", "get_device_status", {}),
        ("ตอนนี้ไฟเปิดอยู่หรือเปล่า", "get_device_status", {}),
        ("ไฟปิดอยู่หรือไม่", "get_device_status", {}),
        ("ตอนนี้ไฟสว่างเท่าไหร่", "get_device_status", {}),
        ("สถานะไฟห้องหลัก", "get_device_status", {}),
        # English (v2)
        ("Turn on the light", "turn_light_on", {}),
        ("Turn the main light off.", "turn_light_off", {}),
        ("Switch off the lights", "turn_light_off", {}),
        ("Lights on", "turn_light_on", {}),
        ("Hey NEXUS, turn off the light", "turn_light_off", {}),
        ("Can you turn on the light?", "turn_light_on", {}),
        ("Could you please switch off the lamp?", "turn_light_off", {}),
        ("Set the light to 30%", "set_brightness", {"level": 30}),
        ("Dim the light to 40 percent", "set_brightness", {"level": 40}),
        ("Brightness 70", "set_brightness", {"level": 70}),
        ("Dim the light", "adjust_brightness", {"delta": -RELATIVE_STEP}),
        ("Make the light brighter", "adjust_brightness", {"delta": RELATIVE_STEP}),
        ("Turn the light down a bit", "adjust_brightness", {"delta": -RELATIVE_STEP}),
        ("Dim the lights by 30", "adjust_brightness", {"delta": -30}),
        ("Is the light on?", "get_device_status", {}),
        ("Is the light on right now?", "get_device_status", {}),
        ("How bright is the light?", "get_device_status", {}),
        ("What's the brightness?", "get_device_status", {}),
    ],
)
def test_executes_unambiguous_commands(text, tool, args):
    d = decide(text)
    assert d.execute, d.reason
    assert d.tool == tool
    assert d.args == {**LIGHT, **args}


def test_off_is_not_matched_inside_on():
    # "ปิด" is a substring of "เปิด" — "เปิดไฟ" must never be read as on+off
    assert decide("เปิดไฟ").tool == "turn_light_on"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        # negation always wins, even over status / polite forms
        ("อย่าเพิ่งเปิดไฟ", "negation"),
        ("ไม่ต้องเปิดไฟนะ", "negation"),
        ("ห้ามปิดไฟ", "negation"),
        ("ไฟไม่ได้เปิดอยู่ใช่ไหม", "negation"),
        ("Don't turn on the light yet", "negation"),
        ("Do not switch off the lights", "negation"),
        ("Never turn the light off", "negation"),
        # questions that are not status questions
        ("เปิดหรือปิดไฟดี", "question"),
        ("เปิดไฟทำไม", "question"),
        ("Why is the light on?", "question"),
        ("Should I turn on the light?", "question"),
        # conditions / time
        ("ถ้ามืดแล้วเปิดไฟ", "condition_or_time"),
        ("อีก 5 นาทีปิดไฟ", "condition_or_time"),
        ("เดี๋ยวค่อยปิดไฟ", "condition_or_time"),
        ("Turn off the light in 10 minutes", "condition_or_time"),
        ("Turn on the light when it gets dark", "condition_or_time"),
        ("Turn the light off later", "condition_or_time"),
        # hedging / reported speech
        ("ลองเปิดไฟดู", "hedging"),
        ("ลองเปิดไฟดีกว่า", "hedging"),
        ("Maybe turn on the light", "hedging"),
        ("แม่บอกให้ปิดไฟ", "reported_speech"),
        ("Mom said turn off the light", "reported_speech"),
        # statements, not requests
        ("ปิดไฟแล้ว", "state_description"),
        ("ไฟเปิดอยู่", "state_description"),
        ("เปิดไฟแล้วก็ปิดไฟ", "state_description"),
        # other rooms / devices
        ("ปิดแอร์", "not_about_light"),
        ("เปิดไฟห้องนอน", "other_room"),
        ("Turn on the bedroom light", "other_device"),
        ("Turn on the fan", "not_about_light"),
        # indirect → the LLM decides (and can ask)
        ("ไฟสว่างเกินไป", "brightness_without_number"),
        ("It's too bright in here", "not_about_light"),
        ("The light is too bright", "no_explicit_action"),
        ("วันนี้ร้อนจัง", "not_about_light"),
        ("เล่าเรื่องตลกหน่อย", "not_about_light"),
        ("ไฟ 3 ดวง", "number_without_brightness_verb"),
        ("", "empty"),
    ],
)
def test_defers_to_llm(text, reason):
    d = decide(text)
    assert not d.execute, f"{text!r} executed {d.tool} {d.args}"
    assert d.reason == reason


def test_conflicting_actions_defer():
    assert decide("เปิดไฟปิดไฟ").reason == "conflicting_actions"
    assert decide("Turn on the light and turn off the lamp").reason == "conflicting_actions"
    assert decide("หรี่ไฟแล้วเพิ่มความสว่าง").reason in {"conflicting_actions", "state_description"}


def test_long_sentence_defers():
    assert decide("เปิดไฟ" + " ครับ" * 20).reason == "too_long"
