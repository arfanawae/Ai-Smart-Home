import pytest

from nexus_voice.corrections import correct


@pytest.mark.parametrize(
    ("heard", "expected"),
    [
        # observed in the Azure roundtrip test
        ("สวัสดี mexis", "สวัสดี NEXUS"),
        ("รีไฟล์เหลือ 30%", "หรี่ไฟเหลือ 30%"),
        ("รีไฟเหลือ 30%", "หรี่ไฟเหลือ 30%"),
        ("เปิดไฟห้องละ", "เปิดไฟห้องหลัก"),
        ("เปิดไฟห้องละครับ", "เปิดไฟห้องหลักครับ"),
        ("Nexis เปิดไฟ", "NEXUS เปิดไฟ"),
        ("เน็กซัส ปิดไฟ", "NEXUS ปิดไฟ"),
    ],
)
def test_fixes_observed_errors(heard, expected):
    assert correct(heard) == expected


@pytest.mark.parametrize(
    "text",
    [
        "หรี่ไฟเหลือ 30%",  # already correct — must not become "หหรี่ไฟ"
        "ปิดไฟห้องหลัก",
        "ห้องละ 500 บาท",  # "per room" — not a light command
        "เปิดไฟล์งาน",  # a real "file", no "รี" before it
        "วันนี้ร้อนจัง",
        # English transcriptions must pass through untouched
        "Turn on the main light.",
        "Dim the light to 30%.",
        "Don't turn on the light yet.",
        "I need to refile the report.",  # Latin "refile" ≠ Thai "รีไฟ"
        "Mexico is hot today.",
    ],
)
def test_leaves_other_text_alone(text):
    assert correct(text) == text


def test_english_wake_name_is_normalised():
    assert correct("Hey Nexus, turn on the light.") == "Hey NEXUS, turn on the light."
    assert correct("Hey mexis turn off the light") == "Hey NEXUS turn off the light"
