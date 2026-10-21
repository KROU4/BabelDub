from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from babeldub.adapters.fmod import pcm


def _snr(ref: np.ndarray, test: np.ndarray) -> float:
    return float(10 * np.log10(np.sum(ref**2) / np.sum((ref - test) ** 2)))


@pytest.mark.parametrize(
    ("sr_in", "sr_out"), [(24000, 48000), (48000, 24000), (44100, 48000), (22050, 44100)]
)
def test_resample_sine(sr_in: int, sr_out: int) -> None:
    f = 1000.0
    n = sr_in  # one second
    x = np.sin(2 * np.pi * f * np.arange(n) / sr_in)[:, None].astype(np.float32)
    y = pcm.resample(x, sr_in, sr_out)
    assert y.shape == (pcm.resampled_length(n, sr_in, sr_out), 1)
    ideal = np.sin(2 * np.pi * f * np.arange(y.shape[0]) / sr_out)
    mid = slice(sr_out // 10, -sr_out // 10)
    assert _snr(ideal[mid], y[mid, 0]) > 60


def test_resample_identity_and_lengths() -> None:
    x = np.ones((10, 2), np.float32)
    assert pcm.resample(x, 48000, 48000) is not None
    assert np.array_equal(pcm.resample(x, 48000, 48000), x)
    assert pcm.resampled_length(48000, 48000, 24000) == 24000
    assert pcm.resampled_length(1, 24000, 48000) == 2


def test_to_channels() -> None:
    mono = np.array([[0.5], [-0.5]], np.float32)
    assert pcm.to_channels(mono, 2).tolist() == [[0.5, 0.5], [-0.5, -0.5]]
    st = np.array([[1.0, 0.0], [0.0, 1.0]], np.float32)
    assert pcm.to_channels(st, 1).tolist() == [[0.5], [0.5]]
    six = pcm.to_channels(st, 6)
    assert six.shape == (2, 6) and six[:, 2:].sum() == 0


def test_load_for_subsound_round_trip_exact(tmp_path: Path) -> None:
    data = (np.arange(-500, 500, dtype=np.int16) * 30)[:, None]
    wav = tmp_path / "x.wav"
    sf.write(wav, data, 48000, subtype="PCM_16")
    out, sr, frames = pcm.load_for_subsound(wav, 1, 48000)
    assert (sr, frames) == (48000, 1000)
    assert np.array_equal(out, data)
    up, _, _ = pcm.load_for_subsound(wav, 2, 96000)
    assert up.shape == (2000, 2) and up.dtype == np.int16
