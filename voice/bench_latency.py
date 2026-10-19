"""วัดความหน่วงตั้งแต่ "ผู้ใช้พูดจบ" จนถึง "NEXUS เริ่มพูด" ของลูปเสียงจริง

    python bench_latency.py            ทุกประโยค (ใช้ Gemini ~7 request ต่อรอบ)
    python bench_latency.py --fast     เฉพาะประโยคที่ไม่ต้องใช้ Gemini

เสียงผู้ใช้สร้างด้วย Azure TTS แล้วป้อนเข้า VoiceLoop ทีละเฟรม 30 ms ตามเวลาจริง (เหมือนพูดใส่ไมค์)
ผ่าน STT / Assistant (Gemini + อุปกรณ์จำลอง) / TTS ของจริง แต่ไม่เล่นเสียงออกลำโพง และไม่เปิดฟีดหน้าเว็บ

แยกเวลาเป็น: รอ VAD (เงียบจนตัดประโยค) · STT · คิด (fast path/Gemini + อุปกรณ์) · TTS จนได้เสียงชิ้นแรก
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

from nexus.cli import build_assistant
from nexus.config import load_settings as load_backend_settings
from nexus.events import AIState, Event, StateChanged, UserMessage

from nexus_voice import audio_io
from nexus_voice.config import load_settings
from nexus_voice.loop import VoiceLoop
from nexus_voice.speech import ENGLISH, THAI, AzureSpeech
from nexus_voice.vad import FRAME_MS, UtteranceSegmenter, VadConfig, frames
from nexus_voice.wakeword import VoskWakeWord

CASES = [
    (THAI, "เปิดไฟห้องหลัก", False),
    (THAI, "ตอนนี้ไฟสว่างเท่าไหร่", True),
    (ENGLISH, "Turn off the main light.", True),
    (ENGLISH, "Is the light on right now?", True),
    (THAI, "ไฟสว่างเกินไป", True),
    (THAI, "วันนี้ร้อนจัง", True),
]


def voiced_bytes(pcm: bytes) -> int:
    """Where the speech really ends: TTS clips carry trailing silence that must not count as speech."""
    chunks = frames(pcm)
    last = max((i for i, f in enumerate(chunks) if audio_io.loudness(f) > 0.2), default=len(chunks) - 1)
    return (last + 1) * len(chunks[0])


class Clock:
    def __init__(self) -> None:
        self.marks: dict[str, float] = {}
        self.route = ""

    def reset(self) -> None:
        self.marks.clear()
        self.route = ""

    def on_event(self, event: Event) -> None:
        now = time.perf_counter()
        if isinstance(event, StateChanged):
            if event.state == AIState.TRANSCRIBING:
                self.marks.setdefault("vad_end", now)
            elif event.state == AIState.SPEAKING:
                self.marks.setdefault("first_audio", now)
        elif isinstance(event, UserMessage):
            self.marks.setdefault("stt_done", now)
        elif type(event).__name__ == "AssistantReply":
            self.route = getattr(event, "route", "")


async def run(fast_only: bool, rounds: int) -> None:
    voice = load_settings()
    backend = load_backend_settings()
    clock = Clock()
    assistant, _driver = build_assistant(backend, emit=clock.on_event)
    azure = AzureSpeech(
        voice.azure_key, voice.azure_region, voice.stt_languages, voice.tts_voices, voice.stt_language_mode,
        segmentation_silence_ms=voice.vad_end_silence_ms,
    )
    azure.warm_up()
    played: list[bytes] = []

    async def silent_player(chunks) -> None:  # no speaker: we only time when audio is ready
        played.append(b"".join([c async for c in chunks]))

    segmenter = UtteranceSegmenter(VadConfig(end_silence_ms=voice.vad_end_silence_ms))
    loop = VoiceLoop(assistant, azure, azure, VoskWakeWord(), silent_player, clock.on_event, segmenter=segmenter)
    vad_wait = loop._segmenter.config.end_silence_ms / 1000
    pre = b"\x00" * (audio_io.SAMPLE_RATE * audio_io.SAMPLE_WIDTH // 2)  # 0.5 s
    post = b"\x00" * (audio_io.SAMPLE_RATE * audio_io.SAMPLE_WIDTH * 2)  # 2 s
    cases = [c for c in CASES if not (fast_only and c[2])]
    clips = {sentence: azure.synthesize(sentence, language).pcm for language, sentence, _ in cases}
    azure.transcribe(clips[cases[0][1]])  # warm-up so the first case isn't penalised for DNS/TLS setup

    totals: dict[str, list[float]] = {}
    print(f"VAD end-of-speech: {vad_wait * 1000:.0f} ms · rounds: {rounds}\n")
    print(f"{'ประโยค':<30}{'ทาง':<10}{'VAD':>7}{'STT':>7}{'คิด+TTS':>9}{'รวม':>8}")
    for _ in range(rounds):
        for language, sentence, _uses_llm in cases:
            clock.reset()
            loop.start_listening()
            clip = clips[sentence]
            start = time.perf_counter()
            speech_end = start + (len(pre) + voiced_bytes(clip)) / (audio_io.SAMPLE_RATE * audio_io.SAMPLE_WIDTH)
            for i, frame in enumerate(frames(pre + clip + post)):
                await loop.process_frame(frame)
                if "first_audio" in clock.marks:
                    break
                # real-time pacing, as if spoken into the microphone
                delay = start + (i + 1) * FRAME_MS / 1000 - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
            m = clock.marks
            if not {"vad_end", "stt_done", "first_audio"} <= m.keys():
                print(f"{sentence:<30} ✗ ไม่ครบ: {sorted(m)}")
                continue
            parts = {
                "VAD": m["vad_end"] - speech_end,
                "STT": m["stt_done"] - m["vad_end"],
                "think+TTS": m["first_audio"] - m["stt_done"],
                "total": m["first_audio"] - speech_end,
            }
            for k, v in parts.items():
                totals.setdefault(f"{clock.route}:{k}", []).append(v)
                totals.setdefault(f"all:{k}", []).append(v)
            print(
                f"{sentence[:28]:<30}{clock.route:<10}{parts['VAD']:>7.2f}{parts['STT']:>7.2f}"
                f"{parts['think+TTS']:>9.2f}{parts['total']:>8.2f}"
            )
            await asyncio.sleep(0.3)
    print("\nค่ามัธยฐาน (วินาที):")
    for route in ("fastpath", "llm", "all"):
        if f"{route}:total" in totals:
            row = " · ".join(f"{k} {statistics.median(totals[f'{route}:{k}']):.2f}" for k in ("VAD", "STT", "think+TTS", "total"))
            print(f"  {route:<9} {row}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="เฉพาะประโยคที่ไม่ใช้ Gemini")
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(run(args.fast, args.rounds))


if __name__ == "__main__":
    main()
