"""Audio helpers for repacking: WAV I/O, channel mapping, resampling, int16 conversion.

Self-contained on purpose (numpy + soundfile only) so the adapter does not depend on the
pipeline's audio package; the resampler is a polyphase windowed-sinc (Kaiser) filter.
"""

from __future__ import annotations

from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf

_HALF_TAPS = 24  # zero crossings on each side at full bandwidth
_KAISER_BETA = 8.6
_ROLLOFF = 0.95
_MAX_TABLE_PHASES = 4096
_OUT_BLOCK = 1 << 15


def read_float(path: Path) -> tuple[np.ndarray, int]:
    """float32 samples (frames, channels) and sample rate."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return data, int(sr)


def write_int16(path: Path, pcm: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), pcm, sr, subtype="PCM_16")


def to_channels(data: np.ndarray, channels: int) -> np.ndarray:
    have = data.shape[1]
    if have == channels:
        return data
    if channels == 1:
        return data.mean(axis=1, keepdims=True, dtype=np.float32).astype(data.dtype)
    if have == 1:
        return np.repeat(data, channels, axis=1)
    out = np.zeros((data.shape[0], channels), dtype=data.dtype)
    n = min(have, channels)
    out[:, :n] = data[:, :n]
    return out


def resampled_length(n: int, sr_in: int, sr_out: int) -> int:
    if sr_in == sr_out:
        return n
    return (n * sr_out + sr_in - 1) // sr_in


def _kernel(x: np.ndarray, cutoff: float, half: int) -> np.ndarray:
    w = np.where(
        np.abs(x) < half, np.i0(_KAISER_BETA * np.sqrt(np.clip(1.0 - (x / half) ** 2, 0.0, 1.0))), 0.0
    )
    return cutoff * np.sinc(cutoff * x) * w / np.i0(_KAISER_BETA)


def resample(data: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Band-limited resampling of float (frames, channels) data."""
    if sr_in == sr_out or data.shape[0] == 0:
        return data.astype(np.float32, copy=False)
    g = gcd(sr_in, sr_out)
    up, down = sr_out // g, sr_in // g
    cutoff = min(1.0, sr_out / sr_in) * _ROLLOFF
    half = int(np.ceil(_HALF_TAPS / cutoff))
    taps = np.arange(-half + 1, half + 1)  # input offsets relative to floor(t)
    n_in = data.shape[0]
    n_out = resampled_length(n_in, sr_in, sr_out)
    padded = np.concatenate(
        [
            np.zeros((half, data.shape[1]), np.float64),
            data.astype(np.float64),
            np.zeros((half + 1, data.shape[1])),
        ]
    )
    table = None
    if up <= _MAX_TABLE_PHASES:
        frac = np.arange(up)[:, None] / up
        table = _kernel(taps[None, :] - frac, cutoff, half)
        table /= table.sum(axis=1, keepdims=True)
    out = np.empty((n_out, data.shape[1]), dtype=np.float32)
    for start in range(0, n_out, _OUT_BLOCK):
        j = np.arange(start, min(n_out, start + _OUT_BLOCK), dtype=np.int64)
        base = (j * down) // up
        phase = (j * down) % up
        if table is not None:
            weights = table[phase]
        else:
            weights = _kernel(taps[None, :] - (phase / up)[:, None], cutoff, half)
            weights /= weights.sum(axis=1, keepdims=True)
        idx = base[:, None] + taps[None, :] + half
        for c in range(data.shape[1]):
            out[start : start + j.size, c] = np.einsum("ij,ij->i", padded[idx, c], weights)
    return out


def float_to_int16(data: np.ndarray) -> np.ndarray:
    return np.clip(np.round(data.astype(np.float64) * 32768.0), -32768, 32767).astype(np.int16)


def load_for_subsound(path: Path, channels: int, rate: int) -> tuple[np.ndarray, int, int]:
    """Read a WAV/FLAC, map channels, resample to `rate`; returns (int16 pcm, source rate, source frames)."""
    data, sr = read_float(path)
    frames = data.shape[0]
    data = to_channels(data, channels)
    data = resample(data, sr, rate)
    return float_to_int16(data), sr, frames
