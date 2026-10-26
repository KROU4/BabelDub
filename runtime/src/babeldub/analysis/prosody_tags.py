"""Delivery tags: how a line is delivered relative to the same character's other lines.

Every analysed original line (inventory/analysis.jsonl, `babeldub analyze`) has
  prosody.f0_median_hz, f0_range_st (pyin 10-90 %), rms_db (active RMS), voiced_ratio (voiced frames / file),
  prosody.syl_rate (syllables of the source text / speech seconds), speech_s, dur_s.
Each feature of a line becomes a robust leave-one-out z-score against the same character's OTHER lines:
  z = (x - median) / max(1.4826 * MAD, floor)      (the floor keeps small casts from producing huge z)
Pitch range and syllable rate are only trusted on lines with >= LONG_S of speech (and measured against such
lines); a feature needs at least MIN_POPULATION comparable lines. Strong deviations become short English
delivery tags (at most MAX_TAGS) from the public vocabulary TAGS; their phrases form the VoxCPM2 control
text "(phrase, phrase)" placed before the dub text.

Pure stdlib + numpy: no project files are read here (analysis.store.measured_tags is the project glue).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# the vocabulary of delivery tags (agent and measured) -> English control phrase for VoxCPM2
TAGS: dict[str, str] = {
    "shouting": "shouting",
    "whispered": "whispering",
    "urgent": "urgent",
    "energetic": "energetic",
    "calm": "calm",
    "sad": "sad",
    "soft": "soft voice",
    "loud": "loud voice",
    "quick": "fast pace",
    "slow": "slow pace",
    "expressive": "expressive intonation",
    "flat": "flat intonation",
}
VOCABULARY: tuple[str, ...] = tuple(TAGS)
MAX_TAGS = 3
LONG_S = 1.5  # speech seconds a line needs for the range and rate features
MIN_SPEECH_S = 0.3  # shorter lines take no part in the population
MIN_POPULATION = 5  # comparable other lines a feature needs
STRONG = 1.5
MAX_RANGE_ST = 24.0  # a 10-90 % F0 range above two octaves is a pitch-tracking failure
SLIGHT_Z = 2.0  # quick/slow under this |z| of the rate read "slightly faster/slower"
# feature -> (sigma floor, needs a long line)
FEATURES: dict[str, tuple[float, bool]] = {
    "f0": (1.0, False), "rms": (1.0, False), "voic": (0.05, False), "rng": (1.5, True), "rate": (0.35, True),
}
EXCLUDED_CLASSES = frozenset({"nonverbal"})
NO_CHARACTER = frozenset({"", "unknown"})  # a mix of voices: no population to compare with


@dataclass
class Measured:
    """Measured delivery of one line: tags, the z-scores behind them and their reference statistics."""
    tags: list[str]
    z: dict[str, float] = field(default_factory=dict)
    ref: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def control(self) -> str:
        return control_phrase(self.tags, self.z)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def features(row: Mapping[str, Any]) -> dict[str, Any]:
    """Feature vector of one analysis row (None where not measurable) plus speech_s and the long flag."""
    prosody = row.get("prosody") or {}
    speech = _finite(row.get("speech_s")) or 0.0
    dur = _finite(row.get("dur_s")) or 0.0
    f0 = _finite(prosody.get("f0_median_hz"))
    voiced = _finite(prosody.get("voiced_ratio"))
    rng = _finite(prosody.get("f0_range_st"))
    if rng is not None and rng > MAX_RANGE_ST:  # octave jumps of the tracker: neither median nor range is usable
        f0 = rng = None
    return {
        "speech_s": speech,
        "long": speech >= LONG_S,
        "f0": 12.0 * math.log2(f0 / 100.0) if f0 and f0 > 0 else None,  # semitones re 100 Hz
        "rms": _finite(prosody.get("rms_db")),
        # voiced share of the speech, not of the whole file
        "voic": min(1.0, voiced * dur / speech) if voiced is not None and speech > 0.2 and dur > 0 else None,
        "rng": rng,
        "rate": _finite(prosody.get("syl_rate")),
    }


def population(
    analysis: Mapping[str, Mapping[str, Any]], char_of: Mapping[str, str],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """({line id: features}, {char_id: [line id]}) of the comparable lines: analysed, of a known character,
    not nonverbal, with at least MIN_SPEECH_S of speech."""
    feats: dict[str, dict[str, Any]] = {}
    by_char: dict[str, list[str]] = {}
    for lid, row in analysis.items():
        char_id = str(char_of.get(lid) or "")
        if char_id in NO_CHARACTER or row.get("class") in EXCLUDED_CLASSES:
            continue
        if (_finite(row.get("speech_s")) or 0.0) < MIN_SPEECH_S:
            continue
        feats[lid] = features(row)
        by_char.setdefault(char_id, []).append(lid)
    return feats, by_char


def _median_without(ordered: np.ndarray, removed: np.ndarray) -> np.ndarray:
    """np.median of the sorted array `ordered` with one occurrence of removed[i] taken out, for every i, bit for
    bit: index k of the shortened array is ordered[k] below the removed value's rank and ordered[k + 1] from it."""
    rank = np.searchsorted(ordered, removed)  # any occurrence of a repeated value leaves the same array
    m = ordered.size - 1

    def at(k: int) -> np.ndarray:
        return np.where(rank <= k, ordered[k + 1], ordered[k])

    return at(m // 2) if m % 2 else (at(m // 2 - 1) + at(m // 2)) / 2


def _character_z(ids: list[str], feats: Mapping[str, Mapping[str, Any]]) -> dict[str, tuple[dict[str, float], dict[str, dict[str, float]]]]:
    """Leave-one-out robust z of every feature of every line of one character, in O(n log n): the leave-one-out
    median takes at most three values, and each one's MAD is the median of the sorted absolute deviations of
    the whole population without the line's own deviation."""
    out: dict[str, tuple[dict[str, float], dict[str, dict[str, float]]]] = {lid: ({}, {}) for lid in ids}
    for key, (floor, needs_long) in FEATURES.items():
        members = [lid for lid in ids if feats[lid].get(key) is not None and (feats[lid]["long"] or not needs_long)]
        if len(members) <= MIN_POPULATION:  # each line is compared with the others: n - 1 >= MIN_POPULATION
            continue
        values = np.array([float(feats[lid][key]) for lid in members], dtype=float)
        medians = _median_without(np.sort(values), values)
        mads = np.empty_like(values)
        for center in np.unique(medians):
            pick = medians == center
            deviations = np.abs(values - center)
            mads[pick] = _median_without(np.sort(deviations), deviations[pick])
        n = values.size - 1
        for lid, x, med, mad in zip(members, values.tolist(), medians.tolist(), mads.tolist(), strict=True):
            scale = max(1.4826 * mad, floor)
            out[lid][0][key] = round((x - med) / scale, 2)
            out[lid][1][key] = {"x": round(x, 3), "median": round(med, 3), "scale": round(scale, 3), "n": n}
    return out


def zscores(lid: str, char_id: str, feats: Mapping[str, Mapping[str, Any]],
            by_char: Mapping[str, list[str]]) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Robust leave-one-out z of every feature of line `lid` against the character's other lines."""
    if lid not in feats:
        return {}, {}
    return _character_z(list(by_char.get(char_id, [])), feats).get(lid, ({}, {}))


def tags_from_z(z: Mapping[str, float]) -> list[str]:
    """Strong deviations -> ordered delivery tags (at most MAX_TAGS)."""
    def g(key: str) -> float:
        return float(z.get(key, 0.0))

    f0, rms, voic, rng, rate = g("f0"), g("rms"), g("voic"), g("rng"), g("rate")
    # arousal: pitch weighs more than level (game dialogue is loudness-normalised, a shout keeps its pitch)
    arousal = 0.6 * f0 + 0.4 * rms if "f0" in z else rms
    tags: list[str] = []
    if (f0 >= 2.0 and rms >= 1.0) or (rms >= 2.5 and f0 >= 1.0):
        tags.append("shouting")
    elif rms <= -2.0 and ("f0" not in z or voic <= -2.0):
        tags.append("whispered")
    elif arousal >= 1.0 and rate >= 1.0:
        tags.append("urgent")
    elif arousal >= 1.25:
        tags.append("energetic")
    elif f0 <= -1.0 and rms <= -1.0 and (rate <= -0.75 or rng <= -1.0):
        tags.append("sad")
    elif arousal <= -1.25:
        tags.append("calm")
    elif rms <= -STRONG:
        tags.append("soft")
    elif rms >= 2.0:
        tags.append("loud")
    if rate >= STRONG and "urgent" not in tags:
        tags.append("quick")
    elif rate <= -STRONG:
        tags.append("slow")
    if rng >= 2.0:
        tags.append("expressive")
    elif rng <= -2.0 and "whispered" not in tags:
        tags.append("flat")
    return tags[:MAX_TAGS]


def control_phrase(tags: Iterable[str], z: Mapping[str, float] | None = None) -> str:
    """The control text without its parentheses ("shouting, fast pace"); with measured z a tempo tag
    under |z| SLIGHT_Z reads "slightly faster/slower". Unknown tags are skipped."""
    out: list[str] = []
    for tag in tags:
        phrase = TAGS.get(tag)
        if phrase is None:
            continue
        if tag in ("quick", "slow") and z is not None and abs(float(z.get("rate", 0.0))) < SLIGHT_Z:
            phrase = "slightly faster" if tag == "quick" else "slightly slower"
        out.append(phrase)
    return ", ".join(out)


def parse_tags(value: Any) -> list[str]:
    """Validate an agent's delivery: a list of at most MAX_TAGS tags of the vocabulary (case and spaces are
    ignored, repeats dropped). Raises ValueError with the problem."""
    if not isinstance(value, list):
        raise ValueError("delivery must be a list of tags")
    tags: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("delivery tags must be strings")
        tag = item.strip().lower()
        if tag not in TAGS:
            raise ValueError(f"unknown delivery tag {item!r}")
        if tag not in tags:
            tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise ValueError(f"delivery takes at most {MAX_TAGS} tags, got {len(tags)}")
    return tags


def clean_tags(value: Any) -> list[str]:
    """The known tags of a stored delivery (at most MAX_TAGS), never raising."""
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value:
        tag = str(item).strip().lower()
        if tag in TAGS and tag not in tags:
            tags.append(tag)
    return tags[:MAX_TAGS]


def delivery_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """{by_source: {agent|measured|none|dropped: lines}, tags: {tag: lines}} over rows with `tags_source`
    (synth results rows or planned takes); the histogram counts the tags actually sent. None without rows."""
    by_source: dict[str, int] = {}
    histogram: dict[str, int] = {}
    for row in rows:
        source = row.get("tags_source")
        if not source:
            continue
        by_source[str(source)] = by_source.get(str(source), 0) + 1
        if source in ("agent", "measured") and row.get("control"):
            for tag in clean_tags(row.get("tags")):
                histogram[tag] = histogram.get(tag, 0) + 1
    if not by_source:
        return None
    return {"by_source": by_source, "tags": dict(sorted(histogram.items(), key=lambda kv: (-kv[1], kv[0])))}


def measure(analysis: Mapping[str, Mapping[str, Any]], char_of: Mapping[str, str]) -> dict[str, Measured]:
    """{line id: Measured} for every comparable line (population()); a line whose character has too few
    comparable lines gets empty tags, a line outside the population no entry at all."""
    feats, by_char = population(analysis, char_of)
    out: dict[str, Measured] = {}
    for ids in by_char.values():
        for lid, (z, ref) in _character_z(ids, feats).items():
            out[lid] = Measured(tags_from_z(z), z, ref)
    return out
