from nexus_voice.vad import FRAME_BYTES, FRAME_MS, UtteranceSegmenter, VadConfig, frames

SPEECH = b"\x01" * FRAME_BYTES
SILENCE = b"\x00" * FRAME_BYTES


def segmenter(**overrides):
    return UtteranceSegmenter(VadConfig(**overrides), is_speech=lambda f: f == SPEECH)


def feed(seg, sequence):
    return [u for u in (seg.push(f) for f in sequence) if u is not None]


def test_one_utterance_with_pre_roll():
    seg = segmenter()
    sequence = [SILENCE] * 20 + [SPEECH] * 30 + [SILENCE] * 30
    [utterance] = feed(seg, sequence)
    n_frames = len(utterance) // FRAME_BYTES
    # pre-roll (10 frames) + speech after trigger + at least end_silence_ms of trailing silence
    assert n_frames == 10 + (30 - 6) + -(-VadConfig().end_silence_ms // FRAME_MS)
    assert utterance.startswith(SILENCE * 4)  # pre-roll keeps the moment before speech was confirmed


def test_short_click_does_not_start_speech():
    seg = segmenter()
    assert feed(seg, [SILENCE] * 5 + [SPEECH] * 3 + [SILENCE] * 50) == []
    assert not seg.in_speech


def test_short_pause_inside_a_sentence_does_not_split_it():
    seg = segmenter()
    sequence = [SPEECH] * 20 + [SILENCE] * 15 + [SPEECH] * 20 + [SILENCE] * 30  # 450 ms pause < 800 ms
    assert len(feed(seg, sequence)) == 1


def test_two_utterances():
    seg = segmenter()
    sequence = ([SPEECH] * 20 + [SILENCE] * 30) * 2
    assert len(feed(seg, sequence)) == 2


def test_max_length_cuts_a_never_ending_utterance():
    seg = segmenter(max_utterance_ms=3000)
    [utterance] = feed(seg, [SPEECH] * 200)[:1]
    assert len(utterance) // FRAME_BYTES * FRAME_MS == 3000


def test_frames_splits_whole_frames_only():
    assert len(frames(b"\x00" * (FRAME_BYTES * 3 + 100))) == 3


def test_hearing_voice_before_speech_is_confirmed():
    seg = segmenter()
    feed(seg, [SILENCE] * 5 + [SPEECH] * 2)
    assert not seg.in_speech and seg.hearing_voice
    feed(seg, [SILENCE] * 10)
    assert not seg.hearing_voice
