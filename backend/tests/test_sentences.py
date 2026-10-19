from nexus.sentences import MAX_CHARS, SentenceSplitter


def stream(text: str, size: int = 3) -> list[str]:
    splitter = SentenceSplitter()
    out = []
    for i in range(0, len(text), size):
        out += splitter.feed(text[i : i + size])
    return out + splitter.flush()


def test_english_sentences():
    assert stream("The main light is on now. Would you like me to dim it?") == [
        "The main light is on now.",
        "Would you like me to dim it?",
    ]


def test_thai_clauses_split_after_polite_particle():
    text = "ลดความสว่างเหลือ 80% แล้วครับ ต้องการให้หรี่ลงอีกไหมครับ"
    assert stream(text) == ["ลดความสว่างเหลือ 80% แล้วครับ", "ต้องการให้หรี่ลงอีกไหมครับ"]


def test_short_pieces_wait_for_more_text():
    assert stream("ได้ครับ ตอนนี้ไฟห้องหลักปิดอยู่ครับ") == ["ได้ครับ ตอนนี้ไฟห้องหลักปิดอยู่ครับ"]


def test_run_on_text_is_cut_at_a_space():
    words = " ".join(["คำ"] * 100)
    pieces = stream(words, size=7)
    assert all(len(p) <= MAX_CHARS for p in pieces)
    assert " ".join(pieces) == words


def test_nothing_is_lost():
    text = "Yes. No! Maybe? ใช่ครับ แล้วก็\nบรรทัดใหม่"
    assert "".join(stream(text)).replace(" ", "") == text.replace(" ", "").replace("\n", "")
