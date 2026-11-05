"""Channel match: make a dub sound like it went through the same channel as its original line.

A clean TTS/RVC take placed into a line recorded through a radio, a phone or a band-limited bank sounds
"pasted in". `match_channel` compares the long-term average spectrum (LTAS) of the speech frames (STFT
frames within SPEECH_DB of the loudest one) of the dub and of the original in 1/3-octave bands from 80 Hz
to 16 kHz and corrects the dub:

  eq    LTAS EQ: the band differences relative to the speech band (315 Hz - 3.15 kHz), smoothed over
        neighbouring bands and clipped to +-MAX_EQ_DB, never boosting a band the dub has (almost) nothing
        in; plus bandwidth match: when the original has far less energy than the dub above some
        frequency (an 8 kHz bank, a phone line), the dub is low-passed at that cutoff.
  full  eq, and for a "radio/phone" original (both its lows under ~250 Hz and its highs above
        RADIO_MAX_CUTOFF_HZ missing) a band-pass at the original's edges plus a mild tanh saturation.

The correction is one minimum-phase FIR (frequency sampling + homomorphic minimum phase, numpy only):
deterministic, no pre-ringing, the length of the take is kept. A short original line (under
MIN_LINE_SPEECH_S of speech) takes its EQ target from the line pooled with the character's other
originals (`extra`); the bandwidth and the radio check always come from the line itself.
The caller re-matches the loudness afterwards.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from babeldub.audio.dsp import to_mono

MODES = ("off", "eq", "full")
BAND_CENTERS = tuple(1000.0 * 2.0 ** (k / 3.0) for k in range(-11, 13))  # 79 Hz ... 15.9 kHz, 1/3 octave
SPEECH_BAND = (300.0, 3400.0)  # the bands the correction is anchored to (0 dB) and the distance is offset by
SPEECH_DB = -30.0  # STFT frames within this of the loudest frame are speech
MAX_EQ_DB = 9.0
EMPTY_DB = -45.0  # a dub band this far under its speech band is empty: never boosted
CUTOFF_MIN_HZ = 2000.0  # a band limit is only searched above this
CUTOFF_DROP_DB = 20.0  # ... where the original is this much under the dub, up to the top
RADIO_MAX_CUTOFF_HZ = 4500.0  # radio/phone: the highs end at or under this ...
RADIO_LOW_DB = -15.0  # ... and 100-250 Hz is this much under the dub
RADIO_DRIVE = 2.0  # tanh saturation of the radio chain (mild)
STOP_DB = -60.0  # attenuation of a low-/high-pass stop band
FLOOR_DB = -60.0  # band levels under the speech band are floored here for the distance
MIN_LINE_SPEECH_S = 1.0  # a shorter original pools the character's other originals for its EQ target
MIN_SPEECH_S = 0.3  # less speech than this: nothing is measured or applied
POOL_SPEECH_S = 3.0  # pool other originals until this much speech


@dataclass
class Profile:
    """LTAS of the speech frames of one recording."""
    sr: int
    speech_s: float
    bands_db: np.ndarray  # mean power per BAND_CENTERS band (dB), NaN above the Nyquist limit
    freqs: np.ndarray = field(default_factory=lambda: np.zeros(0))  # fine spectrum (STFT bins)
    psd_db: np.ndarray = field(default_factory=lambda: np.zeros(0))


@dataclass
class MatchResult:
    audio: np.ndarray  # same shape as the input
    applied: bool
    info: dict[str, Any]


def _n_fft(sr: int) -> int:
    return 1 << max(9, math.ceil(math.log2(max(1.0, 0.08 * sr))))


def _band_edges(fc: float, fraction: float = 3.0) -> tuple[float, float]:
    half = 2.0 ** (1.0 / (2.0 * fraction))
    return fc / half, fc * half


def profile(audio: np.ndarray, sr: int) -> Profile:
    """LTAS of the speech frames (Hann STFT, 80 ms, 75 % overlap) in 1/3-octave bands plus the fine spectrum."""
    mono = (to_mono(audio) if audio.ndim > 1 else np.asarray(audio, dtype=np.float32)).astype(np.float64)
    n_fft = _n_fft(sr)
    hop = n_fft // 4
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    empty = Profile(sr, 0.0, np.full(len(BAND_CENTERS), np.nan), freqs, np.full(len(freqs), np.nan))
    if len(mono) < n_fft:
        mono = np.pad(mono, (0, n_fft - len(mono)))
    n = 1 + (len(mono) - n_fft) // hop
    window = np.hanning(n_fft)[None, :]
    power = np.empty((n, len(freqs)))
    for start in range(0, n, 256):  # blocks of frames keep the memory small on long cutscenes
        idx = np.arange(n_fft)[None, :] + hop * np.arange(start, min(n, start + 256))[:, None]
        power[start : start + idx.shape[0]] = np.abs(np.fft.rfft(mono[idx] * window, axis=1)) ** 2
    energy = power.sum(axis=1)
    if energy.max() <= 1e-12:
        return empty
    speech = energy >= energy.max() * 10.0 ** (SPEECH_DB / 10.0)
    psd = power[speech].mean(axis=0)
    bands = np.full(len(BAND_CENTERS), np.nan)
    nyq = 0.98 * sr / 2.0
    for b, fc in enumerate(BAND_CENTERS):
        lo, hi = _band_edges(fc)
        if hi > nyq:
            continue
        pick = (freqs >= lo) & (freqs < hi)
        value = psd[pick].mean() if pick.any() else float(np.interp(fc, freqs, psd))
        bands[b] = 10.0 * math.log10(value + 1e-20)
    return Profile(sr, float(speech.sum() * hop / sr), bands, freqs, 10.0 * np.log10(psd + 1e-20))


def pooled(profiles: Sequence[Profile]) -> Profile:
    """Speech-weighted power average of the band levels (no fine spectrum)."""
    usable = [p for p in profiles if p.speech_s > 0]
    if not usable:
        return Profile(0, 0.0, np.full(len(BAND_CENTERS), np.nan))
    weights = np.array([p.speech_s for p in usable])
    stack = np.array([10.0 ** (p.bands_db / 10.0) for p in usable])  # NaN stays NaN
    valid = ~np.isnan(stack)
    total = np.where(valid, stack, 0.0).T @ weights
    norm = valid.astype(float).T @ weights
    bands = np.where(norm > 0, 10.0 * np.log10(np.where(norm > 0, total / np.maximum(norm, 1e-12), 1.0) + 1e-20),
                     np.nan)
    return Profile(min(p.sr for p in usable), float(weights.sum()), bands)


def _speech_level(bands_db: np.ndarray) -> float:
    pick = [b for b, fc in enumerate(BAND_CENTERS) if SPEECH_BAND[0] <= fc <= SPEECH_BAND[1] and not np.isnan(bands_db[b])]
    return float(np.mean(bands_db[pick])) if pick else float("nan")


def band_distance(a: Profile, b: Profile) -> float | None:
    """Mean |band difference| (dB) over the bands both have, after removing their median offset; every band
    is floored at FLOOR_DB under its own speech band (digital silence is not a measurement)."""
    if a.speech_s < MIN_SPEECH_S or b.speech_s < MIN_SPEECH_S:
        return None
    rel_a = np.maximum(a.bands_db - _speech_level(a.bands_db), FLOOR_DB)
    rel_b = np.maximum(b.bands_db - _speech_level(b.bands_db), FLOOR_DB)
    diff = rel_a - rel_b
    diff = diff[~np.isnan(diff)]
    if diff.size < 3:
        return None
    return float(np.mean(np.abs(diff - np.median(diff))))


def _fine_bands(p: Profile, top_hz: float) -> tuple[np.ndarray, np.ndarray]:
    """(centers, dB re the speech band) of the fine spectrum in 1/12-octave bands from 1 kHz to `top_hz`."""
    centers = 1000.0 * 2.0 ** (np.arange(0, 12 * math.log2(top_hz / 1000.0)) / 12.0)
    power = 10.0 ** (p.psd_db / 10.0)
    speech = (p.freqs >= SPEECH_BAND[0]) & (p.freqs <= SPEECH_BAND[1])
    ref = 10.0 * math.log10(power[speech].mean() + 1e-20)
    out = np.empty(len(centers))
    for i, fc in enumerate(centers):
        lo, hi = _band_edges(fc, 12.0)
        pick = (p.freqs >= lo) & (p.freqs < hi)
        value = power[pick].mean() if pick.any() else float(np.interp(fc, p.freqs, power))
        out[i] = 10.0 * math.log10(value + 1e-20) - ref
    return centers, out


def detect_cutoff(dub: Profile, orig: Profile) -> float | None:
    """The frequency above which the original stays CUTOFF_DROP_DB under the dub (both re their speech
    band) up to the top: its band limit, or None."""
    top = 0.95 * min(dub.sr, orig.sr) / 2.0
    if top <= CUTOFF_MIN_HZ * 1.2 or not dub.freqs.size or not orig.freqs.size:
        return None
    centers, d_dub = _fine_bands(dub, top)
    _, d_orig = _fine_bands(orig, top)
    diff = d_orig - d_dub
    for i in np.flatnonzero(centers >= CUTOFF_MIN_HZ):
        rest = diff[i:]
        if rest.size >= 2 and diff[i] <= -CUTOFF_DROP_DB and np.percentile(rest, 80) <= -CUTOFF_DROP_DB:
            return float(_band_edges(centers[i], 12.0)[0])
    return None


def detect_radio(dub: Profile, orig: Profile, cutoff: float | None) -> float | None:
    """The low edge (Hz) of a radio/phone original (highs cut at <= RADIO_MAX_CUTOFF_HZ and 100-250 Hz at least
    RADIO_LOW_DB under the dub, both re their speech band), or None."""
    if cutoff is None or cutoff > RADIO_MAX_CUTOFF_HZ:
        return None
    diff = (orig.bands_db - _speech_level(orig.bands_db)) - (dub.bands_db - _speech_level(dub.bands_db))
    low = [b for b, fc in enumerate(BAND_CENTERS) if 100.0 <= fc <= 250.0 and not np.isnan(diff[b])]
    if not low or float(np.mean(diff[low])) > RADIO_LOW_DB:
        return None
    present = [fc for b, fc in enumerate(BAND_CENTERS) if 100.0 <= fc <= 800.0 and diff[b] > -10.0]
    return float(min(max(present[0] if present else 300.0, 200.0), 600.0) / 2.0 ** (1.0 / 6.0))


def eq_curve(dub: Profile, target: Profile, cutoff: float | None, low_edge: float | None) -> np.ndarray:
    """Correction per band (dB): target - dub re their speech bands, outside [low_edge, cutoff] and for bands
    the dub has nothing in held at the nearest usable value, smoothed (1-2-1), clipped to +-MAX_EQ_DB."""
    d_dub = dub.bands_db - _speech_level(dub.bands_db)
    corr = (target.bands_db - _speech_level(target.bands_db)) - d_dub
    centers = np.array(BAND_CENTERS)
    usable = ~np.isnan(corr)
    if cutoff is not None:
        usable &= _band_edges_array(centers)[1] <= cutoff
    if low_edge is not None:
        usable &= _band_edges_array(centers)[0] >= low_edge
    corr = np.where(usable & (d_dub < EMPTY_DB), np.minimum(corr, 0.0), corr)
    if not usable.any():
        return np.zeros(len(centers))
    idx = np.flatnonzero(usable)
    filled = np.interp(np.arange(len(centers)), idx, corr[idx])  # held flat beyond the usable bands
    smooth = np.convolve(np.pad(filled, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    return np.clip(smooth, -MAX_EQ_DB, MAX_EQ_DB)


def _band_edges_array(centers: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    half = 2.0 ** (1.0 / 6.0)
    return centers / half, centers * half


def _shelf(freqs: np.ndarray, edge: float, high: bool) -> np.ndarray:
    """dB of a stop band above (high) or below `edge`: 0 -> STOP_DB over 1/6 octave (raised cosine in log f)."""
    x = np.log2(np.maximum(freqs, 1e-3) / edge) * 6.0 * (1.0 if high else -1.0) + 0.5  # 0..1 across the transition
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.clip(x, 0.0, 1.0))
    return STOP_DB * ramp


def design_fir(sr: int, eq_db: np.ndarray, lowpass: float | None = None, highpass: float | None = None) -> np.ndarray:
    """Minimum-phase FIR (about 40 ms) with the band gains `eq_db` (interpolated in log frequency) and optional
    stop bands above `lowpass` / below `highpass` (Hz)."""
    taps = int(0.04 * sr) | 1
    n = 1 << math.ceil(math.log2(8 * taps))
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    logf = np.log2(np.maximum(freqs, 1.0))
    gain = np.interp(logf, np.log2(np.array(BAND_CENTERS)), eq_db)
    if lowpass is not None and lowpass < sr / 2.0:
        gain = gain + _shelf(freqs, lowpass, high=True)
    if highpass is not None:
        gain = gain + _shelf(freqs, highpass, high=False)
    mag = 10.0 ** (np.maximum(gain, STOP_DB) / 20.0)
    full = np.concatenate([mag, mag[-2:0:-1]])  # the whole FFT circle
    cep = np.fft.ifft(np.log(full)).real
    fold = np.zeros(n)
    fold[0] = 1.0
    fold[1 : n // 2] = 2.0
    fold[n // 2] = 1.0
    h = np.fft.ifft(np.exp(np.fft.fft(cep * fold))).real[:taps]
    tail = max(1, taps // 8)
    h[-tail:] *= 0.5 + 0.5 * np.cos(np.linspace(0.0, np.pi, tail))
    return h


def apply_fir(audio: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Causal FFT convolution, the input's length and channels kept."""
    arr = audio.astype(np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    n = arr.shape[0] + len(h) - 1
    size = 1 << math.ceil(math.log2(max(2, n)))
    out = np.fft.irfft(np.fft.rfft(arr, size, axis=0) * np.fft.rfft(h, size)[:, None], size, axis=0)[: arr.shape[0]]
    return (out if audio.ndim > 1 else out[:, 0]).astype(np.float32)


def saturate(audio: np.ndarray, drive: float = RADIO_DRIVE) -> np.ndarray:
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak <= 1e-9:
        return audio
    return (np.tanh(drive * audio / peak) / math.tanh(drive) * peak).astype(np.float32)


def match_channel(
    dub: np.ndarray, sr: int, orig: np.ndarray, orig_sr: int, mode: str, extra: Sequence[Profile] = (),
) -> MatchResult:
    """Match `dub` (at `sr`, any channels) to the channel of `orig` (see the module docstring). `extra`: profiles
    of the character's other originals for a short line's EQ target. `info`: {mode, applied, ref, eq_db, cutoff_hz,
    radio, ltas_db: {before, after}} (or `reason` when nothing was applied)."""
    if mode not in MODES:
        raise ValueError(f"match mode must be one of {MODES}")
    info: dict[str, Any] = {"mode": mode, "applied": False}
    if mode == "off":
        return MatchResult(dub, False, info)
    line, dub_p = profile(orig, orig_sr), profile(dub, sr)
    if line.speech_s < MIN_SPEECH_S or dub_p.speech_s < MIN_SPEECH_S:
        info["reason"] = "too little speech to measure"
        return MatchResult(dub, False, info)
    target, ref = line, "line"
    if line.speech_s < MIN_LINE_SPEECH_S and extra:
        pool, total = [line], line.speech_s
        for p in extra:
            if total >= POOL_SPEECH_S:
                break
            if p.speech_s >= MIN_SPEECH_S:
                pool.append(p)
                total += p.speech_s
        if len(pool) > 1:
            target, ref = pooled(pool), "character"
    cutoff = detect_cutoff(dub_p, line)
    low_edge = detect_radio(dub_p, line, cutoff)
    radio = low_edge is not None
    chain = radio and mode == "full"
    eq = eq_curve(dub_p, target, cutoff, low_edge if chain else None)
    out = apply_fir(dub, design_fir(sr, eq, lowpass=cutoff, highpass=low_edge if chain else None))
    if chain:
        out = apply_fir(saturate(out), design_fir(sr, np.zeros(len(BAND_CENTERS)), lowpass=cutoff, highpass=low_edge))
    after = profile(out, sr)
    info.update({
        "applied": True, "ref": ref, "eq_db": [round(float(v), 1) for v in eq],
        "cutoff_hz": None if cutoff is None else round(cutoff), "radio": radio, "radio_chain": chain,
        "low_hz": round(low_edge) if chain and low_edge is not None else None,
        "ltas_db": {"before": _round(band_distance(dub_p, line)), "after": _round(band_distance(after, line))},
    })
    return MatchResult(out, True, info)


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)
