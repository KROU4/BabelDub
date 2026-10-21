"""Time-stretch through ffmpeg (atempo and, if compiled in, the rubberband filter)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from babeldub.audio import stretch
from babeldub.audio.io import probe, read_audio, write_wav
from babeldub.core.output import EXIT_USAGE, BabelDubError
from babeldub.core.tools import find_tool

HAS_FFMPEG = find_tool("ffmpeg") is not None


def tone_file(path: Path, seconds: float = 2.0, sr: int = 24000, freq: float = 220.0) -> Path:
    t = np.arange(int(seconds * sr)) / sr
    data = 0.4 * np.sin(2 * np.pi * freq * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    return write_wav(path, data.astype(np.float32), sr)


def dominant_freq(x: np.ndarray, sr: int) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.argmax(spec) * sr / len(x))


def test_atempo_chain_stays_in_range() -> None:
    for tempo in (0.2, 0.5, 0.9, 1.0, 1.3, 2.0, 3.7, 9.0):
        chain = stretch.atempo_chain(tempo)
        assert all(0.5 <= f <= 2.0 for f in chain)
        assert np.prod(chain) == pytest.approx(tempo)


def test_ratio_one_copies_and_bad_ratio_fails(tmp_path: Path) -> None:
    src = tone_file(tmp_path / "in.wav")
    assert stretch.time_stretch(src, tmp_path / "same.wav", 1.0) == "copy"
    assert (tmp_path / "same.wav").read_bytes() == src.read_bytes()
    with pytest.raises(BabelDubError) as err:
        stretch.time_stretch(src, tmp_path / "bad.wav", 10.0)
    assert err.value.code == EXIT_USAGE


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.parametrize("ratio", [0.88, 0.7, 1.15])
def test_atempo_changes_duration_not_pitch(tmp_path: Path, ratio: float) -> None:
    src = tone_file(tmp_path / "in.wav")
    out = tmp_path / f"out_{ratio}.wav"
    assert stretch.time_stretch(src, out, ratio, method="atempo") == "atempo"
    assert probe(out)["dur_s"] == pytest.approx(2.0 * ratio, rel=0.03)
    data, sr = read_audio(out)
    assert dominant_freq(data[:, 0], sr) == pytest.approx(220.0, abs=3.0)


@pytest.mark.skipif("ffmpeg-rubberband" not in stretch.available_methods(), reason="ffmpeg without librubberband")
def test_ffmpeg_rubberband_filter(tmp_path: Path) -> None:
    src = tone_file(tmp_path / "in.wav")
    out = tmp_path / "rb.wav"
    assert stretch.time_stretch(src, out, 0.9, method="ffmpeg-rubberband") == "ffmpeg-rubberband"
    assert probe(out)["dur_s"] == pytest.approx(1.8, rel=0.03)


def test_default_prefers_best_available(tmp_path: Path) -> None:
    methods = stretch.available_methods()
    if not methods:
        with pytest.raises(BabelDubError):
            stretch.time_stretch(tone_file(tmp_path / "in.wav"), tmp_path / "o.wav", 0.9)
        return
    used = stretch.time_stretch(tone_file(tmp_path / "in.wav"), tmp_path / "o.wav", 0.9)
    assert used == methods[0]
