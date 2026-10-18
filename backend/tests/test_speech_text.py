from nexus.replies import MESSAGES, SIMULATION_TAG, to_speech


def test_fast_path_reply_drops_the_tag_because_it_already_says_simulated():
    reply = f"จำลองการเปิดไฟห้องหลักแล้วครับ ตอนนี้ไฟห้องหลักเปิด 100% {SIMULATION_TAG}"
    assert to_speech(reply) == "จำลองการเปิดไฟห้องหลักแล้วครับ ตอนนี้ไฟห้องหลักเปิด 100%"


def test_llm_reply_without_simulation_word_gets_a_short_spoken_version():
    reply = f"ลดความสว่างเหลือ 80% แล้วครับ {SIMULATION_TAG}"
    assert to_speech(reply) == "ลดความสว่างเหลือ 80% แล้วครับ นี่เป็นการจำลองนะครับ"


def test_truth_note_becomes_a_spoken_correction_not_silence():
    reply = f"เปิดไฟให้เรียบร้อยแล้วครับ\n{MESSAGES['th']['no_command']}"
    spoken = to_speech(reply)
    assert "หมายเหตุ" not in spoken and "(" not in spoken
    assert spoken.endswith("แต่จริง ๆ ยังไม่ได้ส่งคำสั่งไปที่อุปกรณ์นะครับ")


def test_english_notes_and_tag():
    reply = f"I've turned on the light.\n{MESSAGES['en']['command_failed']}"
    assert to_speech(reply) == "I've turned on the light. But the command did not go through."
    reply = f"The main light is on now. {MESSAGES['en']['simulation_tag']}"
    assert to_speech(reply) == "The main light is on now. This was only a simulation."


def test_markdown_symbols_are_not_read_aloud():
    assert to_speech("**ไฟ** เปิดอยู่ครับ") == "ไฟ เปิดอยู่ครับ"


def test_plain_reply_is_unchanged():
    assert to_speech("สวัสดีครับ มีอะไรให้ช่วยไหมครับ") == "สวัสดีครับ มีอะไรให้ช่วยไหมครับ"
