import pytest

from nexus.fastpath import decide


@pytest.mark.parametrize(
    ("text", "tool", "args"),
    [
        ("เปิดไฟ", "turn_light_on", {}),
        ("ปิดไฟห้องหลัก", "turn_light_off", {}),
        ("ช่วยเปิดไฟห้องหลักให้หน่อยครับ", "turn_light_on", {}),
        ("Nexus ปิดไฟ", "turn_light_off", {}),
        ("ปิดหลอดไฟหน่อย", "turn_light_off", {}),
        ("หรี่ไฟเหลือ 30", "set_brightness", {"level": 30}),
        ("ลดไฟเหลือ 30%", "set_brightness", {"level": 30}),
        ("ตั้งไฟ 70 เปอร์เซ็นต์", "set_brightness", {"level": 70}),
        ("ปรับความสว่าง ๕๐", "set_brightness", {"level": 50}),  # Thai digits
        ("เปิดไฟ 50%", "set_brightness", {"level": 50}),
        ("ตั้งความสว่าง 150", "set_brightness", {"level": 150}),  # validator rejects, not the fast path
    ],
)
def test_executes_unambiguous_commands(text, tool, args):
    d = decide(text)
    assert d.execute, d.reason
    assert d.tool == tool
    assert d.args == {"device_id": "main_light", **args}


def test_off_is_not_matched_inside_on():
    # "ปิด" is a substring of "เปิด" — "เปิดไฟ" must never be read as on+off
    assert decide("เปิดไฟ").tool == "turn_light_on"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("อย่าเพิ่งเปิดไฟ", "negation"),
        ("ไม่ต้องเปิดไฟนะ", "negation"),
        ("ห้ามปิดไฟ", "negation"),
        ("เปิดไฟได้ไหม", "question"),
        ("ไฟเปิดอยู่หรือเปล่า", "question"),
        ("เปิดหรือปิดไฟดี", "question"),
        ("ถ้ามืดแล้วเปิดไฟ", "condition_or_time"),
        ("อีก 5 นาทีปิดไฟ", "condition_or_time"),
        ("เดี๋ยวค่อยปิดไฟ", "condition_or_time"),
        ("ลองเปิดไฟดู", "hedging"),
        ("แม่บอกให้ปิดไฟ", "reported_speech"),
        ("ปิดไฟแล้ว", "state_description"),
        ("ไฟเปิดอยู่", "state_description"),
        ("ปิดแอร์", "other_device"),
        ("เปิดไฟห้องนอน", "other_room"),
        ("เปิดไฟแล้วก็ปิดไฟ", "state_description"),
        ("ไฟสว่างเกินไป", "brightness_without_number"),
        ("หรี่ไฟหน่อย", "brightness_without_number"),
        ("ลดความสว่างลง 20", "relative_change"),
        ("วันนี้ร้อนจัง", "not_about_light"),
        ("เล่าเรื่องตลกหน่อย", "not_about_light"),
        ("ไฟ 3 ดวง", "number_without_brightness_verb"),
        ("", "empty"),
    ],
)
def test_defers_to_llm(text, reason):
    d = decide(text)
    assert not d.execute
    assert d.reason == reason


def test_conflicting_actions_defer():
    assert decide("เปิดไฟปิดไฟ").reason == "conflicting_actions"


def test_long_sentence_defers():
    assert decide("เปิดไฟ" + " ครับ" * 20).reason == "too_long"
