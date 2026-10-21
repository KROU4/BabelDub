"""Pure-numpy DSP used by post-processing and QA (contract §7).

Shapes: functions accept mono [frames] or multi-channel [frames, channels] float arrays and
return the same layout they were given (except `to_mono`, which always returns [frames]).
No scipy: resampling is a polyphase windowed-sinc filter, K-weighting is applied in the
frequency domain (exact biquad response, zero initial state).
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

SILENCE_DB = -120.0  # floor used where a level of an all-zero signal is needed as a number
LUFS_MIN_SECONDS = 0.4  # one BS.1770 gating block; shorter clips are levelled by RMS


# ---- layout helpers -------------------------------------------------------------------
def _as2d(data: np.ndarray) -> tuple[np.ndarray, bool]:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim == 1:
        return arr[:, None], True
    if arr.ndim != 2:
        raise ValueError(f"audio must be [frames] or [frames, channels], got shape {arr.shape}")
    return arr, False


def _restore(arr: np.ndarray, was_1d: bool) -> np.ndarray:
    out = arr[:, 0] if was_1d else arr
    return np.ascontiguousarray(out, dtype=np.float32)


def to_mono(data: np.ndarray) -> np.ndarray:
    """Average channels; always returns a 1-D float32 array."""
    arr, _ = _as2d(data)
    return np.ascontiguousarray(arr.mean(axis=1), dtype=np.float32)


def to_channels(data: np.ndarray, channels: int) -> np.ndarray:
    """Return [frames, channels]: mono is duplicated, other layouts are downmixed then duplicated."""
    arr, _ = _as2d(data)
    if arr.shape[1] == channels:
        return np.ascontiguousarray(arr, dtype=np.float32)
    mono = arr.mean(axis=1)
    return np.ascontiguousarray(np.repeat(mono[:, None], channels, axis=1), dtype=np.float32)


# ---- resampling -----------------------------------------------------------------------
@lru_cache(maxsize=32)
def _polyphase_bank(up: int, down: int, zeros: int = 16, beta: float = 8.6, rolloff: float = 0.94):
    """Kaiser-windowed sinc low-pass at the upsampled rate, split into `up` phases."""
    factor = max(up, down)
    cutoff = 0.5 * rolloff / factor  # cycles per sample at rate sr_in * up
    half = int(math.ceil(zeros * factor / rolloff))
    n = np.arange(-half, half + 1, dtype=np.float64)
    h = 2.0 * cutoff * np.sinc(2.0 * cutoff * n) * np.kaiser(2 * half + 1, beta)
    h *= up / h.sum()  # exact DC gain after zero-stuffing
    taps = int(math.ceil(len(h) / up))
    padded = np.zeros(taps * up, dtype=np.float64)
    padded[: len(h)] = h
    bank = padded.reshape(taps, up).T.copy()  # bank[p, k] = h[p + k*up]
    return bank, half, taps


def resample(data: np.ndarray, sr_in: int, sr_out: int, quality: int = 16) -> np.ndarray:
    """Band-limited rational resampling (polyphase FIR; quality = sinc zero crossings per side,
    16 gives ~-100 dB aliasing, 6 is enough for peak estimation)."""
    sr_in, sr_out = int(sr_in), int(sr_out)
    arr, was_1d = _as2d(data)
    if sr_in == sr_out or arr.shape[0] == 0:
        return _restore(arr.copy(), was_1d)
    g = math.gcd(sr_in, sr_out)
    up, down = sr_out // g, sr_in // g
    bank, half, taps = _polyphase_bank(up, down, zeros=int(quality))
    n_in = arr.shape[0]
    n_out = int(math.ceil(n_in * up / down))
    pad_l, pad_r = taps, taps + 2
    out = np.empty((n_out, arr.shape[1]), dtype=np.float32)
    k = np.arange(taps)
    chunk = max(1024, 1_500_000 // max(taps, 1))
    for c in range(arr.shape[1]):
        xpad = np.concatenate([np.zeros(pad_l), arr[:, c].astype(np.float64), np.zeros(pad_r)])
        for start in range(0, n_out, chunk):
            n = np.arange(start, min(n_out, start + chunk), dtype=np.int64)
            t = n * down + half
            phase = t % up
            base = t // up
            idx = base[:, None] - k[None, :] + pad_l
            out[start : start + len(n), c] = np.einsum("nk,nk->n", bank[phase], xpad[idx])
    return _restore(out, was_1d)


# ---- silence / speech -----------------------------------------------------------------
def _frame_db(mono: np.ndarray, hop: int) -> np.ndarray:
    n_frames = int(math.ceil(len(mono) / hop))
    padded = np.zeros(n_frames * hop, dtype=np.float64)
    padded[: len(mono)] = mono
    rms = np.sqrt(np.mean(padded.reshape(n_frames, hop) ** 2, axis=1))
    return 20.0 * np.log10(rms + 1e-12)


def voiced_bounds(data: np.ndarray, sr: int, threshold_db: float = -45.0) -> tuple[int, int] | None:
    """(start, end) sample indices of the region between the first and last frame above threshold.

    10 ms frames. A recording whose loudest frame is below `threshold_db` is judged relative to
    its own maximum (max - 30 dB), so very quiet takes are not treated as silence.
    Returns None for digital silence.
    """
    mono = to_mono(data)
    if len(mono) == 0:
        return None
    hop = max(1, int(round(sr * 0.01)))
    db = _frame_db(mono, hop)
    top = float(db.max())
    if top < -100.0:
        return None
    thr = threshold_db if top >= threshold_db else top - 30.0
    voiced = np.flatnonzero(db > thr)
    if voiced.size == 0:
        return None
    start = int(voiced[0]) * hop
    end = min(len(mono), (int(voiced[-1]) + 1) * hop)
    return start, end


def trim_silence(
    data: np.ndarray, sr: int, threshold_db: float = -45.0, pad_s: float = 0.04
) -> tuple[np.ndarray, float, float]:
    """Cut leading/trailing silence, keeping up to `pad_s` of it on each side.

    Returns (trimmed, lead_s, trail_s) where lead_s/trail_s are the silence durations detected
    at the edges of the input (before padding). Digital silence gives an empty array.
    """
    arr, was_1d = _as2d(data)
    n = arr.shape[0]
    bounds = voiced_bounds(arr, sr, threshold_db)
    if bounds is None:
        return _restore(arr[:0], was_1d), n / sr if sr else 0.0, 0.0
    start, end = bounds
    pad = int(round(pad_s * sr))
    trimmed = arr[max(0, start - pad) : min(n, end + pad)]
    return _restore(trimmed.copy(), was_1d), start / sr, (n - end) / sr


def speech_seconds(data: np.ndarray, sr: int, threshold_db: float = -45.0) -> float:
    """Duration from the first to the last voiced frame (edge silence excluded)."""
    bounds = voiced_bounds(data, sr, threshold_db)
    if bounds is None:
        return 0.0
    return (bounds[1] - bounds[0]) / sr


# ---- levels ---------------------------------------------------------------------------
def _k_weighting(sr: int) -> list[tuple[list[float], list[float]]]:
    """BS.1770 pre-filter + RLB high-pass for any sample rate (libebur128 formulation)."""
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / sr)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh**0.4996667741545416
    a0 = 1.0 + k / q + k * k
    shelf_b = [(vh + vb * k / q + k * k) / a0, 2.0 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0]
    shelf_a = [1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / sr)
    a0 = 1.0 + k / q + k * k
    hp_b = [1.0, -2.0, 1.0]
    hp_a = [1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0]
    return [(shelf_b, shelf_a), (hp_b, hp_a)]


def _apply_k_weighting(arr: np.ndarray, sr: int) -> np.ndarray:
    n = arr.shape[0]
    tail = int(0.5 * sr)  # IIR tails decay far below 1e-6 within this padding
    nfft = 1 << int(math.ceil(math.log2(n + tail)))
    w = 2.0 * np.pi * np.arange(nfft // 2 + 1) / nfft
    z1 = np.exp(-1j * w)
    z2 = z1 * z1
    resp = np.ones_like(z1)
    for b, a in _k_weighting(sr):
        resp *= (b[0] + b[1] * z1 + b[2] * z2) / (a[0] + a[1] * z1 + a[2] * z2)
    spec = np.fft.rfft(arr.astype(np.float64), n=nfft, axis=0)
    return np.fft.irfft(spec * resp[:, None], n=nfft, axis=0)[:n]


def _channel_weights(channels: int) -> np.ndarray:
    if channels == 6:  # L R C LFE Ls Rs
        return np.array([1.0, 1.0, 1.0, 0.0, 1.41, 1.41])
    if channels == 5:  # L R C Ls Rs
        return np.array([1.0, 1.0, 1.0, 1.41, 1.41])
    return np.ones(channels)


def loudness_lufs(data: np.ndarray, sr: int) -> float:
    """Integrated loudness per ITU-R BS.1770-4 (K-weighting, 400 ms blocks, 75 % overlap,
    absolute -70 LUFS and relative -10 LU gates). Clips shorter than one block are measured
    as a single block. Returns -inf for silence."""
    arr, _ = _as2d(data)
    n = arr.shape[0]
    if n == 0:
        return float("-inf")
    y = _apply_k_weighting(arr, sr)
    weights = _channel_weights(arr.shape[1])
    block = int(round(0.4 * sr))
    step = int(round(0.1 * sr))
    energy = np.concatenate([np.zeros((1, arr.shape[1])), np.cumsum(y * y, axis=0)])
    if n < block:
        z = (energy[-1] / n)[None, :]
    else:
        starts = np.arange(0, n - block + 1, step)
        z = (energy[starts + block] - energy[starts]) / block
    power = z @ weights
    with np.errstate(divide="ignore"):
        levels = -0.691 + 10.0 * np.log10(power)
    gated = levels > -70.0
    if not gated.any():
        return float("-inf")
    relative = -0.691 + 10.0 * math.log10(float(power[gated].mean())) - 10.0
    gated &= levels > relative
    return float(-0.691 + 10.0 * math.log10(float(power[gated].mean())))


def rms_db(data: np.ndarray) -> float:
    """RMS level in dBFS over all samples and channels (-inf for silence)."""
    arr, _ = _as2d(data)
    if arr.size == 0:
        return float("-inf")
    ms = float(np.mean(arr.astype(np.float64) ** 2))
    return 10.0 * math.log10(ms) if ms > 0 else float("-inf")


def level_db(data: np.ndarray, sr: int, measure: str = "auto") -> float:
    """Loudness used for matching: LUFS for clips ≥ 0.4 s (or measure='lufs'), else RMS dBFS."""
    if measure == "auto":
        frames = np.asarray(data).shape[0]
        measure = "lufs" if frames >= LUFS_MIN_SECONDS * sr else "rms"
    if measure == "lufs":
        return loudness_lufs(data, sr)
    if measure == "rms":
        return rms_db(data)
    raise ValueError(f"unknown loudness measure '{measure}'")


def peak_db(data: np.ndarray) -> float:
    """Sample peak in dBFS (-inf for silence)."""
    arr = np.asarray(data)
    if arr.size == 0:
        return float("-inf")
    peak = float(np.max(np.abs(arr)))
    return 20.0 * math.log10(peak) if peak > 0 else float("-inf")


def true_peak_db(data: np.ndarray, sr: int, oversample: int = 4) -> float:
    """BS.1770 true peak estimate: sample peak of the 4x oversampled signal."""
    arr, _ = _as2d(data)
    if arr.size == 0:
        return float("-inf")
    over = resample(arr, sr, sr * oversample, quality=6)
    return max(peak_db(arr), peak_db(over))


def match_loudness(
    data: np.ndarray, sr: int, target_lufs: float, ceiling_db: float = -1.0, measure: str = "auto"
) -> np.ndarray:
    """Apply gain so the clip measures `target_lufs` (see level_db), then pull the gain down
    if the true peak would exceed `ceiling_db`. Silence is returned unchanged."""
    arr, was_1d = _as2d(data)
    current = level_db(arr, sr, measure)
    if not math.isfinite(current) or not math.isfinite(target_lufs):
        return _restore(arr.copy(), was_1d)
    out = arr.astype(np.float64) * 10.0 ** ((target_lufs - current) / 20.0)
    tp = true_peak_db(out.astype(np.float32), sr)
    if math.isfinite(tp) and tp > ceiling_db:
        out *= 10.0 ** ((ceiling_db - tp) / 20.0)
    return _restore(out.astype(np.float32), was_1d)


def fade(data: np.ndarray, sr: int, ms: float = 5.0, fade_in: bool = True, fade_out: bool = True) -> np.ndarray:
    """Raised-cosine fade at the edges (avoids clicks after trimming or stretching)."""
    arr, was_1d = _as2d(data)
    out = arr.copy()
    n = min(int(round(sr * ms / 1000.0)), out.shape[0] // 2)
    if n > 0:
        ramp = (0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, n))).astype(np.float32)[:, None]
        if fade_in:
            out[:n] *= ramp
        if fade_out:
            out[-n:] *= ramp[::-1]
    return _restore(out, was_1d)


def pad_silence(data: np.ndarray, sr: int, lead_s: float, trail_s: float) -> np.ndarray:
    """Prepend/append digital silence."""
    arr, was_1d = _as2d(data)
    lead = np.zeros((max(0, int(round(lead_s * sr))), arr.shape[1]), dtype=np.float32)
    trail = np.zeros((max(0, int(round(trail_s * sr))), arr.shape[1]), dtype=np.float32)
    return _restore(np.concatenate([lead, arr, trail]), was_1d)
