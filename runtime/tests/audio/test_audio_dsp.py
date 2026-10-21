"""DSP helpers on synthetic signals with known properties."""

from __future__ import annotations

import math

import numpy as np
import pytest

from babeldub.audio import dsp


def sine(freq: float, seconds: float, sr: int, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(round(seconds * sr))) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def dominant_freq(x: np.ndarray, sr: int) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.argmax(spec) * sr / len(x))


@pytest.mark.parametrize(("sr_in", "sr_out"), [(24000, 48000), (48000, 24000), (24000, 44100), (44100, 16000)])
def test_resample_keeps_tone_and_length(sr_in: int, sr_out: int) -> None:
    x = sine(1000.0, 2.0, sr_in)
    y = dsp.resample(x, sr_in, sr_out)
    assert y.dtype == np.float32
    assert len(y) == math.ceil(len(x) * sr_out / sr_in)
    assert dominant_freq(y, sr_out) == pytest.approx(1000.0, abs=2.0)
    middle = y[len(y) // 4 : 3 * len(y) // 4]
    assert float(np.max(np.abs(middle))) == pytest.approx(0.5, abs=0.01)


def test_resample_rejects_aliases_and_keeps_layout() -> None:
    sr = 48000
    tone = sine(15000.0, 1.0, sr, amp=1.0)  # above the 12 kHz Nyquist of 24 kHz
    down = dsp.resample(tone, sr, 24000)
    assert dsp.rms_db(down[500:-500]) < -60.0
    stereo = np.stack([sine(440, 0.5, sr), sine(880, 0.5, sr)], axis=1)
    out = dsp.resample(stereo, sr, 16000)
    assert out.shape == (8000, 2)
    assert dsp.resample(stereo, sr, sr).shape == stereo.shape


@pytest.mark.parametrize("sr", [48000, 44100])
def test_loudness_of_reference_sine(sr: int) -> None:
    # BS.1770: a 997 Hz sine of peak amplitude A on one channel reads 20*log10(A) - 3.01 LUFS
    x = sine(997.0, 3.0, sr, amp=0.5)
    assert dsp.loudness_lufs(x, sr) == pytest.approx(20 * math.log10(0.5) - 3.01, abs=0.1)
    stereo = np.stack([x, x], axis=1)
    assert dsp.loudness_lufs(stereo, sr) == pytest.approx(20 * math.log10(0.5), abs=0.1)


def test_loudness_gating_ignores_silence_and_handles_edges() -> None:
    sr = 48000
    x = sine(997.0, 10.0, sr, amp=0.5)
    padded = np.concatenate([np.zeros(sr * 3, np.float32), x, np.zeros(sr * 3, np.float32)])
    # silent blocks are gated out; only the few blocks straddling the edges pull the value down
    assert dsp.loudness_lufs(padded, sr) == pytest.approx(dsp.loudness_lufs(x, sr), abs=0.2)
    assert dsp.loudness_lufs(np.zeros(sr, np.float32), sr) == float("-inf")
    short = sine(997.0, 0.2, sr, amp=0.5)  # shorter than one 400 ms block: measured as one block
    assert dsp.loudness_lufs(short, sr) == pytest.approx(-9.03, abs=0.3)


def test_trim_silence_and_speech_seconds() -> None:
    sr = 24000
    x = np.concatenate([np.zeros(int(0.5 * sr)), sine(300, 1.0, sr, 0.3), np.zeros(int(0.3 * sr))]).astype(np.float32)
    trimmed, lead, trail = dsp.trim_silence(x, sr, pad_s=0.04)
    assert lead == pytest.approx(0.5, abs=0.011)
    assert trail == pytest.approx(0.3, abs=0.011)
    assert len(trimmed) / sr == pytest.approx(1.08, abs=0.02)
    assert dsp.speech_seconds(x, sr) == pytest.approx(1.0, abs=0.02)
    stereo = np.stack([x, x], axis=1)
    trimmed2, _, _ = dsp.trim_silence(stereo, sr)
    assert trimmed2.ndim == 2 and trimmed2.shape[1] == 2
    empty, lead0, trail0 = dsp.trim_silence(np.zeros(sr, np.float32), sr)
    assert len(empty) == 0 and lead0 == pytest.approx(1.0) and trail0 == 0.0


def test_quiet_take_is_not_treated_as_silence() -> None:
    sr = 16000
    quiet = np.concatenate([np.zeros(sr // 2), sine(200, 1.0, sr, amp=0.003)]).astype(np.float32)  # ~ -53 dBFS
    assert dsp.speech_seconds(quiet, sr) == pytest.approx(1.0, abs=0.02)


def test_match_loudness_hits_target_and_respects_ceiling() -> None:
    sr = 48000
    x = sine(997.0, 2.0, sr, amp=0.1)
    y = dsp.match_loudness(x, sr, -20.0, ceiling_db=-1.0)
    assert dsp.loudness_lufs(y, sr) == pytest.approx(-20.0, abs=0.1)
    loud = dsp.match_loudness(x, sr, 0.0, ceiling_db=-1.0)
    assert dsp.true_peak_db(loud, sr) <= -0.95
    short = sine(997.0, 0.2, sr, amp=0.1)
    z = dsp.match_loudness(short, sr, -25.0, measure="rms")
    assert dsp.rms_db(z) == pytest.approx(-25.0, abs=0.05)
    silent = np.zeros(1000, np.float32)
    assert np.array_equal(dsp.match_loudness(silent, sr, -20.0), silent)


def test_levels_peaks_and_helpers() -> None:
    sr = 24000
    x = sine(1000, 1.0, sr, amp=0.5)
    assert dsp.peak_db(x) == pytest.approx(20 * math.log10(0.5), abs=0.01)
    assert dsp.rms_db(x) == pytest.approx(20 * math.log10(0.5 / math.sqrt(2)), abs=0.05)
    assert dsp.true_peak_db(x, sr) >= dsp.peak_db(x) - 1e-6
    assert dsp.level_db(sine(1000, 0.2, sr), sr) == pytest.approx(dsp.rms_db(sine(1000, 0.2, sr)))
    assert dsp.level_db(x, sr) == pytest.approx(dsp.loudness_lufs(x, sr))
    faded = dsp.fade(np.ones(sr, np.float32), sr, ms=10.0)
    assert faded[0] == pytest.approx(0.0, abs=1e-6) and faded[-1] == pytest.approx(0.0, abs=1e-6)
    assert faded[sr // 2] == 1.0
    stereo = np.stack([x, -x], axis=1)
    assert dsp.to_mono(stereo).shape == (sr,)
    assert float(np.max(np.abs(dsp.to_mono(stereo)))) == pytest.approx(0.0, abs=1e-6)
    assert dsp.to_channels(x, 2).shape == (sr, 2)
    padded = dsp.pad_silence(x, sr, 0.5, 0.25)
    assert len(padded) == sr + sr // 2 + sr // 4
