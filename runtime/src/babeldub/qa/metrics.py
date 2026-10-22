"""QA metrics: text normalization, character error rate, audio level checks, voice similarity
and the speaker identity margin.

CER is computed on normalized text (lower case, no punctuation or markup, ё -> е; digits are
spelled out on both sides when the language is given), with spaces counted as characters. The Levenshtein distance is a row-vectorized
numpy implementation, so no extra dependency is needed in the CLI.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

_MARKUP_RE = re.compile(r"\{[^{}]*\}|<[^<>]*>|\[[^\[\]]*\]")
_APOSTROPHES = "'’ʼ`´"
_SPACE_RE = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    """Lower case, strip markup tags, drop punctuation and symbols, ё -> е, collapse spaces."""
    if not text:
        return ""
    text = _MARKUP_RE.sub(" ", text)
    text = unicodedata.normalize("NFC", text).lower().replace("ё", "е")
    for ch in _APOSTROPHES:
        text = text.replace(ch, "")
    out: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat[0] in ("P", "S", "C"):
            out.append(" ")
        else:
            out.append(ch)
    return _SPACE_RE.sub(" ", "".join(out)).strip()


def levenshtein(a: Sequence[str] | str, b: Sequence[str] | str) -> int:
    """Edit distance (insert/delete/substitute = 1) between two sequences."""
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    codes: dict[str, int] = {}
    av = np.array([codes.setdefault(x, len(codes)) for x in a], dtype=np.int64)
    bv = np.array([codes.setdefault(x, len(codes)) for x in b], dtype=np.int64)
    idx = np.arange(m + 1, dtype=np.int64)
    prev = idx.copy()
    row = np.empty(m + 1, dtype=np.int64)
    for i in range(1, n + 1):
        cost = (bv != av[i - 1]).astype(np.int64)
        row[0] = i
        # deletion or substitution, then resolve insertions with a running minimum
        row[1:] = np.minimum(prev[1:] + 1, prev[:-1] + cost)
        row = np.minimum.accumulate(row - idx) + idx
        prev, row = row, prev
    return int(prev[m])


def cer(reference: str | None, hypothesis: str | None, lang: str | None = None) -> float:
    """Character error rate of the hypothesis (ASR) against the reference (dub text).

    With `lang`, digits on both sides are spelled out first (num2words), so "25" heard for
    "двадцать пять" (or the other way round) is not an error."""
    if lang:
        from babeldub.text.normalize import numbers_to_words

        reference = numbers_to_words(reference or "", lang)
        hypothesis = numbers_to_words(hypothesis or "", lang)
    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(ref, hyp) / len(ref)


CONTOUR_POINTS = 32  # voiced F0 contours are resampled to this many points before the correlation
MIN_CONTOUR = 8  # voiced points each contour needs
MAX_CONTOUR_STRETCH = 2.0  # contours whose voiced lengths differ more are not compared


def f0_range_ratio(dub_range: float | None, orig_range: float | None) -> float | None:
    """min/max of the two F0 ranges (semitones): 1 = as lively as the original, 0.5 = half the range."""
    if not isinstance(dub_range, (int, float)) or not isinstance(orig_range, (int, float)):
        return None
    if dub_range <= 0 or orig_range <= 0:
        return None
    return float(min(dub_range, orig_range) / max(dub_range, orig_range))


def contour_correlation(dub: Sequence[float | None] | None, orig: Sequence[float | None] | None) -> float | None:
    """Pearson correlation of the voiced F0 contours (semitones), both resampled to CONTOUR_POINTS;
    None when either is too short or their lengths differ more than MAX_CONTOUR_STRETCH times."""
    a = np.array([v for v in dub or [] if v is not None], dtype=np.float64)
    b = np.array([v for v in orig or [] if v is not None], dtype=np.float64)
    if min(a.size, b.size) < MIN_CONTOUR or max(a.size, b.size) > MAX_CONTOUR_STRETCH * min(a.size, b.size):
        return None
    grid = np.linspace(0.0, 1.0, CONTOUR_POINTS)
    ra = np.interp(grid, np.linspace(0.0, 1.0, a.size), a)
    rb = np.interp(grid, np.linspace(0.0, 1.0, b.size), b)
    if ra.std() < 1e-6 or rb.std() < 1e-6:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def prosody_similarity(dub: dict[str, Any] | None, orig: dict[str, Any] | None) -> dict[str, float | None]:
    """{range_ratio, contour_corr, sim}: sim = 0.6 x range ratio + 0.4 x (corr + 1) / 2 (range ratio alone
    when the contours cannot be compared); None without F0 ranges."""
    dub, orig = dub or {}, orig or {}
    ratio = f0_range_ratio(dub.get("f0_range_st"), orig.get("f0_range_st"))
    corr = contour_correlation(dub.get("contour"), orig.get("contour"))
    if ratio is None:
        sim = None
    elif corr is None:
        sim = ratio
    else:
        sim = 0.6 * ratio + 0.4 * (corr + 1.0) / 2.0
    return {"range_ratio": ratio, "contour_corr": corr, "sim": sim}


def duration_ratio(dur_s: float | None, target_s: float | None) -> float | None:
    if not dur_s or not target_s or target_s <= 0:
        return None
    return float(dur_s) / float(target_s)


def max_ratio_for(fixed_length: bool, max_duration_ratio: float, fixed_length_tolerance: float) -> float:
    """Allowed upper bound of dur/target: tight for fixed-length lines (cutscenes, timelines)."""
    return 1.0 + fixed_length_tolerance if fixed_length else max_duration_ratio


@dataclass
class AudioLevels:
    dur_s: float
    sr: int
    peak_db: float  # sample peak, dBFS
    rms_db: float  # RMS over the whole file, dBFS
    active_rms_db: float  # RMS over the loudest 50 ms frames (speech level), dBFS
    clip_frac: float  # share of samples at full scale


_FLOOR_DB = -120.0


def _db(x: float) -> float:
    return float(20.0 * np.log10(max(x, 1e-6))) if x > 0 else _FLOOR_DB


def audio_levels(path: Path) -> AudioLevels:
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    n = mono.shape[0]
    if n == 0:
        return AudioLevels(0.0, int(sr), _FLOOR_DB, _FLOOR_DB, _FLOOR_DB, 0.0)
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
    frame = max(1, int(sr * 0.05))
    n_frames = n // frame
    if n_frames >= 1:
        frames = mono[: n_frames * frame].reshape(n_frames, frame)
        frame_rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
        top = np.sort(frame_rms)[-max(1, n_frames // 2) :]
        active = float(np.sqrt(np.mean(np.square(top))))
    else:
        active = rms
    clip_frac = float(np.mean(np.abs(data) >= 0.999))
    return AudioLevels(n / float(sr), int(sr), _db(peak), _db(rms), _db(active), clip_frac)


def unit(vec: Sequence[float] | np.ndarray) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else v


def cosine(a: Sequence[float] | np.ndarray, b: Sequence[float] | np.ndarray) -> float:
    return float(np.dot(unit(a), unit(b)))


def identity_margin(
    vec: Sequence[float] | np.ndarray,
    own: str,
    centroids: dict[str, np.ndarray],
    same_voice: float,
) -> tuple[float | None, str | None]:
    """(margin, closest character) of a dub among the characters' voice centroids.

    margin = cosine to the own centroid - the best cosine to another character. A character
    whose centroid is at least `same_voice` similar to the own one (a merged voice cluster, one
    actor in two roles) is the same voice, not another character. Needs two centroids.
    """
    if len(centroids) < 2:
        return None, None
    sims = {cid: cosine(vec, c) for cid, c in centroids.items()}
    closest = max(sims, key=lambda cid: sims[cid])
    own_c = centroids.get(own)
    if own_c is None:
        return None, closest
    others = [s for cid, s in sims.items() if cid != own and cosine(own_c, centroids[cid]) < same_voice]
    if not others:
        return None, closest
    return sims[own] - max(others), closest


def mean_pairwise_cosine(embs: Sequence[np.ndarray]) -> float | None:
    """Average cosine over all distinct pairs (the voice baseline of a speaker)."""
    if len(embs) < 2:
        return None
    mat = np.stack([unit(e) for e in embs])
    sims = mat @ mat.T
    iu = np.triu_indices(len(embs), k=1)
    return float(np.mean(sims[iu]))
