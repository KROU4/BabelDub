"""Composite best-of ranking (tts.best_of_mode=score): which candidate take of a line to keep.

Gates first: the CER of a candidate may not exceed max(qa.max_cer, best CER + CER_SLACK), its speech
length must be within LEN_GATE (x1.25) of the original's speech span, and its MOS may not fall below the
character's originals' median MOS - qa.mos_drop (when both are known). When no candidate passes every
gate, the ones that pass the CER and length gates are ranked; when none passes those either, all are.
Then the score, with the weights of `qa.rank_weights` (defaults in core.project.DEFAULT_SETTINGS):

  0.25 x prosody     mean of the prosody similarity to the original (F0 range and contour), the F0-median
                     fit (1 at the original's median F0, 0 at RANGE_ST = 6 semitones away) and the emotion
                     cosine, of those measured
  0.10 x MOS         among the line's candidates: (mos - min) / max(spread, 1 MOS point)
  0.20 x liveliness  mean of min(dub / original, 1) of the F0 range and of the loudness dynamics (spread
                     of the active frame levels): a take flatter than the original loses, a livelier one
                     does not gain
  0.15 x length fit  1 at the original's speech length, 0 at the x1.25 gate
  0.30 x voice SIM   ECAPA cosine to the original; `sim_rvc` (0.05) instead when an RVC model sets the
                     timbre later and the candidates were scored before conversion

A fixed_length (cutscene) line weighs the length fit `length_fixed` (0.30) instead, since a take that ends
early or late cannot be timed without slowing or squeezing it; the extra weight is taken in equal halves
from prosody and liveliness. A metric that could not be measured counts as 0.5. CER is only a gate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from babeldub.core.project import DEFAULT_SETTINGS

DEFAULT_WEIGHTS: dict[str, float] = dict(DEFAULT_SETTINGS["qa"]["rank_weights"])
WEIGHT_KEYS = tuple(DEFAULT_WEIGHTS)
SCORE_KEYS = ("prosody", "mos", "liveliness", "length", "sim")  # the terms of the score
CER_SLACK = 0.05
LEN_GATE = math.log(1.25)
NEUTRAL = 0.5
MOS_MIN_SPAN = 1.0  # a 0.2 MOS lead is 0.2 of the MOS term, not all of it
RANGE_ST = 6.0  # F0-median fit: 0 this many semitones away from the original's median
# kept for readers of v0.3 results rows (`best_of.sim_weight`)
W_SIM, W_SIM_RVC = DEFAULT_WEIGHTS["sim"], DEFAULT_WEIGHTS["sim_rvc"]


@dataclass
class Candidate:
    seed: int
    wav: str
    speech_s: float
    cer: float | None = None
    sim: float | None = None
    mos: float | None = None
    emo_cos: float | None = None
    prosody_sim: float | None = None
    f0_shift_st: float | None = None  # median F0 of the take vs the original line, semitones
    liveliness: float | None = None  # `liveliness()` of the take against the original
    passed: bool = True
    mos_gate: bool | None = None  # False: below the character's MOS floor; None: not checked
    score: float = 0.0
    parts: dict[str, float] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        def r(v: float | None) -> float | None:
            return None if v is None else round(float(v), 4)

        return {"seed": self.seed, "speech_s": round(self.speech_s, 3), "cer": r(self.cer), "sim": r(self.sim),
                "mos": r(self.mos), "emo_cos": r(self.emo_cos), "prosody_sim": r(self.prosody_sim),
                "f0_shift_st": r(self.f0_shift_st), "liveliness": r(self.liveliness), "mos_gate": self.mos_gate,
                "passed": self.passed, "score": round(self.score, 4)}


def check_weights(value: Any) -> str | None:
    """Why `qa.rank_weights` is not usable, or None: a section of known keys with numbers in [0, 1] whose
    score terms do not all weigh zero (null = the defaults)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return "qa.rank_weights is a section: set its keys, e.g. qa.rank_weights.sim=0.3"
    unknown = sorted(set(value) - set(WEIGHT_KEYS))
    if unknown:
        return f"unknown rank weight(s) {unknown}; known: {list(WEIGHT_KEYS)}"
    for key, v in value.items():
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= float(v) <= 1.0:
            return f"qa.rank_weights.{key} must be a number from 0 to 1, got {v!r}"
    merged = weights_from(value)
    if sum(merged[k] for k in SCORE_KEYS) <= 0:
        return "qa.rank_weights: at least one of " + ", ".join(SCORE_KEYS) + " must weigh more than 0"
    return None


def weights_from(value: Any) -> dict[str, float]:
    """The defaults overridden by the valid numbers of a `qa.rank_weights` section."""
    out = dict(DEFAULT_WEIGHTS)
    for key, v in (value or {}).items() if isinstance(value, dict) else ():
        if key in out and isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= float(v) <= 1.0:
            out[key] = float(v)
    return out


def effective_weights(weights: dict[str, float] | None = None, *, rvc: bool = False,
                      fixed_length: bool = False) -> dict[str, float]:
    """{prosody, mos, liveliness, length, sim} used for one line."""
    w = weights_from(weights)
    out = {k: w[k] for k in SCORE_KEYS}
    if rvc:
        out["sim"] = w["sim_rvc"]
    if fixed_length and w["length_fixed"] > out["length"]:
        extra = w["length_fixed"] - out["length"]
        out["length"] = w["length_fixed"]
        out["prosody"] = max(0.0, out["prosody"] - extra / 2)
        out["liveliness"] = max(0.0, out["liveliness"] - extra / 2)
    return {k: round(v, 4) for k, v in out.items()}


def length_fit(speech_s: float, target_s: float) -> float:
    """1 when the take speaks as long as the original, falling to 0 at the x1.25 gate (either way)."""
    if speech_s <= 0 or target_s <= 0:
        return 0.0
    return max(0.0, 1.0 - abs(math.log(speech_s / target_s)) / LEN_GATE)


def f0_median_fit(shift_st: float | None) -> float | None:
    """1 at the original's median F0, falling linearly to 0 at RANGE_ST semitones either way."""
    return None if shift_st is None else max(0.0, 1.0 - abs(float(shift_st)) / RANGE_ST)


def liveliness(dub: dict[str, Any] | None, orig: dict[str, Any] | None) -> float | None:
    """Mean of min(dub / original, 1) of the F0 range (`f0_range_st`) and the loudness dynamics
    (`rms_std_db`) of two `prosody` replies; None when neither can be compared. (The syllable-rate
    variability is not measured per candidate take, so it is not part of it.)"""
    dub, orig = dub or {}, orig or {}
    ratios: list[float] = []
    for key in ("f0_range_st", "rms_std_db"):
        d, o = dub.get(key), orig.get(key)
        if isinstance(d, (int, float)) and isinstance(o, (int, float)) and o > 0 and d >= 0:
            ratios.append(min(float(d) / float(o), 1.0))
    return sum(ratios) / len(ratios) if ratios else None


def _clip01(v: float | None) -> float | None:
    return None if v is None else min(max(float(v), 0.0), 1.0)


def _relative(values: list[float | None], min_span: float) -> list[float]:
    """(v - min) / max(spread, min_span); NEUTRAL for unknown values or when nothing differs."""
    known = [v for v in values if v is not None]
    if len(known) < 2 or max(known) - min(known) < 1e-9:
        return [NEUTRAL] * len(values)
    lo, span = min(known), max(max(known) - min(known), min_span)
    return [NEUTRAL if v is None else (v - lo) / span for v in values]


def rank(cands: list[Candidate], *, target_s: float, max_cer: float, rvc: bool = False,
         fixed_length: bool = False, mos_floor: float | None = None,
         weights: dict[str, float] | None = None) -> Candidate:
    """Score every candidate in place and return the winner (needs at least one candidate).
    `rvc`: the candidates' timbre is not final (an RVC model converts the winner later)."""
    cers = [c.cer for c in cands if c.cer is not None]
    cer_cap = max(max_cer, min(cers) + CER_SLACK) if cers else math.inf
    basic: list[Candidate] = []
    for c in cands:
        ok = (c.cer is None or c.cer <= cer_cap) and \
            c.speech_s > 0 and target_s > 0 and abs(math.log(c.speech_s / target_s)) <= LEN_GATE
        c.mos_gate = None if mos_floor is None or c.mos is None else c.mos >= mos_floor
        c.passed = ok and c.mos_gate is not False
        if ok:
            basic.append(c)
    pool = [c for c in cands if c.passed] or basic or cands
    mos_n = _relative([c.mos for c in pool], MOS_MIN_SPAN)
    w = effective_weights(weights, rvc=rvc, fixed_length=fixed_length)
    for c, mos in zip(pool, mos_n, strict=True):
        prosody = [v for v in (_clip01(c.prosody_sim), f0_median_fit(c.f0_shift_st), _clip01(c.emo_cos))
                   if v is not None]
        sim, lively = _clip01(c.sim), _clip01(c.liveliness)
        c.parts = {
            "prosody": sum(prosody) / len(prosody) if prosody else NEUTRAL,
            "mos": mos,
            "liveliness": NEUTRAL if lively is None else lively,
            "length": length_fit(c.speech_s, target_s),
            "sim": NEUTRAL if sim is None else sim,
        }
        c.score = sum(w[k] * c.parts[k] for k in SCORE_KEYS)

    def distance(c: Candidate) -> float:
        return abs(math.log(c.speech_s / target_s)) if c.speech_s > 0 and target_s > 0 else math.inf

    return max(pool, key=lambda c: (round(c.score, 9), -distance(c), -c.seed))
