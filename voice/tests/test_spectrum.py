"""Equaliser bands for the web UI come from real audio only."""

import numpy as np

from nexus_voice.audio_io import SAMPLE_RATE, SPECTRUM_BANDS, spectrum


def tone(hz: float, seconds: float = 0.06, amplitude: float = 0.3) -> bytes:
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    return (np.sin(2 * np.pi * hz * t) * amplitude * 32767).astype(np.int16).tobytes()


def test_silence_is_flat():
    assert spectrum(b"\0" * 1920) == [0.0] * SPECTRUM_BANDS


def test_a_low_tone_lights_low_bands_and_a_high_tone_high_bands():
    low, high = spectrum(tone(200)), spectrum(tone(5000))
    assert len(low) == len(high) == SPECTRUM_BANDS
    assert int(np.argmax(low)) < SPECTRUM_BANDS // 3 < 2 * SPECTRUM_BANDS // 3 < int(np.argmax(high))
    assert all(0.0 <= v <= 1.0 for v in low + high)


def test_a_30_ms_mic_frame_is_enough():
    assert max(spectrum(tone(440, seconds=0.03))) > 0.5
