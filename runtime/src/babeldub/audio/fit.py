"""Fit a dub to its original line: speech span, tempo fit, exact length, matched level.

The dubbing chain measures speech with a simple energy VAD (20 ms frames, -42 dB under the loudest
frame). `fit_segments` (what `post` uses) never slows speech down: a dub shorter than the original
keeps its tempo and the rest is silence. A dub whose speech does not fit the room after the original's
speech onset first loses part of its internal pauses (down to max(80 ms, half of each)), then speeds
up to at most `tempo_max`. A `fixed_length` line (cutscene) is split into chunks that are matched to
the original's speech segments (monotone DP over |log(dub/orig)| with a merge penalty) and each chunk starts where its
segment starts, sped up only to fit that segment. The result has exactly the original's number of
samples at its rate and channel count, loudness matched to the original speech. A dub that still does
not fit is never cut: the file then runs longer. `fit_to_original` is the older bounded-tempo fit
(it may slow down to `tempo_min`), kept for callers that want it.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from babeldub.audio.dsp import fade, level_db, match_loudness, resample, to_channels, to_mono, true_peak_db
from babeldub.audio.io import read_audio, write_wav

VAD_THRESHOLD_DB = -42.0
VAD_FRAME_S = 0.02
EDGE_FADE_MS = 10.0
JOIN_FADE_MS = 5.0
PAUSE_GAP_S = 0.12  # a silence at least this long splits speech into chunks
PAUSE_MIN_S = 0.08  # a shortened pause keeps at least this much ...
PAUSE_KEEP = 0.5  # ... and at least this share of its length
MAX_MERGE = 8  # chunks/segments one DP group may merge on each side
MERGE_PENALTY = 0.1  # DP cost per merged chunk/segment (about a 10 % length mismatch)


def _active_frames(data: np.ndarray, sr: int, threshold_db: float) -> tuple[np.ndarray, int, int] | None:
    """(frame indices within `threshold_db` of the loudest 20 ms frame, frame size, n samples)."""
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
    return (on, frame, len(mono)) if on.size else None


def speech_bounds(data: np.ndarray, sr: int, threshold_db: float = VAD_THRESHOLD_DB) -> tuple[int, int] | None:
    """(start, end) samples from the first to the last 20 ms frame within `threshold_db` of the loudest."""
    active = _active_frames(data, sr, threshold_db)
    if active is None:
        return None
    on, frame, total = active
    return int(on[0]) * frame, min(total, (int(on[-1]) + 1) * frame)


def speech_chunks(
    data: np.ndarray, sr: int, min_gap_s: float = PAUSE_GAP_S, threshold_db: float = VAD_THRESHOLD_DB
) -> list[tuple[int, int]]:
    """Speech chunks (start, end) in samples: runs of active frames, split only by silences of at least
    `min_gap_s` (shorter ones stay inside a chunk). Empty for silence."""
    active = _active_frames(data, sr, threshold_db)
    if active is None:
        return []
    on, frame, total = active
    min_gap = max(1, int(math.ceil(min_gap_s * sr / frame)))
    chunks: list[list[int]] = [[int(on[0]), int(on[0]) + 1]]
    for idx in on[1:]:
        idx = int(idx)
        if idx - chunks[-1][1] >= min_gap:
            chunks.append([idx, idx + 1])
        else:
            chunks[-1][1] = idx + 1
    return [(s * frame, min(total, e * frame)) for s, e in chunks]


def speech_segments_s(data: np.ndarray, sr: int, min_gap_s: float = PAUSE_GAP_S) -> list[tuple[float, float]]:
    """`speech_chunks` in seconds."""
    return [(s / sr, e / sr) for s, e in speech_chunks(data, sr, min_gap_s)]


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
    pause_cut_s: float = 0.0  # internal pause time removed before any speed-up
    onset_deltas: list[float] = field(default_factory=list)  # placed - original onset per segment (s)
    offset_deltas: list[float] = field(default_factory=list)  # placed - original end per segment (s; < 0 = ends early)
    chunk_tempos: list[float] = field(default_factory=list)  # tempo per placed chunk (one for a plain line)


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


def align_chunks(
    dub: Sequence[tuple[int, int]], orig: Sequence[tuple[int, int]], max_merge: int = MAX_MERGE
) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Monotone matching of dub chunks to original segments: both lists are cut into the same number of
    contiguous groups, paired in order, minimising the sum of |log(dub group span / orig group span)|
    (a group span runs from its first start to its last end) plus MERGE_PENALTY per merged item, so
    one-to-one pairs win unless merging clearly matches the lengths better.
    Returns [((i0, i1), (j0, j1))] half-open index ranges covering both lists."""
    m, k = len(dub), len(orig)
    if not m or not k:
        raise ValueError("align_chunks needs at least one chunk and one segment")
    limit = max(max_merge, math.ceil(max(m, k) / min(m, k)))
    cost = [[math.inf] * (k + 1) for _ in range(m + 1)]
    back: list[list[tuple[int, int] | None]] = [[None] * (k + 1) for _ in range(m + 1)]
    cost[0][0] = 0.0
    for i in range(1, m + 1):
        for j in range(1, k + 1):
            for a in range(max(0, i - limit), i):
                span_d = max(1, dub[i - 1][1] - dub[a][0])
                for b in range(max(0, j - limit), j):
                    if cost[a][b] == math.inf:
                        continue
                    span_o = max(1, orig[j - 1][1] - orig[b][0])
                    c = cost[a][b] + abs(math.log(span_d / span_o)) + MERGE_PENALTY * ((i - a - 1) + (j - b - 1))
                    if c < cost[i][j]:
                        cost[i][j], back[i][j] = c, (a, b)
    groups: list[tuple[tuple[int, int], tuple[int, int]]] = []
    i, j = m, k
    while i or j:
        prev = back[i][j]
        assert prev is not None
        groups.append(((prev[0], i), (prev[1], j)))
        i, j = prev
    return groups[::-1]


def _shorten_pauses(speech: np.ndarray, chunks: Sequence[tuple[int, int]], sr: int, need: int) -> tuple[np.ndarray, int]:
    """Cut up to `need` samples out of the pauses between `chunks` (relative to `speech`), every pause
    by the same share of what it may lose (down to max(PAUSE_MIN_S, PAUSE_KEEP x its length)). The
    middle of a pause is removed; the joins get short fades. Returns (audio, samples removed)."""
    gaps = [(chunks[n][1], chunks[n + 1][0]) for n in range(len(chunks) - 1)]
    floor = int(PAUSE_MIN_S * sr)
    cuttable = [max(0, (e - s) - max(floor, int(PAUSE_KEEP * (e - s)))) for s, e in gaps]
    total = sum(cuttable)
    if need <= 0 or total <= 0:
        return speech, 0
    share = min(1.0, need / total)
    pieces: list[np.ndarray] = []
    pos = removed = 0
    for (s, e), can in zip(gaps, cuttable, strict=True):
        cut = int(round(can * share))
        if cut <= 0:
            continue
        keep = (e - s) - cut
        pieces.append(speech[pos : s + keep // 2])
        pos = e - (keep - keep // 2)
        removed += cut
    pieces.append(speech[pos:])
    last = len(pieces) - 1
    joined = [fade(p, sr, JOIN_FADE_MS, fade_in=n > 0, fade_out=n < last) for n, p in enumerate(pieces)]
    return np.concatenate(joined).astype(np.float32), removed


def _compress(
    speech: np.ndarray, chunks: Sequence[tuple[int, int]], sr: int, room: int, *, tempo_max: float,
    tolerance: float, stretcher: Stretcher,
) -> tuple[np.ndarray, int, float, str]:
    """Make `speech` fit `room` samples: nothing when it fits, else shorten its pauses, then speed up to
    `tempo_max` (an overrun within `tolerance` is left alone). Returns (audio, pause cut, tempo, method)."""
    if len(speech) <= room:
        return speech, 0, 1.0, "none"
    speech, cut = _shorten_pauses(speech, chunks, sr, len(speech) - room)
    needed = len(speech) / max(1, room)
    if needed <= 1.0 + tolerance or tempo_max <= 1.0 + 1e-3:
        return speech, cut, 1.0, "none"
    tempo = min(needed, tempo_max)
    out, used = _stretch(speech, sr, 1.0 / tempo, stretcher)
    return out, cut, tempo, used


def _segments(given: Sequence[Sequence[float]] | None, mono: np.ndarray, sr: int, frames: int) -> list[tuple[int, int]]:
    """Original speech segments in samples: `given` (seconds, e.g. analysis vad_segments) when usable,
    else the energy-VAD chunks, else the whole file."""
    segs: list[tuple[int, int]] = []
    for item in given or ():
        s, e = max(0, int(round(float(item[0]) * sr))), min(frames, int(round(float(item[1]) * sr)))
        if e > s:
            segs.append((s, e))
    segs.sort()
    merged: list[tuple[int, int]] = []
    for s, e in segs:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged or speech_chunks(mono, sr) or [(0, frames)]


def fit_segments(
    dub: np.ndarray,
    dub_sr: int,
    orig: np.ndarray,
    orig_sr: int,
    *,
    tempo_max: float,
    tolerance: float,
    ceiling_db: float,
    stretcher: Stretcher,
    tempo_min: float = 1.0,
    fixed_length: bool = False,
    orig_segments: Sequence[Sequence[float]] | None = None,
    default_level: float = -20.0,
) -> FitResult:
    """Fit `dub` (any rate/channels) to `orig` ([frames, channels]); see the module docstring.

    `tempo_min` < 1 allows slowing a plain line down to the original's speech span (off by default).
    `orig_segments` ([[start_s, end_s], ...]) replaces the energy VAD of the original for `fixed_length`."""
    frames, channels = orig.shape[0], orig.shape[1] if orig.ndim > 1 else 1
    mono_orig = to_mono(orig) if orig.ndim > 1 else orig.astype(np.float32)
    o0, o1 = speech_bounds(mono_orig, orig_sr) or (0, frames)
    measure = "lufs" if (o1 - o0) / orig_sr >= 0.4 else "rms"
    level_ref = level_db(mono_orig[o0:o1], orig_sr, measure)
    if not math.isfinite(level_ref):
        level_ref = default_level

    mono = resample(to_mono(dub) if dub.ndim > 1 else dub.astype(np.float32), dub_sr, orig_sr)
    chunks = speech_chunks(mono, orig_sr)
    if not chunks:
        raise ValueError("synthesized audio is silent")
    d0, d1 = chunks[0][0], chunks[-1][1]
    placements: list[tuple[int, np.ndarray]] = []
    tempos: list[float] = []
    weights: list[int] = []
    deltas: list[float] = []
    ends: list[float] = []
    methods: list[str] = []
    cut_total = 0
    if fixed_length:
        segs = _segments(orig_segments, mono_orig, orig_sr, frames)
        target = max(1, segs[-1][1] - segs[0][0])
        cursor = 0
        for (i0, i1), (j0, j1) in align_chunks(chunks, segs):
            a, b = chunks[i0][0], chunks[i1 - 1][1]
            seg_start, seg_end = segs[j0][0], segs[j1 - 1][1]
            audio, cut, tempo, used = _compress(
                mono[a:b], [(s - a, e - a) for s, e in chunks[i0:i1]], orig_sr, seg_end - seg_start,
                tempo_max=tempo_max, tolerance=tolerance, stretcher=stretcher,
            )
            start = max(seg_start, cursor)  # a chunk that still overruns pushes the next one later
            placements.append((start, audio))
            cursor = start + len(audio)
            deltas.append((start - seg_start) / orig_sr)
            ends.append((start + len(audio) - seg_end) / orig_sr)
            tempos.append(tempo)
            weights.append(b - a)
            methods.append(used)
            cut_total += cut
    else:
        target = max(1, o1 - o0)
        speech = mono[d0:d1]
        if tempo_min < 1.0 - 1e-6 and len(speech) * (1.0 + tolerance) < target:  # opt-in slow-down
            tempo = max(len(speech) / target, tempo_min)
            audio, used = _stretch(speech, orig_sr, 1.0 / tempo, stretcher)
            cut = 0
        else:
            audio, cut, tempo, used = _compress(
                speech, [(s - d0, e - d0) for s, e in chunks], orig_sr, frames - o0,
                tempo_max=tempo_max, tolerance=tolerance, stretcher=stretcher,
            )
        n = len(audio)
        start = o0 if o0 + n <= frames else max(0, frames - n)  # overrun into the trail, then into the lead
        placements.append((start, audio))
        deltas.append((start - o0) / orig_sr)
        ends.append((start + n - o1) / orig_sr)
        tempos.append(tempo)
        weights.append(len(speech))
        methods.append(used)
        cut_total += cut

    first = placements[0][0]
    last = placements[-1][0] + len(placements[-1][1])
    total = max(frames, last)
    out = np.zeros(total, dtype=np.float32)
    for start, audio in placements:
        out[start : start + len(audio)] += fade(audio, orig_sr, EDGE_FADE_MS)
    level_in = level_db(out[first:last], orig_sr, measure)
    if math.isfinite(level_in):
        out *= np.float32(10.0 ** ((level_ref - level_in) / 20.0))
    final = to_channels(out, channels)
    peak = true_peak_db(final, orig_sr)
    if math.isfinite(peak) and peak > ceiling_db:
        final = final * np.float32(10.0 ** ((ceiling_db - peak) / 20.0))
        peak = true_peak_db(final, orig_sr)
    level_out = level_db(to_mono(final[first:last]), orig_sr, measure)
    tempo_avg = sum(t * w for t, w in zip(tempos, weights, strict=True)) / max(1, sum(weights))
    return FitResult(
        audio=final, sr=orig_sr, tempo=tempo_avg, ratio_raw=(d1 - d0) / target, ratio=(last - first) / target,
        target_s=target / orig_sr, lead_s=first / orig_sr, trail_s=(total - last) / orig_sr, exact=total == frames,
        fits=last <= frames, method=next((m for m in methods if m != "none"), "none"), level_ref=level_ref,
        level_in=level_in, level_out=level_out, measure=measure, peak_db=peak, pause_cut_s=cut_total / orig_sr,
        onset_deltas=deltas, offset_deltas=ends, chunk_tempos=tempos,
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
