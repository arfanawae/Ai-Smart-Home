"""Microphone capture and speaker playback. One format everywhere: 16 kHz, mono, 16-bit PCM."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2  # bytes (int16)


def record(seconds: float, device: int | None = None) -> bytes:
    audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=device)
    sd.wait()
    return audio.tobytes()


def play(pcm: bytes, device: int | None = None) -> None:
    if not pcm:
        return
    sd.play(np.frombuffer(pcm, dtype=np.int16), samplerate=SAMPLE_RATE, device=device)
    sd.wait()


def peak_dbfs(pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype=np.int16)
    peak = float(np.max(np.abs(samples.astype(np.int32)))) / 32768.0 if samples.size else 0.0
    return 20 * np.log10(max(peak, 1e-9))


SPECTRUM_BANDS = 24
_LOW_HZ, _HIGH_HZ = 90.0, 7600.0  # where speech energy is (16 kHz audio tops out at 8 kHz)


def spectrum(pcm: bytes, bands: int = SPECTRUM_BANDS, floor_db: float = -75.0, ceiling_db: float = -25.0) -> list[float]:
    """0–1 energy per log-spaced frequency band, low to high — the web UI's equaliser bars.

    Real audio only (mic frames or NEXUS's own voice); silence gives all zeros.
    """
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size < 64:
        return [0.0] * bands
    window = np.hanning(samples.size).astype(np.float32)
    magnitude = np.abs(np.fft.rfft(samples * window)) / (window.sum() / 2)
    freqs = np.fft.rfftfreq(samples.size, 1 / SAMPLE_RATE)
    edges = np.geomspace(_LOW_HZ, _HIGH_HZ, bands + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = magnitude[(freqs >= lo) & (freqs < hi)]
        if sel.size == 0:  # narrower than one FFT bin: take the nearest bin
            sel = magnitude[[int(np.argmin(np.abs(freqs - (lo + hi) / 2)))]]
        db = 20 * np.log10(max(float(np.sqrt(np.mean(sel**2))), 1e-9))
        out.append(round(min(max((db - floor_db) / (ceiling_db - floor_db), 0.0), 1.0), 3))
    return out


def loudness(pcm: bytes, floor_db: float = -60.0, ceiling_db: float = -10.0) -> float:
    """0–1 perceptual level for the UI waveform: RMS in dBFS mapped linearly between floor and ceiling."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return 0.0
    db = 20 * np.log10(max(float(np.sqrt(np.mean(samples**2))), 1e-9))
    return float(min(max((db - floor_db) / (ceiling_db - floor_db), 0.0), 1.0))


def envelope(pcm: bytes, chunk_ms: int = 60) -> list[float]:
    """Loudness per chunk — streamed to the UI while NEXUS speaks so the waveform follows its real voice."""
    chunk = SAMPLE_RATE * chunk_ms // 1000 * SAMPLE_WIDTH
    return [loudness(pcm[i : i + chunk]) for i in range(0, len(pcm), chunk)]


def save_wav(pcm: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(SAMPLE_WIDTH)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(pcm)


def load_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as f:
        if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, SAMPLE_WIDTH, SAMPLE_RATE):
            raise ValueError(f"{path} ต้องเป็น 16 kHz mono 16-bit")
        return f.readframes(f.getnframes())
