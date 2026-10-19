"""VoiceLoop wired to the real backend Assistant (simulated device, no LLM), with fake audio services."""

import asyncio

from nexus.assistant import Assistant
from nexus.devices.drivers.mock import MockDriver
from nexus.devices.service import DeviceService
from nexus.events import AIState, StateChanged, UserMessage
from nexus.quota import QuotaTracker

from nexus_voice.loop import VoiceLoop
from nexus_voice.speech import ENGLISH, THAI, STTResult
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

    def synthesize_stream(self, text, language=None):
        self.spoken.append(text)
        yield b"\x00\x00" * 160
        yield b"\x00\x00" * 160


def make(tmp_path, *stt_results, window_s=8.0):
    events, played = [], []
    driver = MockDriver(latency_s=(0, 0))
    assistant = Assistant(
        DeviceService(driver, timeout_s=0.05), None, QuotaTracker(tmp_path / "q.json", None), events.append
    )
    tts = FakeTTS()

    async def play(chunks):
        played.append(b"".join([c async for c in chunks]))

    loop = VoiceLoop(
        assistant,
        FakeSTT(*stt_results),
        tts,
        FakeWake(),
        play,
        events.append,
        segmenter=UtteranceSegmenter(VadConfig(), is_speech=lambda f: f == SPEECH),
        window_s=window_s,
        echo_tail_s=0,
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
    # two-step feedback: acknowledgement first, then the confirmed (simulated) result
    assert tts.spoken == ["ได้ครับ เดี๋ยวผมเปิดไฟให้ครับ", "จำลองการเปิดไฟห้องหลักเรียบร้อยแล้วครับ"]
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


def test_unheard_speech_is_ignored_silently(tmp_path):
    loop, _, tts, played, events = make(tmp_path, STTResult("no_match", error="x"))
    run(loop, [WAKE] + UTTERANCE)
    assert len(played) == 1  # only the wake prompt — no "ไม่ได้ยิน"
    assert loop.state == AIState.LISTENING and tts.spoken == []
    assert AIState.ERROR not in states(events)  # noise is not an error


def test_two_empty_transcripts_end_the_session_quietly(tmp_path):
    loop, _, tts, played, _ = make(tmp_path, STTResult("no_match", error="x"), STTResult("ok", "  ", THAI, 0.1))
    run(loop, [WAKE] + UTTERANCE + UTTERANCE)
    assert loop.state == AIState.IDLE
    assert len(played) == 1 and tts.spoken == []


def test_empty_transcript_does_not_restart_the_clock(tmp_path):
    loop, _, _, _, _ = make(tmp_path, STTResult("no_match", error="x"), window_s=0.05)

    async def go():
        for frame in [WAKE] + UTTERANCE:
            await loop.process_frame(frame)
        await asyncio.sleep(0.06)  # the window passes in silence
        await loop.process_frame(SILENCE)

    asyncio.run(go())
    assert loop.state == AIState.IDLE


def test_starting_to_speak_extends_the_window(tmp_path, monkeypatch):
    # A fake clock that advances 30 ms per audio frame, like a real microphone.
    import nexus_voice.loop as loop_module

    clock = {"t": 0.0}
    monkeypatch.setattr(loop_module.time, "monotonic", lambda: clock["t"])
    loop, driver, _, _, _ = make(tmp_path, STTResult("ok", "เปิดไฟห้องหลัก", THAI, 0.9), window_s=0.5)

    async def go():
        await loop.process_frame(WAKE)
        clock["t"] += 0.4  # 0.4 s of the 0.5 s window already gone when the user starts talking
        for frame in UTTERANCE:  # 1.5 s of audio: ends long after the original deadline
            await loop.process_frame(frame)
            clock["t"] += 0.03

    asyncio.run(go())
    assert asyncio.run(driver.get_state("main_light")).on  # handled, not cut off by the clock


def test_listening_hint_has_no_countdown(tmp_path):
    loop, _, _, _, events = make(tmp_path)
    run(loop, [WAKE])
    hints = [e.detail for e in events if isinstance(e, StateChanged) and e.state == AIState.LISTENING]
    assert hints == ["สามารถพูดต่อได้ทันที"]


def test_mic_stays_ignored_while_the_room_stops_ringing(tmp_path):
    loop, _, _, _, _ = make(tmp_path)
    loop._echo_tail_s = 0.05
    seen = []

    async def play(chunks):
        async for _ in chunks:
            pass
        asyncio.get_running_loop().call_later(0.02, lambda: seen.append(loop.speaking))

    loop._play = play
    run(loop, [WAKE])
    assert seen == [True]  # 20 ms after playback ended, the mic is still muted


def test_conversation_window_times_out(tmp_path):
    loop, _, _, _, _ = make(tmp_path, window_s=0.0)
    run(loop, [WAKE, SILENCE, SILENCE])
    assert loop.state == AIState.IDLE


def test_english_command_runs_on_the_fast_path_and_is_answered_in_english(tmp_path):
    loop, driver, tts, _, _ = make(tmp_path, STTResult("ok", "Turn on the light", ENGLISH, 0.9))
    run(loop, [WAKE] + UTTERANCE)
    assert loop.language == ENGLISH
    assert asyncio.run(driver.get_state("main_light")).on
    assert tts.spoken == ["Got it, turning on the light.", "Simulated: The main light is now on."]


def test_english_chat_without_llm_gets_english_reply_spoken(tmp_path):
    loop, _, tts, _, _ = make(tmp_path, STTResult("ok", "Hello there", ENGLISH, 0.9))
    run(loop, [WAKE] + UTTERANCE)
    assert tts.spoken and tts.spoken[0].startswith("Gemini isn't configured")


# ── streaming STT (latency work) ────────────────────────────────────────────────────────────
class FakeStreamingSTT:
    """Records exactly what audio reaches the recogniser."""

    def __init__(self, result):
        self.result = result
        self.sessions = []

    def start_stream(self):
        session = FakeSession(self.result)
        self.sessions.append(session)
        return session

    def transcribe(self, pcm):
        raise AssertionError("streaming STT must not fall back to one-shot transcription")


class FakeSession:
    def __init__(self, result):
        self.result = result
        self.audio = b""
        self.finished = self.cancelled = False

    def write(self, pcm):
        self.audio += pcm

    def finish(self):
        self.finished = True
        return self.result

    def cancel(self):
        self.cancelled = True


def test_streaming_stt_gets_exactly_the_utterance_audio(tmp_path):
    loop, driver, _, _, _ = make(tmp_path)
    stt = FakeStreamingSTT(STTResult("ok", "เปิดไฟห้องหลัก", THAI, 0.9))
    loop._stt = stt
    captured = []
    original = loop._handle_utterance

    async def spy(pcm):
        captured.append(pcm)
        await original(pcm)

    loop._handle_utterance = spy
    run(loop, [WAKE] + [SILENCE] * 3 + UTTERANCE)
    [session] = stt.sessions
    assert session.finished and not session.cancelled
    assert session.audio == captured[0]  # pre-roll + speech + trailing silence, nothing twice
    assert asyncio.run(driver.get_state("main_light")).on


def test_going_back_to_sleep_cancels_an_open_stt_session(tmp_path):
    loop, _, _, _, _ = make(tmp_path)
    stt = FakeStreamingSTT(STTResult("ok", "x", THAI, 0.9))
    loop._stt = stt

    async def go():
        for frame in [WAKE] + [SPEECH] * 8:  # speech started, utterance not finished yet
            await loop.process_frame(frame)
        loop._sleep()
        await asyncio.sleep(0.05)  # cancel runs in a worker thread

    asyncio.run(go())
    [session] = stt.sessions
    assert session.cancelled and not session.finished


def test_finished_speech_is_counted_so_the_mic_reader_can_flush_stale_audio(tmp_path):
    loop, _, _, _, _ = make(tmp_path, STTResult("ok", "เปิดไฟห้องหลัก", THAI, 0.9))
    run(loop, [WAKE])
    assert loop.utterances_spoken == 1  # the wake prompt
    run(loop, UTTERANCE)
    assert loop.utterances_spoken == 2  # one reply (acknowledgement + confirmation in one stream)
    assert not loop.speaking


def test_a_crashing_turn_keeps_the_session_alive(tmp_path):
    loop, driver, tts, _, events = make(
        tmp_path,
        STTResult("ok", "เปิดไฟห้องหลัก", THAI, 0.9),
        STTResult("ok", "ปิดไฟห้องหลัก", THAI, 0.9),
    )
    real_handle, calls = loop.assistant.handle, []

    async def flaky(*args, **kwargs):  # first turn blows up like the PermissionError did
        calls.append(args[0])
        if len(calls) == 1:
            raise PermissionError("llm_usage.json")
        return await real_handle(*args, **kwargs)

    loop.assistant.handle = flaky
    run(loop, [WAKE] + UTTERANCE + UTTERANCE)
    assert AIState.ERROR in states(events)
    assert "ทำคำสั่งนี้ไม่สำเร็จครับ ลองพูดใหม่ได้เลยครับ" in tts.spoken
    assert calls == ["เปิดไฟห้องหลัก", "ปิดไฟห้องหลัก"]  # the follow-up still ran, no new wake word
    assert loop.state == AIState.LISTENING


def test_departure_only_when_it_is_the_whole_utterance():
    from nexus_voice.loop import is_departure

    for text in ("ขอบคุณครับ", "ขอบคุณค่ะ", "ขอบคุณ NEXUS", "ขอบคุณ NEXUS ครับ", "Thank you, NEXUS.",
                 "Bye.", "Stop!", "exit", "แค่นี้แหละครับ", "พอแล้ว"):
        assert is_departure(text), text
    for text in ("เปิดไฟหน่อย ขอบคุณครับ", "Stop the music", "ขอบคุณที่เปิดไฟให้", "Thanks, now dim it"):
        assert not is_departure(text), text


def test_session_timeout_setting(monkeypatch):
    from nexus_voice import config

    monkeypatch.setattr(config, "load_dotenv", lambda *_: None)
    monkeypatch.delenv("CONVERSATION_WINDOW_S", raising=False)
    monkeypatch.delenv("VOICE_SESSION_TIMEOUT_SEC", raising=False)
    assert config.load_settings().conversation_window_s == 60
    monkeypatch.setenv("CONVERSATION_WINDOW_S", "20")
    assert config.load_settings().conversation_window_s == 20
    monkeypatch.setenv("VOICE_SESSION_TIMEOUT_SEC", "45")
    assert config.load_settings().conversation_window_s == 45
