"""audio/fit.py: energy-VAD speech span, bounded tempo fit to the exact original length, conform."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from babeldub.audio import fit
from babeldub.audio.dsp import level_db
from babeldub.audio.io import read_audio, write_wav


def tone(seconds: float, sr: int, amp: float = 0.3, f0: float = 150.0) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * f0 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)


def padded(speech: np.ndarray, sr: int, lead: float, trail: float, ch: int = 1) -> np.ndarray:
    full = np.concatenate([np.zeros(int(lead * sr), np.float32), speech, np.zeros(int(trail * sr), np.float32)])
    return np.repeat(full[:, None], ch, axis=1)


def fake_stretcher(calls: list[float]):
    """Time stretch by plain resampling (pitch is irrelevant here): output = ratio x input duration."""

    def stretch(src: Path, dst: Path, ratio: float) -> str:
        calls.append(ratio)
        data, sr = read_audio(src)
        mono = data[:, 0]
        n = int(round(len(mono) * ratio))
        out = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono).astype(np.float32)
        write_wav(dst, out, sr, subtype="FLOAT")
        return "fake"

    return stretch


def test_speech_bounds_and_span() -> None:
    sr = 16000
    data = padded(tone(1.5, sr), sr, 0.4, 0.6)[:, 0]
    start, end = fit.speech_bounds(data, sr)
    assert start / sr == pytest.approx(0.4, abs=0.021) and end / sr == pytest.approx(1.9, abs=0.021)
    assert fit.speech_span(data, sr) == pytest.approx(1.5, abs=0.04)
    assert fit.speech_bounds(np.zeros(sr, np.float32), sr) is None
    assert fit.length_score(2.0, 2.0) == 0.0 and fit.length_score(1.0, 2.0) == fit.length_score(4.0, 2.0)


@pytest.mark.parametrize(("dub_s", "tempo"), [(2.4, 1.2), (2.1, 1.05), (1.0, 0.93), (2.02, 1.0)])
def test_fit_bounds_tempo_and_keeps_exact_length(dub_s: float, tempo: float) -> None:
    sr, o_sr = 24000, 48000
    orig = padded(tone(2.0, o_sr), o_sr, 0.3, 0.5, ch=2)
    calls: list[float] = []
    res = fit.fit_to_original(padded(tone(dub_s, sr, amp=0.05), sr, 0.1, 0.1), sr, orig, o_sr, tempo_min=0.93,
                              tempo_max=1.2, tolerance=0.03, ceiling_db=-1.0, stretcher=fake_stretcher(calls))
    assert res.tempo == pytest.approx(tempo, abs=1e-3)
    assert bool(calls) == (tempo != 1.0)  # within the tolerance the dub keeps its tempo
    assert res.audio.shape == orig.shape and res.exact and res.fits and res.sr == o_sr
    assert res.lead_s == pytest.approx(0.3, abs=0.021)  # speech starts where the original's does
    assert res.level_out == pytest.approx(res.level_ref, abs=0.6)  # matched to the original speech
    assert res.peak_db <= -0.99


def test_fit_never_cuts_speech() -> None:
    sr = 24000
    orig = padded(tone(1.0, sr), sr, 0.2, 0.2)
    res = fit.fit_to_original(padded(tone(3.0, sr), sr, 0.0, 0.0), sr, orig, sr, tempo_min=0.93, tempo_max=1.2,
                              tolerance=0.03, ceiling_db=-1.0, stretcher=fake_stretcher([]))
    assert not res.fits and not res.exact and res.lead_s == 0.0
    assert res.audio.shape[0] == pytest.approx(int(3.0 * sr / 1.2), abs=sr * 0.03)  # longer than the original
    # speech that only overruns the trail moves into the lead and keeps the exact length
    res = fit.fit_to_original(padded(tone(1.35, sr), sr, 0.0, 0.0), sr, orig, sr, tempo_min=1.0, tempo_max=1.0,
                              tolerance=0.03, ceiling_db=-1.0, stretcher=fake_stretcher([]))
    assert res.exact and res.fits and res.lead_s < 0.2


def test_fit_rejects_silence() -> None:
    sr = 16000
    with pytest.raises(ValueError):
        fit.fit_to_original(np.zeros(sr, np.float32), sr, padded(tone(1.0, sr), sr, 0.1, 0.1), sr, tempo_min=0.93,
                            tempo_max=1.2, tolerance=0.03, ceiling_db=-1.0, stretcher=fake_stretcher([]))


def test_conform_matches_length_rate_channels_and_level() -> None:
    ref = padded(tone(1.0, 48000), 48000, 0.2, 0.3, ch=2)
    converted = tone(1.5, 40000, amp=0.02)  # 40 kHz RVC output, a little longer, much quieter
    level = level_db(ref[int(0.2 * 48000) : int(1.2 * 48000), 0], 48000, "lufs")
    out, peak = fit.conform(converted, 40000, ref, 48000, level_ref=level, measure="lufs", ceiling_db=-1.0)
    assert out.shape == ref.shape and peak <= -0.99
    bounds = fit.speech_bounds(out[:, 0], 48000)
    assert bounds is not None
    assert level_db(out[bounds[0] : bounds[1], 0], 48000, "lufs") == pytest.approx(level, abs=0.6)
