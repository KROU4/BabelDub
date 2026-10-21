"""QA metrics: text normalization, character error rate, audio level checks, voice similarity
and the speaker identity margin.

CER is computed on normalized text (lower case, no punctuation or markup, ё -> е, digits kept
as they are), with spaces counted as characters. The Levenshtein distance is a row-vectorized
numpy implementation, so no extra dependency is needed in the CLI.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

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


def cer(reference: str | None, hypothesis: str | None) -> float:
    """Character error rate of the hypothesis (ASR) against the reference (dub text)."""
    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(ref, hyp) / len(ref)


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
