"""Composite best-of ranking (tts.best_of_mode=score): which candidate take of a line to keep.

Gates first: the CER of a candidate may not exceed max(qa.max_cer, best CER + CER_SLACK) and its
speech length must be within LEN_GATE (x1.25) of the original's speech span; when no candidate
passes, all are ranked. Then the score:

  0.35 x expressiveness  (mean of the prosody similarity to the original and the emotion cosine)
  0.25 x MOS             (UTMOS22 among the line's candidates: (mos - min) / max(spread, 1 MOS point))
  0.20 x length fit      (1 at the original's speech length, 0 at the x1.25 gate)
  0.20 x voice SIM       (ECAPA cosine to the original; 0.05 when an RVC model sets the timbre later)

A fixed_length (cutscene) line weighs the length fit 0.35 and expressiveness 0.20, since a take that
ends early or late cannot be timed without slowing or squeezing it.
A metric that could not be measured counts as 0.5. CER is only a gate, never a score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

W_EXPRESSIVE, W_MOS, W_LENGTH, W_SIM, W_SIM_RVC = 0.35, 0.25, 0.20, 0.20, 0.05
W_EXPRESSIVE_FIXED, W_LENGTH_FIXED = 0.20, 0.35  # fixed_length lines: timing matters more
CER_SLACK = 0.05
LEN_GATE = math.log(1.25)
NEUTRAL = 0.5
MOS_MIN_SPAN = 1.0  # a 0.2 MOS lead is 0.2 of the MOS term, not all of it


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
    passed: bool = True
    score: float = 0.0
    parts: dict[str, float] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        def r(v: float | None) -> float | None:
            return None if v is None else round(float(v), 4)

        return {"seed": self.seed, "speech_s": round(self.speech_s, 3), "cer": r(self.cer), "sim": r(self.sim),
                "mos": r(self.mos), "emo_cos": r(self.emo_cos), "prosody_sim": r(self.prosody_sim),
                "passed": self.passed, "score": round(self.score, 4)}


def length_fit(speech_s: float, target_s: float) -> float:
    """1 when the take speaks as long as the original, falling to 0 at the x1.25 gate (either way)."""
    if speech_s <= 0 or target_s <= 0:
        return 0.0
    return max(0.0, 1.0 - abs(math.log(speech_s / target_s)) / LEN_GATE)


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
         fixed_length: bool = False) -> Candidate:
    """Score every candidate in place and return the winner (needs at least one candidate)."""
    cers = [c.cer for c in cands if c.cer is not None]
    cer_cap = max(max_cer, min(cers) + CER_SLACK) if cers else math.inf
    for c in cands:
        c.passed = (c.cer is None or c.cer <= cer_cap) and \
            c.speech_s > 0 and target_s > 0 and abs(math.log(c.speech_s / target_s)) <= LEN_GATE
    pool = [c for c in cands if c.passed] or cands
    mos_n = _relative([c.mos for c in pool], MOS_MIN_SPAN)
    w_sim = W_SIM_RVC if rvc else W_SIM
    w_expr, w_len = (W_EXPRESSIVE_FIXED, W_LENGTH_FIXED) if fixed_length else (W_EXPRESSIVE, W_LENGTH)
    for c, mos in zip(pool, mos_n, strict=True):
        expressive = [v for v in (_clip01(c.prosody_sim), _clip01(c.emo_cos)) if v is not None]
        sim = _clip01(c.sim)
        c.parts = {
            "expressive": sum(expressive) / len(expressive) if expressive else NEUTRAL,
            "mos": mos,
            "length": length_fit(c.speech_s, target_s),
            "sim": NEUTRAL if sim is None else sim,
        }
        c.score = (w_expr * c.parts["expressive"] + W_MOS * c.parts["mos"]
                   + w_len * c.parts["length"] + w_sim * c.parts["sim"])
    def distance(c: Candidate) -> float:
        return abs(math.log(c.speech_s / target_s)) if c.speech_s > 0 and target_s > 0 else math.inf

    return max(pool, key=lambda c: (round(c.score, 9), -distance(c), -c.seed))
