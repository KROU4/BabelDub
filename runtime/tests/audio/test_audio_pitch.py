from __future__ import annotations

import numpy as np
import pytest

from babeldub.audio.pitch import f0_median_hz, semitones


def voice(f0: float, sr: int = 44100, seconds: float = 1.5, vibrato: float = 0.04) -> np.ndarray:
    t = np.arange(int(sr * seconds)) / sr
    phase = 2 * np.pi * np.cumsum(f0 * (1 + vibrato * np.sin(2 * np.pi * 4 * t))) / sr
    return (0.3 * sum(np.sin(k * phase) / k for k in range(1, 8))).astype(np.float32)


@pytest.mark.parametrize("f0", [85.0, 150.0, 240.0, 420.0])
def test_median_of_harmonic_voices(f0: float) -> None:
    got = f0_median_hz(voice(f0), 44100)
    assert got is not None and abs(got / f0 - 1) < 0.01


def test_stereo_and_other_rates() -> None:
    mono = voice(200.0, sr=48000)
    got = f0_median_hz(np.stack([mono, mono], axis=1), 48000)
    assert got is not None and abs(got - 200.0) < 2.0


def test_unvoiced_and_short_clips_have_no_median() -> None:
    rng = np.random.default_rng(0)
    assert f0_median_hz(np.zeros(44100, dtype=np.float32), 44100) is None
    assert f0_median_hz(voice(150.0, seconds=0.05), 44100) is None
    noise = f0_median_hz((0.2 * rng.standard_normal(44100)).astype(np.float32), 44100)
    assert noise is None or noise > 0  # white noise may yield a few spurious frames, never a crash


def test_semitones() -> None:
    assert semitones(220.0, 110.0) == pytest.approx(12.0)
    assert semitones(None, 110.0) is None and semitones(100.0, 0.0) is None
