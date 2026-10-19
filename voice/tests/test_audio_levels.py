import numpy as np

from nexus_voice.audio_io import SAMPLE_RATE, envelope, loudness


def tone(amplitude: float, seconds: float = 0.06) -> bytes:
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    return (np.sin(2 * np.pi * 440 * t) * amplitude * 32767).astype(np.int16).tobytes()


def test_loudness_is_bounded_and_monotonic():
    assert loudness(b"") == 0.0
    assert loudness(b"\x00\x00" * 960) == 0.0
    quiet, speech, loud = loudness(tone(0.003)), loudness(tone(0.05)), loudness(tone(0.9))
    assert 0.0 <= quiet < speech < loud <= 1.0
    assert loud == 1.0


def test_envelope_has_one_value_per_chunk():
    pcm = tone(0.05, 0.6)  # 600 ms
    assert len(envelope(pcm, chunk_ms=60)) == 10
