"""VoiceLoop wired to the real backend Assistant (simulated device, no LLM), with fake audio services."""

import asyncio

from nexus.assistant import Assistant
from nexus.devices.drivers.mock import MockDriver
from nexus.devices.service import DeviceService
from nexus.events import AIState, StateChanged, UserMessage
from nexus.quota import QuotaTracker

from nexus_voice.loop import VoiceLoop
from nexus_voice.speech import ENGLISH, THAI, STTResult, TTSResult
from nexus_voice.vad import FRAME_BYTES, UtteranceSegmenter, VadConfig

WAKE = b"\x07" * FRAME_BYTES
SPEECH = b"\x01" * FRAME_BYTES
SILENCE = b"\x00" * FRAME_BYTES
UTTERANCE = [SPEECH] * 20 + [SILENCE] * 30


class FakeWake:
    name = "fake"

    def process(self, frame):
        return frame == WAKE

    def reset(self):
        pass


class FakeSTT:
    def __init__(self, *results):
        self.results = list(results)

    def transcribe(self, pcm):
        return self.results.pop(0)


class FakeTTS:
    def __init__(self):
        self.spoken = []

    def synthesize(self, text, language=None):
        self.spoken.append(text)
        return TTSResult("ok", b"\x00\x00" * 160, "fake-voice")


def make(tmp_path, *stt_results, window_s=8.0):
    events, played = [], []
    driver = MockDriver(latency_s=(0, 0))
    assistant = Assistant(
        DeviceService(driver, timeout_s=0.05), None, QuotaTracker(tmp_path / "q.json", None), events.append
    )
    tts = FakeTTS()

    async def play(pcm):
        played.append(pcm)

    loop = VoiceLoop(
        assistant,
        FakeSTT(*stt_results),
        tts,
        FakeWake(),
        play,
        events.append,
        segmenter=UtteranceSegmenter(VadConfig(), is_speech=lambda f: f == SPEECH),
        window_s=window_s,
    )
    return loop, driver, tts, played, events


def run(loop, sequence):
    async def go():
        for frame in sequence:
            await loop.process_frame(frame)

    asyncio.run(go())


def states(events):
    return [e.state for e in events if isinstance(e, StateChanged)]


def test_wake_then_command_controls_the_simulated_light(tmp_path):
    loop, driver, tts, played, events = make(tmp_path, STTResult("ok", "เปิดไฟห้องหลัก", THAI, 0.9))
    run(loop, [SILENCE, WAKE] + UTTERANCE)
    assert asyncio.run(driver.get_state("main_light")).on
    # wake prompt + spoken reply; the spoken reply has no bracketed technical tag
    assert len(played) == 2
    assert tts.spoken == ["จำลองการเปิดไฟห้องหลักแล้วครับ ตอนนี้ไฟห้องหลักเปิด 100%"]
    seq = states(events)
    assert seq[:2] == [AIState.WAKE_DETECTED, AIState.SPEAKING]
    # what the user said is an event too, so the web UI can show it
    assert [e for e in events if isinstance(e, UserMessage)] == [UserMessage("เปิดไฟห้องหลัก", "voice", THAI)]
    assert AIState.TRANSCRIBING in seq and AIState.EXECUTING in seq
    assert loop.state == AIState.LISTENING  # conversation window stays open


def test_no_wake_word_means_nothing_is_transcribed(tmp_path):
    loop, driver, tts, played, _ = make(tmp_path)  # FakeSTT would raise if called
    run(loop, UTTERANCE * 2)
    assert played == [] and tts.spoken == []
    assert loop.state == AIState.IDLE


def test_follow_up_without_saying_the_name_again(tmp_path):
    loop, driver, _, _, _ = make(
        tmp_path,
        STTResult("ok", "เปิดไฟห้องหลัก", THAI, 0.9),
        STTResult("ok", "หรี่ไฟเหลือ 30%", THAI, 0.9),
    )
    run(loop, [WAKE] + UTTERANCE + UTTERANCE)
    assert asyncio.run(driver.get_state("main_light")).brightness == 30


def test_stop_phrase_returns_to_idle(tmp_path):
    loop, _, tts, played, _ = make(tmp_path, STTResult("ok", "ขอบคุณ NEXUS", THAI, 0.9))
    run(loop, [WAKE] + UTTERANCE)
    assert loop.state == AIState.IDLE
    assert tts.spoken == []  # goodbye is a pre-rendered prompt, no TTS call
    assert len(played) == 2


def test_unheard_speech_plays_prompt_and_keeps_listening(tmp_path):
    loop, _, tts, played, events = make(tmp_path, STTResult("no_match", error="x"))
    run(loop, [WAKE] + UTTERANCE)
    assert AIState.ERROR in states(events)
    assert loop.state == AIState.LISTENING and tts.spoken == []


def test_conversation_window_times_out(tmp_path):
    loop, _, _, _, _ = make(tmp_path, window_s=0.0)
    run(loop, [WAKE, SILENCE, SILENCE])
    assert loop.state == AIState.IDLE


def test_english_command_without_llm_gets_english_reply_spoken(tmp_path):
    loop, _, tts, _, _ = make(tmp_path, STTResult("ok", "Turn on the light", ENGLISH, 0.9))
    run(loop, [WAKE] + UTTERANCE)
    assert loop.language == ENGLISH
    assert tts.spoken and tts.spoken[0].startswith("Gemini isn't configured")
