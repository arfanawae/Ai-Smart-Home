"""M1 ขั้น 1.1b — ทดสอบไมโครโฟนและลำโพงของ Acer Laptop

    python voice/test_audio.py --list            ดูรายการอุปกรณ์เสียง
    python voice/test_audio.py                   อัด 3 วินาที → วัดระดับเสียง → เล่นกลับ
    python voice/test_audio.py --input 1 --save  เลือกไมค์ และบันทึกไฟล์ .wav ไว้ฟังทีหลัง

อัดที่ 16 kHz mono 16-bit เพราะเป็นรูปแบบที่ Wake word และ Azure STT ใช้
"""

from __future__ import annotations

import argparse
import sys
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000
RECORDINGS_DIR = Path(__file__).resolve().parent / "recordings"

# Thresholds in dBFS (0 = loudest possible). Speech at ~50 cm should peak around -20 to -6 dBFS.
TOO_QUIET_PEAK_DB = -35.0
CLIPPING_PEAK = 0.99
NOISY_FLOOR_DB = -45.0


def dbfs(value: float) -> float:
    return 20 * np.log10(max(value, 1e-9))


def list_devices() -> None:
    default_in, default_out = sd.default.device
    print("อุปกรณ์เสียง (* = ค่าเริ่มต้น):")
    for index, dev in enumerate(sd.query_devices()):
        api = sd.query_hostapis(dev["hostapi"])["name"]
        kinds = []
        if dev["max_input_channels"] > 0:
            kinds.append("ไมค์" + (" *" if index == default_in else ""))
        if dev["max_output_channels"] > 0:
            kinds.append("ลำโพง" + (" *" if index == default_out else ""))
        print(f"  [{index:>2}] {dev['name'][:45]:<45} {api:<20} {' / '.join(kinds)}")


def check_rate(device: int | None, kind: str) -> None:
    check = sd.check_input_settings if kind == "input" else sd.check_output_settings
    try:
        check(device=device, samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    except sd.PortAudioError as e:
        sys.exit(f"อุปกรณ์ {device} ไม่รองรับ {SAMPLE_RATE} Hz mono ({e}) — ลองเลือกอุปกรณ์ในกลุ่ม MME ด้วย --list")


def analyse(audio: np.ndarray) -> list[str]:
    samples = audio.astype(np.float32).flatten() / 32768.0
    window = SAMPLE_RATE // 20  # 50 ms frames
    frames = samples[: len(samples) // window * window].reshape(-1, window)
    frame_rms = np.sqrt(np.mean(frames**2, axis=1))
    peak = float(np.max(np.abs(samples)))
    noise_floor = float(np.percentile(frame_rms, 10))  # quietest 10% ≈ background noise
    speech_level = float(np.percentile(frame_rms, 95))

    print("\nผลการวัด")
    print(f"  ระดับสูงสุด (peak)      {dbfs(peak):6.1f} dBFS")
    print(f"  ระดับเสียงพูด (RMS 95%)  {dbfs(speech_level):6.1f} dBFS")
    print(f"  เสียงรบกวนพื้นหลัง       {dbfs(noise_floor):6.1f} dBFS")
    print(f"  ระยะห่างเสียงพูด/รบกวน   {dbfs(speech_level) - dbfs(noise_floor):6.1f} dB")

    issues = []
    if peak >= CLIPPING_PEAK:
        issues.append("เสียงแตก (clipping) — ลดระดับไมค์ใน Windows Sound settings หรือพูดห่างขึ้น")
    if dbfs(peak) < TOO_QUIET_PEAK_DB:
        issues.append("เสียงเบาเกินไป — เพิ่มระดับไมค์ หรือเช็กว่าเลือกไมค์ถูกตัว (--list)")
    if dbfs(noise_floor) > NOISY_FLOOR_DB:
        issues.append("เสียงรบกวนพื้นหลังสูง — ปิดพัดลม/เพลง หรือพิจารณา USB Speakerphone")
    if dbfs(speech_level) - dbfs(noise_floor) < 15:
        issues.append("เสียงพูดดังกว่าเสียงรบกวนไม่มาก — Wake word และ STT อาจพลาดบ่อย")
    return issues


def save(audio: np.ndarray) -> Path:
    RECORDINGS_DIR.mkdir(exist_ok=True)
    path = RECORDINGS_DIR / f"test_{datetime.now():%Y%m%d_%H%M%S}.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(audio.tobytes())
    return path


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="ทดสอบไมค์และลำโพงสำหรับ NEXUS")
    parser.add_argument("--list", action="store_true", help="แสดงรายการอุปกรณ์เสียง")
    parser.add_argument("--input", type=int, help="หมายเลขไมค์ (ดูจาก --list)")
    parser.add_argument("--output", type=int, help="หมายเลขลำโพง (ดูจาก --list)")
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--no-playback", action="store_true", help="ไม่เล่นเสียงกลับ")
    parser.add_argument("--save", action="store_true", help="บันทึกไฟล์ .wav ไว้ใน voice/recordings/")
    args = parser.parse_args()

    if args.list:
        list_devices()
        return

    check_rate(args.input, "input")
    in_name = sd.query_devices(args.input, "input")["name"]
    print(f"ไมค์: {in_name}")
    input("กด Enter แล้วพูดประโยคทดสอบ เช่น \"Nexus เปิดไฟห้องหลัก\" ... ")
    print(f"● กำลังอัด {args.seconds:g} วินาที")
    audio = sd.rec(
        int(args.seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=args.input
    )
    sd.wait()

    issues = analyse(audio)
    print("\n" + ("\n".join(f"  ⚠ {i}" for i in issues) if issues else "  ✓ ระดับเสียงเหมาะสำหรับ Wake word และ STT"))

    if args.save:
        print(f"\nบันทึกไฟล์: {save(audio)}")
    if not args.no_playback:
        check_rate(args.output, "output")
        print(f"\n▶ เล่นกลับทาง: {sd.query_devices(args.output, 'output')['name']}")
        sd.play(audio, samplerate=SAMPLE_RATE, device=args.output)
        sd.wait()


if __name__ == "__main__":
    main()
