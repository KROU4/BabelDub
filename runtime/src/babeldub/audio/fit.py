"""Fit a dub to its original line: speech span, bounded tempo fit, exact length, matched level.

The dubbing chain measures speech with a simple energy VAD (20 ms frames, -42 dB under the loudest
frame). `fit_to_original` stretches the dub's speech only within [tempo_min, tempo_max] (pitch kept),
leaves the rest of the difference to silence, puts the speech where the original's speech starts and
returns exactly the original's number of samples at its rate and channel count, loudness matched to
the original speech. A dub that still does not fit is never cut: the file then runs longer.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from babeldub.audio.dsp import fade, level_db, match_loudness, resample, to_channels, to_mono, true_peak_db
from babeldub.audio.io import read_audio, write_wav

VAD_THRESHOLD_DB = -42.0
VAD_FRAME_S = 0.02
EDGE_FADE_MS = 10.0


def speech_bounds(data: np.ndarray, sr: int, threshold_db: float = VAD_THRESHOLD_DB) -> tuple[int, int] | None:
    """(start, end) samples from the first to the last 20 ms frame within `threshold_db` of the loudest."""
    mono = to_mono(data) if data.ndim > 1 else np.asarray(data, dtype=np.float32)
    frame = max(1, int(VAD_FRAME_S * sr))
    n = len(mono) // frame
    if n == 0:
        return None
    rms = np.sqrt(np.mean(mono[: n * frame].astype(np.float64).reshape(n, frame) ** 2, axis=1) + 1e-12)
    peak = float(rms.max())
    if peak <= 1e-5:
        return None
    on = np.where(20.0 * np.log10(rms / peak) > threshold_db)[0]
    if on.size == 0:
        return None
    return int(on[0]) * frame, min(len(mono), (int(on[-1]) + 1) * frame)


def speech_span(data: np.ndarray, sr: int) -> float:
    """Seconds from the first to the last speech frame (0.0 for silence)."""
    bounds = speech_bounds(data, sr)
    return 0.0 if bounds is None else (bounds[1] - bounds[0]) / sr


def length_score(speech_s: float, target_s: float) -> float:
    """|log ratio| of a candidate's speech span to the original's (0 = same natural length)."""
    return abs(math.log(max(speech_s, 0.05) / max(target_s, 0.05)))


@dataclass
class FitResult:
    audio: np.ndarray  # [frames, channels] at the original rate
    sr: int
    tempo: float  # applied speed factor (>1 faster); 1.0 = no tempo change
    ratio_raw: float  # dub speech / original speech before the fit
    ratio: float  # after the fit
    target_s: float  # original speech span
    lead_s: float
    trail_s: float
    exact: bool  # same number of samples as the original
    fits: bool  # the fitted speech fits into the original file
    method: str
    level_ref: float
    level_in: float
    level_out: float
    measure: str
    peak_db: float


Stretcher = Callable[[Path, Path, float], str]


def _stretch(speech: np.ndarray, sr: int, ratio: float, stretcher: Stretcher) -> tuple[np.ndarray, str]:
    with tempfile.TemporaryDirectory(prefix="babeldub-fit-") as tmp:
        src, dst = Path(tmp, "in.wav"), Path(tmp, "out.wav")
        write_wav(src, speech, sr, subtype="FLOAT")
        used = stretcher(src, dst, ratio)
        out, out_sr = read_audio(dst)
    return resample(to_mono(out), out_sr, sr), used


def fit_to_original(
    dub: np.ndarray,
    dub_sr: int,
    orig: np.ndarray,
    orig_sr: int,
    *,
    tempo_min: float,
    tempo_max: float,
    tolerance: float,
    ceiling_db: float,
    stretcher: Stretcher,
    default_level: float = -20.0,
) -> FitResult:
    """Fit `dub` (any rate/channels) to `orig` ([frames, channels]); see the module docstring."""
    frames, channels = orig.shape[0], orig.shape[1] if orig.ndim > 1 else 1
    mono_orig = to_mono(orig) if orig.ndim > 1 else orig.astype(np.float32)
    o_bounds = speech_bounds(mono_orig, orig_sr) or (0, frames)
    o0, o1 = o_bounds
    target = max(1, o1 - o0)
    measure = "lufs" if target / orig_sr >= 0.4 else "rms"
    level_ref = level_db(mono_orig[o0:o1], orig_sr, measure)
    if not math.isfinite(level_ref):
        level_ref = default_level

    mono = resample(to_mono(dub) if dub.ndim > 1 else dub.astype(np.float32), dub_sr, orig_sr)
    d_bounds = speech_bounds(mono, orig_sr)
    if d_bounds is None:
        raise ValueError("synthesized audio is silent")
    speech = mono[d_bounds[0] : d_bounds[1]]
    ratio_raw = len(speech) / target
    tempo, used = 1.0, "none"
    if abs(math.log(ratio_raw)) > math.log1p(tolerance):
        tempo = min(max(ratio_raw, tempo_min), tempo_max)
        if abs(tempo - 1.0) > 1e-3:
            speech, used = _stretch(speech, orig_sr, 1.0 / tempo, stretcher)
        else:
            tempo = 1.0
    speech = fade(speech, orig_sr, EDGE_FADE_MS)
    level_in = level_db(speech, orig_sr, measure)
    speech = match_loudness(speech, orig_sr, level_ref, ceiling_db=ceiling_db, measure=measure)

    n = len(speech)
    fits = n <= frames
    start = o0 if o0 + n <= frames else max(0, frames - n)  # overrun into the trail, then into the lead
    total = max(frames, start + n)
    out = np.zeros(total, dtype=np.float32)
    out[start : start + n] = speech
    final = to_channels(out, channels)
    peak = true_peak_db(final, orig_sr)
    if math.isfinite(peak) and peak > ceiling_db:
        final = final * np.float32(10.0 ** ((ceiling_db - peak) / 20.0))
        peak = true_peak_db(final, orig_sr)
    level_out = level_db(to_mono(final[start : start + n]), orig_sr, measure)
    return FitResult(
        audio=final, sr=orig_sr, tempo=tempo, ratio_raw=ratio_raw, ratio=n / target, target_s=target / orig_sr,
        lead_s=start / orig_sr, trail_s=(total - start - n) / orig_sr, exact=total == frames, fits=fits,
        method=used, level_ref=level_ref, level_in=level_in, level_out=level_out, measure=measure, peak_db=peak,
    )


def conform(audio: np.ndarray, sr: int, ref: np.ndarray, ref_sr: int, *, level_ref: float, measure: str,
            ceiling_db: float) -> tuple[np.ndarray, float]:
    """Re-apply the exact-length + loudness pass to a converted take (same timing as `ref`): resample
    to `ref`'s rate, pad/trim to its sample count and channels, match the speech level. Returns
    (audio, true peak dB)."""
    mono = resample(to_mono(audio) if audio.ndim > 1 else audio.astype(np.float32), sr, ref_sr)
    frames = ref.shape[0]
    mono = mono[:frames] if len(mono) >= frames else np.pad(mono, (0, frames - len(mono)))
    bounds = speech_bounds(mono, ref_sr)
    if bounds is not None and math.isfinite(level_ref):
        b0, b1 = bounds
        current = level_db(mono[b0:b1], ref_sr, measure)
        if math.isfinite(current):
            mono = mono * np.float32(10.0 ** ((level_ref - current) / 20.0))
    final = to_channels(mono.astype(np.float32), ref.shape[1] if ref.ndim > 1 else 1)
    peak = true_peak_db(final, ref_sr)
    if math.isfinite(peak) and peak > ceiling_db:
        final = final * np.float32(10.0 ** ((ceiling_db - peak) / 20.0))
        peak = true_peak_db(final, ref_sr)
    return final, peak
