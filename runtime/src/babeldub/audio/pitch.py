"""Median F0 of a speech clip (YIN, numpy only): enough to place a take in its character's register.

The CLI uses it to choose the RVC pitch shift (`vc.pitch=char|line`); the qc worker's pyin stays the
measure of ranges and contours.
"""

from __future__ import annotations

import math

import numpy as np

from babeldub.audio.dsp import resample, to_mono

SR = 16000
FRAME_S, HOP_S = 0.040, 0.010
F0_MIN_HZ, F0_MAX_HZ = 50.0, 1000.0
YIN_THRESHOLD = 0.15
ACTIVE_DB = 35.0  # frames quieter than the loudest frame by more than this are not speech
FLOOR_DB = -70.0  # digital silence and dither are never speech
MIN_VOICED_FRAMES = 10


def _frames(x: np.ndarray, frame: int, hop: int) -> np.ndarray:
    n = 1 + max(0, (x.size - frame) // hop)
    idx = np.arange(frame)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def f0_track(data: np.ndarray, sr: int, fmin: float = F0_MIN_HZ, fmax: float = F0_MAX_HZ) -> np.ndarray:
    """F0 in Hz of every voiced 10 ms frame (unvoiced and quiet frames are dropped)."""
    mono = to_mono(np.asarray(data, dtype=np.float32))
    if sr != SR:
        mono = resample(mono, sr, SR)
    frame, hop = int(FRAME_S * SR), int(HOP_S * SR)
    if mono.size < frame:
        return np.zeros(0, dtype=np.float64)
    frames = _frames(mono.astype(np.float64), frame, hop)
    frames = frames - frames.mean(axis=1, keepdims=True)
    energy = np.sqrt(np.mean(frames**2, axis=1))
    db = 20.0 * np.log10(np.maximum(energy, 1e-9))
    active = (db > db.max() - ACTIVE_DB) & (db > FLOOR_DB)
    tau_min, tau_max = max(2, int(SR / fmax)), min(frame // 2, int(SR / fmin))
    # YIN difference d(tau) = r(0) + r_shift(0) - 2 r(tau), from the autocorrelation via FFT
    w = frame // 2
    spec = np.fft.rfft(frames, n=2 * frame, axis=1)
    acf = np.fft.irfft(spec * np.conj(np.fft.rfft(frames[:, :w], n=2 * frame, axis=1)), axis=1)[:, : w + 1]
    sq = np.cumsum(frames**2, axis=1)
    sq = np.concatenate([np.zeros((sq.shape[0], 1)), sq], axis=1)
    taus = np.arange(w + 1)
    energy_shift = sq[:, taus + w] - sq[:, taus]
    diff = sq[:, w][:, None] + energy_shift - 2.0 * acf
    diff[:, 0] = 0.0
    cum = np.cumsum(diff[:, 1:], axis=1) / np.arange(1, w + 1)[None, :]
    cmnd = np.ones_like(diff)
    cmnd[:, 1:] = diff[:, 1:] / np.maximum(cum, 1e-12)
    out: list[float] = []
    for i in np.flatnonzero(active):
        row = cmnd[i, tau_min : tau_max + 1]
        below = np.flatnonzero(row < YIN_THRESHOLD)
        if below.size == 0:
            continue
        t = int(below[0])
        while t + 1 < row.size and row[t + 1] < row[t]:  # walk down to the local minimum
            t += 1
        tau = t + tau_min
        if 1 <= tau < cmnd.shape[1] - 1:  # parabolic interpolation of the minimum
            a, b, c = cmnd[i, tau - 1], cmnd[i, tau], cmnd[i, tau + 1]
            den = a - 2.0 * b + c
            shift = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
            tau_f = tau + float(np.clip(shift, -1.0, 1.0))
        else:
            tau_f = float(tau)
        out.append(SR / tau_f)
    return np.asarray(out, dtype=np.float64)


def f0_median_hz(data: np.ndarray, sr: int) -> float | None:
    """Median voiced F0 (Hz), None with fewer than MIN_VOICED_FRAMES voiced frames."""
    track = f0_track(data, sr)
    if track.size < MIN_VOICED_FRAMES:
        return None
    # one octave-error guard: keep the frames within an octave of the first median
    med = float(np.median(track))
    kept = track[(track > med / 2.0) & (track < med * 2.0)]
    return float(np.median(kept)) if kept.size >= MIN_VOICED_FRAMES else med


def semitones(target_hz: float | None, source_hz: float | None) -> float | None:
    """12 * log2(target / source); None when either is missing or not positive."""
    if not target_hz or not source_hz or target_hz <= 0 or source_hz <= 0:
        return None
    return 12.0 * math.log2(float(target_hz) / float(source_hz))
