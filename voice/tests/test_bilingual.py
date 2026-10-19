import pytest

from nexus_voice import sounds
from nexus_voice.speech import (
    DEFAULT_VOICES,
    ENGLISH,
    THAI,
    AzureSpeech,
    STTResult,
    detect_language,
    pick_most_confident,
)


# All confidence numbers below are real Azure results from the 2026-10-19 roundtrip experiments.
def pick(*results):
    return pick_most_confident(results, primary=THAI)


def test_thai_speech_keeps_thai():
    thai = STTResult("ok", "ปิดไฟห้องหลัก", THAI, 0.62)
    english = STTResult("ok", "Bitfi, Hongla.", ENGLISH, 0.06)
    assert pick(thai, english) is thai
    assert pick(english, thai) is thai


def test_confidently_wrong_english_does_not_beat_thai():
    thai = STTResult("ok", "วันนี้ร้อนจัง", THAI, 0.53)
    english = STTResult("ok", "One neuron.", ENGLISH, 0.77)
    assert pick(thai, english) is thai


def test_real_english_still_wins():
    thai = STTResult("ok", "ให้ naxis", THAI, 0.51)  # the closest call measured
    english = STTResult("ok", "Hey Nexus.", ENGLISH, 0.87)
    assert pick(thai, english) is english


def test_low_confidence_english_alone_becomes_no_match():
    thai = STTResult("no_match", language=THAI, error="x")
    english = STTResult("ok", "Kopkun.", ENGLISH, 0.04)  # Thai "ขอบคุณ"
    result = pick(thai, english)
    assert result.status == "no_match" and result.text == ""


def test_success_beats_no_match_and_error():
    ok = STTResult("ok", "Turn on the light.", ENGLISH, 0.9)
    no_match = STTResult("no_match", language=THAI, error="x")
    error = STTResult("error", language=THAI, error="401")
    assert pick(error, ok) is ok
    assert pick(no_match, ok) is ok
    assert pick(error, no_match) is no_match
    assert pick(error, error) is error


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("ว่าไงครับ", THAI),
        ("Yes?", ENGLISH),
        ("Turn on the main light.", ENGLISH),
        ("สวัสดี NEXUS", THAI),  # mostly Thai with an English name
        ("ผมคือ NEXUS", THAI),  # tie → Thai
        ("OK ครับ", THAI),
        ("The light is at 30% ครับ", ENGLISH),
        ("30%", THAI),  # no letters → default
    ],
)
def test_detect_language(text, language):
    assert detect_language(text) == language


def test_voice_follows_language_with_fallback():
    azure = AzureSpeech("key", "region")
    assert azure.voice_for(THAI) == DEFAULT_VOICES[THAI]
    assert azure.voice_for(ENGLISH) == DEFAULT_VOICES[ENGLISH]
    assert azure.voice_for("ja-JP") == DEFAULT_VOICES[THAI]  # unknown → first configured language


def test_thai_prompts_keep_their_original_location():
    assert sounds.prompt_path("wake_1", THAI) == sounds.SOUNDS_DIR / "wake_1.wav"
    assert sounds.prompt_path("wake_1", ENGLISH) == sounds.SOUNDS_DIR / "en" / "wake_1.wav"


def test_existing_thai_prompts_load():
    pcm = sounds.load_prompt("wake_1", THAI)
    assert len(pcm) > 16_000  # > 0.5 s of 16 kHz int16 audio


def test_missing_english_prompt_falls_back_to_thai(monkeypatch, tmp_path):
    (tmp_path / "wake_1.wav").write_bytes((sounds.SOUNDS_DIR / "wake_1.wav").read_bytes())
    monkeypatch.setattr(sounds, "SOUNDS_DIR", tmp_path)
    assert sounds.load_prompt("wake_1", ENGLISH) == sounds.load_prompt("wake_1", THAI)


def test_every_prompt_exists_in_both_languages():
    assert sounds.PROMPTS[THAI].keys() == sounds.PROMPTS[ENGLISH].keys()


# ── LID: script share (real numbers from the 2026-10-19 short-phrase experiment) ─────────────
def test_thai_result_written_in_latin_counts_as_weak_thai():
    # English "Dim it." — th model wrote "Demat" (0.35); without the script check Thai won by bias
    thai = STTResult("ok", "Demat", THAI, 0.35)
    english = STTResult("ok", "Dim it.", ENGLISH, 0.42)
    assert pick(thai, english) is english


def test_short_english_words_win_over_latin_thai_output():
    assert pick(STTResult("ok", "Yes", THAI, 0.39), STTResult("ok", "Yes.", ENGLISH, 0.85)).language == ENGLISH
    assert pick(STTResult("ok", "No", THAI, 0.28), STTResult("ok", "No.", ENGLISH, 0.92)).language == ENGLISH


def test_short_thai_words_still_win():
    assert pick(STTResult("ok", "ขอบคุณ", THAI, 0.44), STTResult("ok", "Kopkun.", ENGLISH, 0.06)).language == THAI
    assert pick(STTResult("ok", "ไม่", THAI, 0.36), STTResult("ok", "My.", ENGLISH, 0.05)).language == THAI
    assert pick(STTResult("ok", "โอเค", THAI, 0.65), STTResult("ok", "OK.", ENGLISH, 0.36)).language == THAI


# ── text sanitising before TTS ───────────────────────────────────────────────────────────────
from nexus_voice.speech import speakable  # noqa: E402


def test_speakable_strips_emoji_symbols_and_brackets():
    assert speakable("เปิดไฟแล้วครับ 💡✨ (การจำลอง)", THAI) == "เปิดไฟแล้วครับ การจำลอง"
    assert speakable("Done ✅ — light at 80%!", ENGLISH) == "Done, light at 80%!"


def test_english_voice_never_gets_thai_script():
    assert speakable("The light is on ครับ", ENGLISH) == "The light is on"


def test_thai_voice_keeps_english_names():
    assert speakable("สวัสดีครับ ผมคือ NEXUS", THAI) == "สวัสดีครับ ผมคือ NEXUS"


def test_nothing_speakable_gives_empty_text():
    assert speakable("🎉🎉", THAI) == ""
