"""M1 ขั้น 1.4–1.5 — ทดสอบ Azure STT/TTS สองภาษา (ไทย + อังกฤษ)

    python voice/test_speech.py voices                    รายชื่อเสียงไทยและอังกฤษที่ใช้ได้
    python voice/test_speech.py tts "สวัสดีครับ"           พูดข้อความ (เลือกเสียงตามภาษาของข้อความ)
    python voice/test_speech.py tts "Hello, I am NEXUS"
    python voice/test_speech.py roundtrip                 TTS → STT ทั้งไทยและอังกฤษ (ไม่ต้องพูดเอง)
    python voice/test_speech.py roundtrip --lang en-US    ทดสอบเฉพาะภาษาเดียว
    python voice/test_speech.py stt --seconds 5           อัดจากไมค์ → ถอดเสียง + บอกภาษาที่ตรวจพบ
    python voice/test_speech.py echo                      อัด → ถอดเสียง → NEXUS พูดทวนในภาษาเดียวกัน
    python voice/test_speech.py prompts                   สร้างไฟล์เสียงตอบรับล่วงหน้าทั้งสองภาษา
"""

from __future__ import annotations

import argparse
import re
import sys

from nexus_voice import audio_io, sounds
from nexus_voice.config import load_settings
from nexus_voice.corrections import correct
from nexus_voice.speech import ENGLISH, THAI, AzureSpeech

# (language spoken, sentence given to TTS, text we expect back from STT after corrections)
# Azure writes spoken numbers as digits, which is what the backend fast path wants.
ROUNDTRIP: list[tuple[str, str, str]] = [
    (THAI, "เปิดไฟห้องหลัก", "เปิดไฟห้องหลัก"),
    (THAI, "ปิดไฟห้องหลัก", "ปิดไฟห้องหลัก"),
    (THAI, "หรี่ไฟเหลือสามสิบเปอร์เซ็นต์", "หรี่ไฟเหลือ 30%"),
    (THAI, "อย่าเพิ่งเปิดไฟ", "อย่าเพิ่งเปิดไฟ"),
    (THAI, "ไฟสว่างเกินไป", "ไฟสว่างเกินไป"),
    (THAI, "วันนี้ร้อนจัง", "วันนี้ร้อนจัง"),
    (THAI, "ตอนนี้ไฟเปิดอยู่ไหม", "ตอนนี้ไฟเปิดอยู่ไหม"),
    (THAI, "สวัสดี NEXUS", "สวัสดี NEXUS"),
    (ENGLISH, "Turn on the main light.", "Turn on the main light."),
    (ENGLISH, "Turn off the main light.", "Turn off the main light."),
    (ENGLISH, "Dim the light to thirty percent.", "Dim the light to 30%."),
    (ENGLISH, "Don't turn on the light yet.", "Don't turn on the light yet."),
    (ENGLISH, "It's too bright in here.", "It's too bright in here."),
    (ENGLISH, "It's really hot today.", "It's really hot today."),
    (ENGLISH, "Is the light on right now?", "Is the light on right now?"),
    (ENGLISH, "Hey NEXUS.", "Hey NEXUS."),
]


def _simplify(text: str) -> str:
    return re.sub(r"[\s.,!?ๆ]", "", text).lower()


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="ทดสอบ Azure STT/TTS สองภาษา")
    parser.add_argument("command", choices=["voices", "tts", "roundtrip", "stt", "echo", "prompts"])
    parser.add_argument("text", nargs="?", default="สวัสดีครับ ผมคือ NEXUS ผู้ช่วยบ้านอัจฉริยะของคุณครับ")
    parser.add_argument("--lang", choices=[THAI, ENGLISH], help="บังคับภาษา (ค่าเริ่มต้น: ตรวจจากข้อความ/เสียง)")
    parser.add_argument("--mode", choices=["parallel", "auto"], help="วิธีแยกภาษา STT (ค่าเริ่มต้นจาก .env)")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--no-playback", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    if not (settings.azure_key and settings.azure_region):
        sys.exit("ยังไม่ได้ใส่ AZURE_SPEECH_KEY / AZURE_SPEECH_REGION ใน backend/.env")
    mode = args.mode or settings.stt_language_mode
    azure = AzureSpeech(settings.azure_key, settings.azure_region, settings.stt_languages, settings.tts_voices, mode)
    voices = " · ".join(f"{lang}: {voice}" for lang, voice in settings.tts_voices.items())
    print(f"Azure {settings.azure_region} · STT: {' + '.join(settings.stt_languages)} ({mode}) · {voices}")

    def speak(text: str, language: str | None = None) -> bytes:
        result = azure.synthesize(text, language)
        if result.status != "ok":
            sys.exit(f"TTS ล้มเหลว: {result.error}")
        seconds = len(result.pcm) / (audio_io.SAMPLE_RATE * audio_io.SAMPLE_WIDTH)
        print(f"  TTS {result.voice} · {result.latency_ms} ms · {result.characters} ตัวอักษร · {seconds:.1f} วินาที")
        if not args.no_playback:
            audio_io.play(result.pcm, settings.output_device)
        return result.pcm

    match args.command:
        case "voices":
            for locale in (THAI, ENGLISH):
                print(f"{locale}:")
                for voice in azure.list_voices(locale):
                    if locale == THAI or any(n in voice for n in ("Andrew", "Brian")):
                        print(f"  {voice}")

        case "tts":
            print(f"▶ {args.text}")
            speak(args.text, args.lang)

        case "roundtrip":
            cases = [c for c in ROUNDTRIP if args.lang in (None, c[0])]
            text_ok = lang_ok = raw_ok = 0
            for language, spoken, expected in cases:
                tts = azure.synthesize(spoken, language)
                if tts.status != "ok":
                    sys.exit(f"TTS ล้มเหลว: {tts.error}")
                stt = azure.transcribe(tts.pcm)
                fixed = correct(stt.text)
                same_text = stt.status == "ok" and _simplify(fixed) == _simplify(expected)
                same_lang = stt.language == language
                raw_ok += stt.status == "ok" and _simplify(stt.text) == _simplify(expected)
                text_ok += same_text
                lang_ok += same_lang
                mark = "✓" if same_text and same_lang else "✗"
                lang_note = "" if same_lang else f" ⚠ ตรวจภาษาได้ {stt.language}"
                fix_note = "" if fixed == stt.text else f"  [แก้จาก \"{stt.text}\"]"
                conf = f"{stt.confidence:.2f} · " if stt.confidence is not None else ""
                print(
                    f"  {mark} [{language}] {spoken:<34} → {fixed or stt.error}{fix_note}{lang_note}"
                    f"  ({conf}STT {stt.latency_ms} ms)"
                )
            total = len(cases)
            print(f"\nข้อความตรง: ก่อนแก้ {raw_ok}/{total} · หลังแก้ {text_ok}/{total} · ตรวจภาษาถูก {lang_ok}/{total}")

        case "stt" | "echo":
            input(f"กด Enter แล้วพูด (ไทยหรืออังกฤษ) ภายใน {args.seconds:g} วินาที ... ")
            print("● กำลังอัด")
            pcm = audio_io.record(args.seconds, settings.input_device)
            print(f"  ระดับเสียงสูงสุด {audio_io.peak_dbfs(pcm):.1f} dBFS")
            stt = azure.transcribe(pcm)
            if stt.status != "ok":
                print(f"  ✗ {stt.error} ({stt.latency_ms} ms)")
                return
            text = correct(stt.text)
            note = "" if text == stt.text else f" (แก้จาก \"{stt.text}\")"
            print(f"  [{stt.language}] \"{text}\"{note} ({stt.latency_ms} ms)")
            if args.command == "echo":
                reply = f"You said: {text}" if stt.language == ENGLISH else f"คุณพูดว่า {text}"
                speak(reply, stt.language)

        case "prompts":
            for language, prompts in sounds.PROMPTS.items():
                for name, text in prompts.items():
                    result = azure.synthesize(text, language)
                    if result.status != "ok":
                        sys.exit(f"TTS ล้มเหลว ({language}/{name}): {result.error}")
                    path = sounds.prompt_path(name, language)
                    audio_io.save_wav(result.pcm, path)
                    print(f"  ✓ {path.relative_to(sounds.SOUNDS_DIR)}  \"{text}\"")


if __name__ == "__main__":
    main()
