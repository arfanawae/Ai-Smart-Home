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
