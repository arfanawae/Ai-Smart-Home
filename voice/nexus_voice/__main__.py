"""Run NEXUS.

    python -m nexus_voice              เสียงจริง: "Nexus" → สั่งงาน → NEXUS ตอบด้วยเสียง + ฟีดหน้าเว็บ
    python -m nexus_voice serve        ไม่ใช้ไมค์/Azure: เปิดเฉพาะฟีดหน้าเว็บ (พิมพ์คุยและกดควบคุมไฟจากเว็บได้)
    python -m nexus_voice simulate     ทดสอบทั้งลูปเสียงโดยไม่ใช้ไมค์: ป้อนเสียงสังเคราะห์จาก Azure TTS

ทุกโหมดใช้อุปกรณ์จำลอง (MOCK) และเปิดฟีดที่ ws://127.0.0.1:8765 ให้หน้าเว็บ (frontend/)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from typing import Any

from collections.abc import AsyncIterator

import sounddevice as sd
from nexus.assistant import Assistant
from nexus.cli import BOLD, CYAN, DIM, RESET, YELLOW, build_assistant, print_event
from nexus.config import Settings as BackendSettings
from nexus.config import load_settings as load_backend_settings
from nexus.events import AIState, Event, StateChanged, UserMessage
from nexus.replies import to_speech

from . import audio_io
from .broadcast import DEFAULT_HOST, DEFAULT_PORT, EventBroadcaster
from .cloud_stt import CloudSTT, TranscriptionError
from .corrections import correct
from .config import VoiceSettings, load_settings
from .loop import Player, VoiceLoop
from .speech import ENGLISH, THAI, AzureSpeech
from .vad import FRAME_SAMPLES, UtteranceSegmenter, VadConfig, frames
from .wakeword import VoskWakeWord

_ICONS = {
    AIState.IDLE: "💤",
    AIState.WAKE_DETECTED: "✨",
    AIState.LISTENING: "👂",
    AIState.TRANSCRIBING: "📝",
    AIState.SPEAKING: "🔊",
}
LEVEL_EVERY_FRAMES = 2  # mic level to the UI every 60 ms
PLAYBACK_PIECE_BYTES = audio_io.SAMPLE_RATE * audio_io.SAMPLE_WIDTH * 60 // 1000  # 60 ms
RESULT_FLASH_S = 0.9  # how long SUCCESS/ERROR shows before the core returns to SPEAKING

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
    elif isinstance(event, UserMessage) and event.source == "text":
        print(f"{BOLD}คุณ ⌨{RESET} {event.text}")
    else:
        print_event(event)


BOARD_WATCH_INTERVAL_S = 1.0


class Core:
    """Assistant + UI feed, shared by every mode."""

    def __init__(self, backend: BackendSettings, languages: list[str]) -> None:
        self.backend = backend
        self.voice_loop: VoiceLoop | None = None
        self.broadcaster = EventBroadcaster(
            # NEXUS_WS_PORT lets tests run a second instance without touching the one you are using
            port=int(os.getenv("NEXUS_WS_PORT", DEFAULT_PORT)),
            on_message=self.on_client_message,
            simulated=True,
            llm=backend.llm_model if backend.gemini_api_key else None,
            languages=languages,
            devices_via=backend.device_backend,  # "mcp" | "local" — shown in the UI's system status
        )
        self.assistant: Assistant
        self.assistant, self.devices = build_assistant(backend, emit=self.emit_from_assistant)
        llm = self.assistant.llm
        self.broadcaster.update_info(llm=f"{llm.provider} · {llm.model}" if llm else None)
        self.cloud_stt = CloudSTT.from_settings(backend, quota=self.assistant.quota)  # voice from the web page
        self._watcher: asyncio.Task[None] | None = None

    async def start(self) -> None:
        try:
            await self.devices.start()  # spawns the MCP server when DEVICE_BACKEND=mcp
        except ConnectionError as e:
            sys.exit(f"{e} — ลองรัน: python -m nexus_mcp (ดูข้อความผิดพลาด) หรือใช้ DEVICE_BACKEND=local")
        self.broadcaster.update_info(simulated=self.devices.simulated)  # MOCK badge only when it is
        state = await self.devices.get_state("main_light")
        if state is not None:
            self.broadcaster.set_device("main_light", state.to_dict())
        await self.broadcaster.start()
        if not self.devices.simulated:
            self._watcher = asyncio.create_task(self._watch_board())

    async def _watch_board(self) -> None:
        """Heartbeat: the relay marks the board offline 2 s after its last poll; this pushes that (and
        any change made outside NEXUS) to the web UI within about a second more."""
        while True:
            await asyncio.sleep(BOARD_WATCH_INTERVAL_S)
            try:
                state = await self.devices.get_state("main_light")
            except Exception:
                logging.getLogger(__name__).exception("board status check failed")
                state = None
            shown = self.broadcaster.device("main_light")
            if state is not None:
                fresh = state.to_dict()
            elif shown and shown.get("online"):
                fresh = {**shown, "online": False}  # unreachable: keep the last known values, mark offline
            else:
                continue
            if shown is None or any(fresh.get(k) != shown.get(k) for k in ("online", "on", "brightness")):
                self.broadcaster.push_device("main_light", fresh)

    async def close(self) -> None:
        if self._watcher:
            self._watcher.cancel()
        await self.broadcaster.stop()
        await self.devices.close()

    def emit(self, event: Event) -> None:
        show(event)
        self.broadcaster.publish(event)

    def emit_from_assistant(self, event: Event) -> None:
        loop = self.voice_loop
        if loop and isinstance(event, StateChanged):
            # The assistant ends every turn with IDLE; with a voice loop running, the loop owns IDLE.
            if event.state == AIState.IDLE:
                return
            # Replies are spoken while the turn finishes, so SUCCESS/ERROR can arrive mid-sentence:
            # show it briefly, then return to SPEAKING so the core keeps moving with the voice.
            if loop.speaking and event.state in (AIState.SUCCESS, AIState.ERROR):
                self.emit(event)
                asyncio.get_running_loop().call_later(RESULT_FLASH_S, self._back_to_speaking)
                return
        self.emit(event)

    def _back_to_speaking(self) -> None:
        if self.voice_loop and self.voice_loop.speaking:
            self.emit(StateChanged(AIState.SPEAKING))

    async def on_client_message(self, message: dict[str, Any]) -> None:
        try:
            await self._client_turn(message)
        except Exception as e:  # runs as a background task: an error here would otherwise vanish silently
            logging.getLogger(__name__).exception("web turn failed")
            self.emit(StateChanged(AIState.ERROR, f"ทำคำสั่งไม่สำเร็จ ({type(e).__name__}: {e})"[:200]))

    async def _client_turn(self, message: dict[str, Any]) -> None:
        if message["type"] == "text":
            self.emit(UserMessage(message["text"], "text"))
            await self.assistant.handle(message["text"])  # typed turns are answered in text, not spoken
            if self.voice_loop:  # put the UI back on what the voice loop is actually doing
                self.emit(StateChanged(self.voice_loop.state))
        elif message["type"] == "command":
            await self.assistant.run_manual(message["tool"], message["args"])
        elif message["type"] == "audio":
            await self._browser_voice(message["wav"])

    async def _browser_voice(self, wav: bytes) -> None:
        """Speech recorded in the dashboard: transcribe (Groq/Gemini), run the normal turn, and send the
        reply back as text for the browser to speak (window.speechSynthesis, free)."""
        self.emit(StateChanged(AIState.TRANSCRIBING, "ถอดเสียงจากหน้าเว็บ"))
        try:
            heard = await self.cloud_stt.transcribe(wav)
        except TranscriptionError as e:
            self.emit(StateChanged(AIState.ERROR, f"ถอดเสียงไม่ได้: {e}"[:200]))
            return
        if not heard.text:
            self.emit(StateChanged(AIState.IDLE, "ไม่ได้ยินคำพูด ลองกดแล้วพูดใหม่"))
            return
        text = correct(heard.text)
        self.emit(UserMessage(text, "voice", heard.language))
        turn = await self.assistant.handle(text, heard.language)
        self.broadcaster.publish_speak(to_speech(turn.reply), heard.language)
        if self.voice_loop:
            self.emit(StateChanged(self.voice_loop.state))


async def start_core(languages: list[str]) -> Core:
    core = Core(load_backend_settings(), languages)
    try:
        await core.start()
    except OSError as e:
        port = os.getenv("NEXUS_WS_PORT", DEFAULT_PORT)
        sys.exit(
            f"เปิดฟีดหน้าเว็บที่พอร์ต {port} ไม่ได้ ({e.strerror or e}) — "
            "มี NEXUS อีกตัวเปิดอยู่แล้วหรือไม่? ปิดตัวเดิมก่อน หรือใช้พอร์ตอื่นด้วย NEXUS_WS_PORT"
        )
    return core


def banner(core: Core, mode: str, voice: VoiceSettings | None) -> None:
    print(f"{CYAN}{BOLD}N E X U S{RESET}  {DIM}{mode}{RESET}")
    llm = f"Gemini · {core.backend.llm_model}" if core.assistant.llm else f"{YELLOW}ไม่มี (Fast path อย่างเดียว){RESET}"
    stt = f" · STT: {' + '.join(voice.stt_languages)}" if voice else ""
    via = "MCP Server (stdio)" if core.backend.device_backend == "mcp" else "local"
    kind = f"{YELLOW}โหมดจำลอง (MOCK){RESET}" if core.devices.simulated else f"{CYAN}Arduino UNO Q (อุปกรณ์จริง){RESET}"
    print(f"  อุปกรณ์: {kind} ผ่าน {via} · LLM: {llm}{stt}")
    print(f"  หน้าเว็บ: ws://{DEFAULT_HOST}:{core.broadcaster.port}  (cd frontend && npm run dev)")


async def start_voice(play: Player | None = None) -> tuple[Core, VoiceLoop, AzureSpeech, VoiceSettings]:
    voice = load_settings()
    if not (voice.azure_key and voice.azure_region):
        sys.exit("ยังไม่ได้ใส่ AZURE_SPEECH_KEY / AZURE_SPEECH_REGION ใน backend/.env (หรือใช้โหมด serve)")
    core = await start_core(list(voice.stt_languages))
    azure = AzureSpeech(
        voice.azure_key, voice.azure_region, voice.stt_languages, voice.tts_voices, voice.stt_language_mode,
        segmentation_silence_ms=voice.vad_end_silence_ms,
    )
    await asyncio.to_thread(azure.warm_up)  # open the TTS connections now, not on the first reply

    async def speaker(chunks: AsyncIterator[bytes]) -> None:
        """Play audio as it arrives. write() returns as soon as the sound card's buffer has room, i.e.
        before the audio is heard — so each piece's level is scheduled for the moment it will actually
        play, keeping the UI's core and waveform in step with the voice."""
        aio = asyncio.get_running_loop()
        out = sd.RawOutputStream(samplerate=audio_io.SAMPLE_RATE, channels=1, dtype="int16", device=voice.output_device)
        out.start()
        heard_at = aio.time() + out.latency  # when the next written sample reaches the speaker
        try:
            async for chunk in chunks:
                for i in range(0, len(chunk), PLAYBACK_PIECE_BYTES):
                    piece = chunk[i : i + PLAYBACK_PIECE_BYTES]
                    heard_at = max(heard_at, aio.time() + out.latency)  # a gap (e.g. between sentences)
                    aio.call_at(
                        heard_at, core.broadcaster.publish_level,
                        audio_io.loudness(piece), "tts", audio_io.spectrum(piece),
                    )
                    heard_at += len(piece) / (audio_io.SAMPLE_RATE * audio_io.SAMPLE_WIDTH)
                    await asyncio.to_thread(out.write, piece)
        finally:
            await asyncio.to_thread(out.stop)  # waits for the buffered audio to finish playing
            out.close()
            aio.call_at(max(heard_at, aio.time()), core.broadcaster.publish_level, 0.0, "tts", [0.0] * audio_io.SPECTRUM_BANDS)

    segmenter = UtteranceSegmenter(VadConfig(end_silence_ms=voice.vad_end_silence_ms))
    loop = VoiceLoop(
        core.assistant, azure, azure, VoskWakeWord(), play or speaker, core.emit,
        segmenter=segmenter, window_s=voice.conversation_window_s,
    )
    core.voice_loop = loop
    return core, loop, azure, voice


async def run_live() -> None:
    core, loop, _azure, voice = await start_voice()
    banner(core, "Voice · M2", voice)
    aio = asyncio.get_running_loop()
    frames_in: asyncio.Queue[bytes] = asyncio.Queue()
    counter = 0

    def on_audio(data, _frames, _time, _status) -> None:
        nonlocal counter
        if loop.speaking:  # half-duplex: ignore the mic while NEXUS talks
            return
        frame = bytes(data)
        aio.call_soon_threadsafe(frames_in.put_nowait, frame)
        counter += 1
        if counter % LEVEL_EVERY_FRAMES == 0:
            aio.call_soon_threadsafe(
                core.broadcaster.publish_level, audio_io.loudness(frame), "mic", audio_io.spectrum(frame)
            )

    try:
        with sd.RawInputStream(
            samplerate=audio_io.SAMPLE_RATE,
            blocksize=FRAME_SAMPLES,
            channels=1,
            dtype="int16",
            device=voice.input_device,
            callback=on_audio,
        ):
            core.emit(StateChanged(AIState.IDLE, 'พูด "Nexus" แล้วเว้นจังหวะสั้น ๆ ก่อนสั่งงาน (Ctrl+C เพื่อออก)'))
            spoken = loop.utterances_spoken
            while True:
                await loop.process_frame(await frames_in.get())
                if loop.utterances_spoken != spoken:  # NEXUS just finished speaking: drop stale mic audio
                    spoken = loop.utterances_spoken
                    while not frames_in.empty():
                        frames_in.get_nowait()
    finally:
        await core.close()  # also stops the MCP server subprocess


async def run_serve() -> None:
    core = await start_core([])
    banner(core, "Serve · ไม่มีระบบเสียง", None)
    core.emit(StateChanged(AIState.IDLE, "โหมด serve — พิมพ์คุยหรือกดควบคุมไฟจากหน้าเว็บ"))
    try:
        await asyncio.Event().wait()
    finally:
        await core.close()


async def run_simulation() -> None:
    """End-to-end check without a microphone: synthetic speech in, assistant + TTS out (not played)."""
    spoken: list[bytes] = []

    async def capture(chunks: AsyncIterator[bytes]) -> None:
        spoken.append(b"".join([c async for c in chunks]))

    core, loop, azure, voice = await start_voice(play=capture)
    banner(core, "Simulate", voice)
    pause = b"\x00" * (audio_io.SAMPLE_RATE * audio_io.SAMPLE_WIDTH)  # 1 s
    for language, sentence in SIMULATION_SCRIPT:
        print(f"\n{DIM}── ป้อนเสียงจำลอง: [{language}] {sentence}{RESET}")
        tts = azure.synthesize(sentence, language)
        for frame in frames(pause + tts.pcm + pause):
            await loop.process_frame(frame)
            await asyncio.sleep(0)  # let the UI feed flush between frames
    print(f"\n{DIM}NEXUS พูดออกไปทั้งหมด {len(spoken)} ครั้ง (ไม่ได้เล่นออกลำโพง) · สถานะสุดท้าย {loop.state.value}{RESET}")
    await core.close()


def main() -> None:
    if sys.platform == "win32":
        os.system("")  # enable ANSI colours
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    command = sys.argv[1] if len(sys.argv) > 1 else "live"
    runners = {"live": run_live, "serve": run_serve, "simulate": run_simulation}
    if command not in runners:
        sys.exit(f"ใช้งาน: python -m nexus_voice [{'|'.join(runners)}]")
    try:
        asyncio.run(runners[command]())
    except KeyboardInterrupt:
        print("\nลาก่อนครับ")


if __name__ == "__main__":
    main()
