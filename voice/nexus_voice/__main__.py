"""Run NEXUS by voice.

    python -m nexus_voice              ไมค์จริง: "Nexus" → สั่งงาน → NEXUS ตอบด้วยเสียง (อุปกรณ์จำลอง)
    python -m nexus_voice simulate     ทดสอบทั้งลูปโดยไม่ใช้ไมค์: สร้างเสียง "Nexus" + คำสั่งด้วย Azure TTS
                                       แล้วป้อนเข้าลูปทีละเฟรม (ไม่เล่นเสียงออกลำโพง)
"""

from __future__ import annotations

import asyncio
import os
import sys

import sounddevice as sd
from nexus.cli import BOLD, CYAN, DIM, RESET, YELLOW, build_assistant, print_event
from nexus.config import load_settings as load_backend_settings
from nexus.events import AIState, Event, StateChanged

from . import audio_io
from .broadcast import DEFAULT_HOST, EventBroadcaster
from .config import VoiceSettings, load_settings
from .loop import Player, VoiceLoop
from .speech import ENGLISH, THAI, AzureSpeech
from .vad import FRAME_SAMPLES, frames
from .wakeword import VoskWakeWord

_ICONS = {
    AIState.IDLE: "💤",
    AIState.WAKE_DETECTED: "✨",
    AIState.LISTENING: "👂",
    AIState.TRANSCRIBING: "📝",
    AIState.SPEAKING: "🔊",
}

SIMULATION_SCRIPT = [
    (ENGLISH, "Hey NEXUS."),
    (THAI, "เปิดไฟห้องหลัก"),
    (THAI, "ไฟสว่างเกินไป"),
    (ENGLISH, "Is the light on right now?"),
    (THAI, "ขอบคุณ NEXUS"),
]


def show(event: Event) -> None:
    if isinstance(event, StateChanged) and event.state in _ICONS:
        extra = f" · {event.detail}" if event.detail else ""
        print(f"{_ICONS[event.state]} {DIM}{event.state.value}{extra}{RESET}")
    else:
        print_event(event)


async def build(play: Player | None = None) -> tuple[VoiceLoop, AzureSpeech, VoiceSettings, EventBroadcaster]:
    voice = load_settings()
    if not (voice.azure_key and voice.azure_region):
        sys.exit("ยังไม่ได้ใส่ AZURE_SPEECH_KEY / AZURE_SPEECH_REGION ใน backend/.env")
    backend = load_backend_settings()
    broadcaster = EventBroadcaster(
        simulated=True, llm=backend.llm_model if backend.gemini_api_key else None, languages=list(voice.stt_languages)
    )

    def emit(event: Event) -> None:
        show(event)
        broadcaster.publish(event)

    def emit_from_assistant(event: Event) -> None:
        # The assistant ends every turn with IDLE, but in voice mode the loop owns IDLE (it keeps listening).
        if not (isinstance(event, StateChanged) and event.state == AIState.IDLE):
            emit(event)

    assistant, driver = build_assistant(backend, emit=emit_from_assistant)
    broadcaster.set_device("main_light", (await driver.get_state("main_light")).to_dict())
    await broadcaster.start()
    azure = AzureSpeech(voice.azure_key, voice.azure_region, voice.stt_languages, voice.tts_voices, voice.stt_language_mode)

    async def speaker(pcm: bytes) -> None:
        await asyncio.to_thread(audio_io.play, pcm, voice.output_device)

    loop = VoiceLoop(assistant, azure, azure, VoskWakeWord(), play or speaker, emit)
    print(f"{CYAN}{BOLD}N E X U S{RESET}  {DIM}Voice · M1{RESET}")
    llm = f"Gemini · {backend.llm_model}" if assistant.llm else f"{YELLOW}ไม่มี (Fast path อย่างเดียว){RESET}"
    print(f"  อุปกรณ์: {YELLOW}โหมดจำลอง (MOCK){RESET} · LLM: {llm} · STT: {' + '.join(voice.stt_languages)}")
    print(f"  UI feed: ws://{DEFAULT_HOST}:{broadcaster.port}")
    return loop, azure, voice, broadcaster


async def run_live() -> None:
    loop, _azure, voice, _broadcaster = await build()
    aio = asyncio.get_running_loop()
    frames_in: asyncio.Queue[bytes] = asyncio.Queue()

    def on_audio(data, _frames, _time, _status) -> None:
        if not loop.speaking:  # half-duplex: ignore the mic while NEXUS talks
            aio.call_soon_threadsafe(frames_in.put_nowait, bytes(data))

    with sd.RawInputStream(
        samplerate=audio_io.SAMPLE_RATE,
        blocksize=FRAME_SAMPLES,
        channels=1,
        dtype="int16",
        device=voice.input_device,
        callback=on_audio,
    ):
        show(StateChanged(AIState.IDLE, 'พูด "Nexus" แล้วเว้นจังหวะสั้น ๆ ก่อนสั่งงาน (Ctrl+C เพื่อออก)'))
        while True:
            await loop.process_frame(await frames_in.get())


async def run_simulation() -> None:
    """End-to-end check without a microphone: synthetic speech in, assistant + TTS out (not played)."""
    spoken: list[bytes] = []

    async def capture(pcm: bytes) -> None:
        spoken.append(pcm)

    loop, azure, _voice, broadcaster = await build(play=capture)
    pause = b"\x00" * (audio_io.SAMPLE_RATE * audio_io.SAMPLE_WIDTH)  # 1 s
    for language, sentence in SIMULATION_SCRIPT:
        print(f"\n{DIM}── ป้อนเสียงจำลอง: [{language}] {sentence}{RESET}")
        tts = azure.synthesize(sentence, language)
        for frame in frames(pause + tts.pcm + pause):
            await loop.process_frame(frame)
    print(f"\n{DIM}NEXUS พูดออกไปทั้งหมด {len(spoken)} ครั้ง (ไม่ได้เล่นออกลำโพง) · สถานะสุดท้าย {loop.state.value}{RESET}")
    await broadcaster.stop()


def main() -> None:
    if sys.platform == "win32":
        os.system("")  # enable ANSI colours
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    command = sys.argv[1] if len(sys.argv) > 1 else "live"
    try:
        asyncio.run(run_simulation() if command == "simulate" else run_live())
    except KeyboardInterrupt:
        print("\nลาก่อนครับ")


if __name__ == "__main__":
    main()
