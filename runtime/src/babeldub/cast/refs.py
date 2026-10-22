"""Voice references: the cleanest, most typical original lines of each character.

A reference is an original game line (3-12 s by default) with a known source text, without
music bed / non-verbal / alternative-take flags, with a normal level (not clipped, not silent).
When speaker embeddings are available, lines closest to the character's centroid win: they
carry the most typical timbre of the hero.

Arousal buckets (emotion_banks): every analyzed line of a character falls into calm, animated,
loud or soft by its prosody relative to the character's own medians (inventory/analysis.jsonl:
active RMS, F0 range in semitones, syllable rate). A line goes to the nearest bucket prototype in
a (loudness, liveliness) plane: loudness = dRMS / 6 dB, liveliness = dF0range / 4 st + drate / 25 %.
An emotion label (opt-in emotion2vec+) moves a line that prosody calls calm: sad -> soft,
angry -> loud, happy/surprised/fearful -> animated. Without analysis every line is calm.
"""

from __future__ import annotations

import shutil
import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from babeldub.cast.characters import line_duration, line_text, wav_path
from babeldub.cast.cluster import centroid_of, l2_normalize
from babeldub.core import layout
from babeldub.qa.metrics import AudioLevels, audio_levels

REF_BAD_FLAGS = frozenset({"has_bed", "nonverbal", "alt_take", "no_text", "no_audio"})
RELAXED_MIN_S = 1.5
RELAXED_MAX_S = 20.0
IDEAL_REF_S = 7.0
POOL_MAX = 60  # candidates whose audio is analysed
EMBED_MAX = 24  # candidates embedded to find the most typical timbre
MAX_CLIP_FRAC = 0.0005
MIN_PEAK_DB = -40.0
MIN_ACTIVE_DB = -40.0
BUCKETS = ("calm", "animated", "loud", "soft")
# bucket prototypes in the (loudness, liveliness) plane, see the module docstring
PROTOTYPES = {"calm": (0.0, 0.0), "animated": (0.2, 1.0), "loud": (1.0, 0.6), "soft": (-1.0, -0.3)}
EMOTION_BUCKET = {"sad": "soft", "angry": "loud", "happy": "animated", "surprised": "animated", "fearful": "animated"}
LOUD_DB, LIVELY_ST, LIVELY_RATE = 6.0, 4.0, 0.25
MIN_STATS_LINES = 3  # fewer analyzed lines: no medians, every line is calm


@dataclass
class RefCandidate:
    line: dict[str, Any]
    wav: Path
    dur_s: float
    text: str | None
    levels: AudioLevels | None = None
    heuristic: float = 0.0
    centroid_cos: float | None = None
    score: float = 0.0

    @property
    def line_id(self) -> str:
        return self.line["id"]


def level_ok(levels: AudioLevels) -> bool:
    return (
        levels.clip_frac <= MAX_CLIP_FRAC
        and levels.peak_db >= MIN_PEAK_DB
        and levels.active_rms_db >= MIN_ACTIVE_DB
    )


def heuristic_score(dur_s: float, levels: AudioLevels) -> float:
    """0..1: duration close to ~7 s and a healthy speech level."""
    dur_part = 1.0 - min(abs(dur_s - IDEAL_REF_S) / IDEAL_REF_S, 1.0)
    level_part = 1.0 if -30.0 <= levels.active_rms_db <= -8.0 else 0.6
    return 0.6 * dur_part + 0.4 * level_part


def _pool(root: Path, lines: list[dict[str, Any]], lo: float, hi: float, need_text: bool) -> list[RefCandidate]:
    out: list[RefCandidate] = []
    for line in lines:
        if REF_BAD_FLAGS.intersection(line.get("flags") or []):
            continue
        text = line_text(line)
        if need_text and not text:
            continue
        wav = wav_path(root, line)
        if wav is None or not wav.exists():
            continue
        dur = line_duration(root, line)
        if lo <= dur <= hi:
            out.append(RefCandidate(line=line, wav=wav, dur_s=dur, text=text))
    return out


def candidate_pool(root: Path, lines: list[dict[str, Any]], min_s: float, max_s: float) -> tuple[list[RefCandidate], str | None]:
    """Eligible lines, relaxing the rules step by step when a character has nothing ideal.

    Returns (candidates, relaxation) where relaxation is None, "duration", "no_text" or
    "duration+no_text".
    """
    steps: list[tuple[float, float, bool, str | None]] = [
        (min_s, max_s, True, None),
        (min(min_s, RELAXED_MIN_S), max(max_s, RELAXED_MAX_S), True, "duration"),
        (min_s, max_s, False, "no_text"),
        (min(min_s, RELAXED_MIN_S), max(max_s, RELAXED_MAX_S), False, "duration+no_text"),
    ]
    for lo, hi, need_text, relax in steps:
        pool = _pool(root, lines, lo, hi, need_text)
        if pool:
            return pool, relax
    return [], None


def rank_candidates(
    pool: list[RefCandidate],
    embed: Callable[[RefCandidate], np.ndarray | None] | None = None,
) -> list[RefCandidate]:
    """Analyse levels, drop broken audio, score by duration/level and centroid similarity."""
    pool = sorted(pool, key=lambda c: (abs(c.dur_s - IDEAL_REF_S), c.line_id))[:POOL_MAX]
    good: list[RefCandidate] = []
    for cand in pool:
        try:
            cand.levels = audio_levels(cand.wav)
        except RuntimeError:
            continue
        if not level_ok(cand.levels):
            continue
        cand.heuristic = heuristic_score(cand.dur_s, cand.levels)
        cand.score = 0.4 * cand.heuristic
        good.append(cand)
    good.sort(key=lambda c: (-c.heuristic, c.line_id))
    if embed is not None and good:
        vecs: dict[str, np.ndarray] = {}
        for cand in good[:EMBED_MAX]:
            vec = embed(cand)
            if vec is not None:
                vecs[cand.line_id] = l2_normalize(vec)[0]
        centroid = centroid_of(list(vecs.values())) if len(vecs) >= 2 else None
        if centroid is not None:
            for cand in good:
                vec = vecs.get(cand.line_id)
                if vec is not None:
                    cand.centroid_cos = float(np.dot(vec, centroid))
                    cand.score = 0.4 * cand.heuristic + 0.6 * max(cand.centroid_cos, 0.0)
    good.sort(key=lambda c: (-c.score, c.line_id))
    return good


def ref_rel_path(char_id: str, uid: str) -> str:
    return f"{layout.REFS_DIR}/{char_id}/{uid}.wav"


def install_refs(root: Path, char_id: str, chosen: list[RefCandidate], force: bool = False) -> None:
    """Copy reference wavs to cast/refs/<char_id>/<uid>.wav; drop stale copies of that character."""
    folder = root / layout.REFS_DIR / char_id
    folder.mkdir(parents=True, exist_ok=True)
    keep = set()
    for cand in chosen:
        dst = root / ref_rel_path(char_id, cand.line["uid"])
        keep.add(dst.name)
        if force or not dst.exists() or dst.stat().st_size != cand.wav.stat().st_size:
            shutil.copy2(cand.wav, dst)
    for old in folder.glob("*.wav"):
        if old.name not in keep:
            old.unlink()


def ref_rows(char_id: str, chosen: list[RefCandidate], default_lang: str | None,
             buckets: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """refs.jsonl rows; `buckets` ({line id: arousal bucket}) adds `bucket` to each row."""
    rows = []
    for rank, cand in enumerate(chosen):
        text_info = cand.line.get("text") or {}
        lang = text_info.get("src_lang") or default_lang or (cand.line.get("locale") or "")[:2] or None
        rows.append(
            {
                "ref_id": f"{char_id}:{cand.line['uid']}",
                "char_id": char_id,
                "line_id": cand.line_id,
                "wav": ref_rel_path(char_id, cand.line["uid"]),
                "text": cand.text,
                "lang": lang,
                "dur_s": round(cand.dur_s, 3),
                "score": round(cand.score, 4),
                "chosen": rank == 0,
                "centroid_cos": None if cand.centroid_cos is None else round(cand.centroid_cos, 4),
                "active_rms_db": None if cand.levels is None else round(cand.levels.active_rms_db, 1),
            }
        )
        if buckets is not None:
            rows[-1]["bucket"] = buckets.get(cand.line_id, "calm")
    return rows


# ---- arousal buckets -------------------------------------------------------------------------
@dataclass
class ProsodyStats:
    """A character's medians of the prosody features (None when unknown)."""
    rms_db: float | None
    f0_range_st: float | None
    syl_rate: float | None


def _median(values: Iterable[Any]) -> float | None:
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    return statistics.median(nums) if nums else None


def prosody_stats(rows: Iterable[dict[str, Any]]) -> ProsodyStats | None:
    """Medians over a character's analysis rows; None with fewer than MIN_STATS_LINES measured lines."""
    pros = [r.get("prosody") or {} for r in rows]
    pros = [p for p in pros if isinstance(p.get("rms_db"), (int, float))]
    if len(pros) < MIN_STATS_LINES:
        return None
    return ProsodyStats(_median(p.get("rms_db") for p in pros), _median(p.get("f0_range_st") for p in pros),
                        _median(p.get("syl_rate") for p in pros))


def arousal_vector(prosody: dict[str, Any] | None, stats: ProsodyStats | None) -> tuple[float, float] | None:
    """(loudness, liveliness) of one line relative to its character, None without data."""
    if not prosody or stats is None:
        return None

    def num(key: str) -> float | None:
        v = prosody.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    rms, rng, rate = num("rms_db"), num("f0_range_st"), num("syl_rate")
    if rms is None and rng is None:
        return None
    loud = (rms - stats.rms_db) / LOUD_DB if rms is not None and stats.rms_db is not None else 0.0
    live = (rng - stats.f0_range_st) / LIVELY_ST if rng is not None and stats.f0_range_st is not None else 0.0
    if rate and stats.syl_rate:
        live += (rate / stats.syl_rate - 1.0) / LIVELY_RATE
    return loud, live


def ranked_buckets(vec: tuple[float, float] | None, allowed: Iterable[str] = BUCKETS,
                   emotion: str | None = None) -> list[str]:
    """Allowed buckets from the nearest to the farthest (calm first without a vector)."""
    wanted = set(allowed)
    pool = [b for b in BUCKETS if b in wanted]
    if not pool:
        return []
    if vec is None:
        return sorted(pool, key=lambda b: b != "calm")
    order = sorted(pool, key=lambda b: ((vec[0] - PROTOTYPES[b][0]) ** 2 + (vec[1] - PROTOTYPES[b][1]) ** 2,
                                        BUCKETS.index(b)))
    moved = EMOTION_BUCKET.get(str(emotion or "").lower())
    if order[0] == "calm" and moved in order:
        order.remove(moved)
        order.insert(0, moved)
    return order


@dataclass
class Arousal:
    """Where one line sits: its vector and emotion label (for the nearest of the available buckets)."""
    vec: tuple[float, float] | None
    emotion: str | None

    def ranked(self, allowed: Iterable[str] = BUCKETS) -> list[str]:
        return ranked_buckets(self.vec, allowed, self.emotion)

    @property
    def bucket(self) -> str:
        return self.ranked()[0]


def line_arousal(lines: Iterable[dict[str, Any]], analysis: dict[str, dict[str, Any]]) -> dict[str, Arousal]:
    """{line id: Arousal} for the lines of ONE character (their analysis rows give the medians)."""
    lines = list(lines)
    stats = prosody_stats(analysis[ln["id"]] for ln in lines if ln["id"] in analysis)
    out: dict[str, Arousal] = {}
    for ln in lines:
        row = analysis.get(ln["id"]) or {}
        out[ln["id"]] = Arousal(arousal_vector(row.get("prosody"), stats), (row.get("emotion") or {}).get("label"))
    return out


def emotion_banks(lines: Iterable[dict[str, Any]], analysis: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """{bucket: line ids} of the character's clean original lines (no bad flags, class speech or unknown)."""
    lines = list(lines)
    arousal = line_arousal(lines, analysis)
    banks: dict[str, list[str]] = {}
    for ln in lines:
        if REF_BAD_FLAGS.intersection(ln.get("flags") or []):
            continue
        if (analysis.get(ln["id"]) or {}).get("class") not in (None, "speech"):
            continue
        banks.setdefault(arousal[ln["id"]].bucket, []).append(ln["id"])
    return {b: banks[b] for b in BUCKETS if b in banks}
