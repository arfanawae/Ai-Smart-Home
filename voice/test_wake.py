"""M1 ขั้น 1.2–1.3 (+1.6 บางส่วน) — VAD + Wake word "NEXUS"

    python voice/test_wake.py selftest            สร้างเสียงทดสอบด้วย Azure TTS แล้ววัดอัตราปลุกติด/ปลุกผิด
    python voice/test_wake.py selftest --trigger final
    python voice/test_wake.py live                ไมค์จริง: เรียก "Nexus" → "ว่าไงครับ" → พูดคำสั่ง → ถอดเสียง
    python voice/test_wake.py live --echo         …และให้ NEXUS พูดทวนสิ่งที่ได้ยิน

โหมด live:
  IDLE ─ "Nexus" ─▶ WAKE (เล่นเสียงที่อัดไว้) ─▶ LISTENING ─ VAD จบประโยค ─▶ STT ─▶ (คุยต่อได้ 8 วินาที) ─▶ IDLE
ระหว่าง NEXUS พูด จะไม่ฟังไมค์ (half-duplex) เพื่อไม่ให้ได้ยินเสียงตัวเอง
"""

from __future__ import annotations

import argparse
import queue
import random
import sys
import time

import sounddevice as sd

from nexus_voice import audio_io, sounds
from nexus_voice.config import load_settings
from nexus_voice.corrections import correct
from nexus_voice.speech import ENGLISH, THAI, AzureSpeech
from nexus_voice.vad import FRAME_MS, FRAME_SAMPLES, UtteranceSegmenter, frames
from nexus_voice.wakeword import VoskWakeWord

CONVERSATION_WINDOW_S = 8.0
STOP_PHRASES = ("ขอบคุณ nexus", "แค่นี้แหละ", "แค่นี้ก่อน", "thank you nexus", "that's all", "goodbye")

# (language, sentence, should wake?)
SELFTEST: list[tuple[str, str, bool]] = [
    (ENGLISH, "Hey NEXUS.", True),
    (ENGLISH, "NEXUS, turn on the light.", True),
    (ENGLISH, "Hey NEXUS, is the light on?", True),
    (THAI, "สวัสดี NEXUS", True),
    (THAI, "NEXUS เปิดไฟหน่อย", True),
    (THAI, "เน็กซัส ปิดไฟ", True),
    (ENGLISH, "Turn on the main light.", False),
    (ENGLISH, "Next time, use the other room.", False),
    (ENGLISH, "I bought a new Lexus yesterday.", False),
    (ENGLISH, "Let's watch Netflix tonight.", False),
    (ENGLISH, "The next bus leaves at six.", False),
    (THAI, "เปิดไฟห้องหลัก", False),
    (THAI, "วันนี้ร้อนจัง", False),
    (THAI, "เน็ตหลุดอีกแล้ว", False),
    (THAI, "แน่นอนครับ", False),
    (THAI, "เล็กซัสคันนี้สวยมาก", False),
]


def selftest(azure: AzureSpeech, trigger: str) -> None:
    detector = VoskWakeWord(trigger=trigger, cooldown_s=0)  # type: ignore[arg-type]
    silence = b"\x00" * (audio_io.SAMPLE_RATE * audio_io.SAMPLE_WIDTH // 2)  # 0.5 s
    correct_count = 0
    for language, sentence, should_wake in SELFTEST:
        tts = azure.synthesize(sentence, language)
        if tts.status != "ok":
            sys.exit(f"TTS ล้มเหลว: {tts.error}")
        detector.reset()
        clip = silence + tts.pcm + silence * 2
        woke_at = next((i for i, f in enumerate(frames(clip)) if detector.process(f)), None)
        woke = woke_at is not None
        correct_count += woke == should_wake
        mark = "✓" if woke == should_wake else "✗"
        expect = "ควรปลุก" if should_wake else "ไม่ควรปลุก"
        when = f"ปลุกที่ {woke_at * FRAME_MS / 1000 - 0.5:.2f}s (เสียงยาว {len(tts.pcm) / 32000:.2f}s)" if woke else "ไม่ปลุก"
        print(f"  {mark} [{language}] {sentence:<34} {expect:<10} → {when}")
    print(f"\nถูกต้อง {correct_count}/{len(SELFTEST)} (trigger={trigger})")


def live(azure: AzureSpeech, trigger: str, echo: bool, input_device: int | None, output_device: int | None) -> None:
    detector = VoskWakeWord(trigger=trigger)  # type: ignore[arg-type]
    segmenter = UtteranceSegmenter()
    frames_in: queue.Queue[bytes] = queue.Queue()

    def on_audio(data, _frames, _time, status) -> None:
        if status:
            print(f"  ⚠ {status}", file=sys.stderr)
        frames_in.put(bytes(data))

    def say(pcm: bytes) -> None:
        audio_io.play(pcm, output_device)
        while not frames_in.empty():  # half-duplex: drop what the mic heard while NEXUS was speaking
            frames_in.get_nowait()
        detector.reset()
        segmenter.reset()

    state, deadline, language = "IDLE", 0.0, THAI
    print('🎙  IDLE — พูด "Nexus" หรือ "Hey Nexus" เพื่อเรียก (Ctrl+C เพื่อออก)')
    with sd.RawInputStream(
        samplerate=audio_io.SAMPLE_RATE,
        blocksize=FRAME_SAMPLES,
        channels=1,
        dtype="int16",
        device=input_device,
        callback=on_audio,
    ):
        while True:
            frame = frames_in.get()
            if state == "IDLE":
                if detector.process(frame):
                    print("✨ WAKE_DETECTED")
                    say(sounds.load_prompt(random.choice(["wake_1", "wake_2"]), language))
                    state, deadline = "LISTENING", time.monotonic() + CONVERSATION_WINDOW_S
                    print("👂 LISTENING")
                continue

            utterance = segmenter.push(frame)
            if utterance is None:
                if not segmenter.in_speech and time.monotonic() > deadline:
                    print('💤 IDLE — หมดเวลาคุยต่อ พูด "Nexus" เพื่อเรียกใหม่')
                    state = "IDLE"
                    detector.reset()
                continue

            print(f"📝 TRANSCRIBING ({len(utterance) / 32000:.1f}s)")
            stt = azure.transcribe(utterance)
            if stt.status != "ok":
                print(f"   ✗ {stt.error}")
                # no apology prompt: unheard speech is ignored quietly (see nexus_voice/loop.py)
            else:
                language = stt.language or THAI
                text = correct(stt.text)
                print(f"   [{language}] \"{text}\" ({stt.latency_ms} ms, conf {stt.confidence:.2f})")
                if any(p in text.lower() for p in STOP_PHRASES):
                    say(sounds.load_prompt("goodbye", language))
                    print("💤 IDLE")
                    state = "IDLE"
                    continue
                if echo:
                    reply = f"You said: {text}" if language == ENGLISH else f"คุณพูดว่า {text}"
                    tts = azure.synthesize(reply, language)
                    if tts.status == "ok":
                        print("🔊 SPEAKING")
                        say(tts.pcm)
            deadline = time.monotonic() + CONVERSATION_WINDOW_S
            print(f"👂 LISTENING (คุยต่อได้ {CONVERSATION_WINDOW_S:g} วินาทีโดยไม่ต้องเรียกชื่อ)")


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="ทดสอบ VAD + Wake word NEXUS")
    parser.add_argument("command", choices=["selftest", "live"])
    parser.add_argument("--trigger", choices=["partial", "final"], default="final")
    parser.add_argument("--echo", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    if not (settings.azure_key and settings.azure_region):
        sys.exit("ยังไม่ได้ใส่ AZURE_SPEECH_KEY / AZURE_SPEECH_REGION ใน backend/.env")
    azure = AzureSpeech(
        settings.azure_key, settings.azure_region, settings.stt_languages, settings.tts_voices, settings.stt_language_mode
    )
    if args.command == "selftest":
        selftest(azure, args.trigger)
    else:
        try:
            live(azure, args.trigger, args.echo, settings.input_device, settings.output_device)
        except KeyboardInterrupt:
            print("\nลาก่อนครับ")


if __name__ == "__main__":
    main()
