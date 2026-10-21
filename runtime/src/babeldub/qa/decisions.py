"""Verdicts and automatic decisions for QA rows (thresholds come from project.json `qa`).

Reason codes written to qa.jsonl:
  missing_file  post file is absent            -> no auto decision (attention)
  qc_error      the qc worker failed on it     -> no auto decision (attention; re-run `qa --ids`)
  empty         file has no samples / < 0.1 s  -> regen, then keep_original
  silent        speech level under -50 dBFS    -> regen, then keep_original
  no_dub_text   no translation for the line    -> no auto decision (attention)
  cer           ASR differs from dub text      -> regen, then keep_original
  sim           voice drifted from the hero    -> regen, then keep_original
  identity      dub sounds like another char   -> regen, then keep_original
  peak          peak above the ceiling/clipped -> regen, then keep_original
  duration      longer than allowed            -> shorten (translator agent shortens the text)

Lines flagged `nonverbal` (screams, laughs) skip the voice checks (sim, identity) and CER: a
scream's embedding and its transcript say nothing about the voice or the words.

Similarity calibration on Low-Budget Repairs (Qwen3-TTS Russian dub of English originals,
ECAPA): good dubs scored 0.38-0.66 against the character's centroid, clear failures 0.08-0.11,
while the same-language baseline sim_ref was 0.76. A cross-language dub always scores lower,
so it gets its own ratio (0.45 x 0.76 = 0.34) plus an absolute floor (0.25).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DECISIONS = ("accept", "regen", "reref", "shorten", "retranslate", "keep_original", "skip")
QUALITY_REASONS = frozenset({"empty", "silent", "cer", "sim", "identity", "peak"})
BLOCKING_REASONS = frozenset({"missing_file", "no_dub_text", "qc_error"})
MAX_SHORTEN = 2  # after two shortened texts that still do not fit, the line needs a human


def _lang(code: Any) -> str | None:
    text = str(code or "").strip().replace("_", "-").split("-")[0].lower()
    return text or None


def is_cross_language(source_lang: Any, target_lang: Any) -> bool:
    """The dub is in another language than the original audio (unknown source: assume so)."""
    src, tgt = _lang(source_lang), _lang(target_lang)
    return src is None or tgt is None or src != tgt


@dataclass
class Thresholds:
    max_cer: float = 0.15
    min_sim_ratio: float = 0.75  # same-language dub: sim >= ratio x sim_ref
    min_sim_ratio_xlang: float = 0.45  # dub language differs from the original audio (the normal case)
    min_sim_abs: float = 0.25  # absolute floor of the similarity threshold
    min_identity_margin: float = -0.05  # sim(own centroid) - max sim(other centroids)
    max_duration_ratio: float = 1.35
    fixed_length_tolerance: float = 0.03
    peak_ceiling_db: float = -1.0
    peak_margin_db: float = 0.5
    silence_db: float = -50.0
    max_attempts: int = 3
    cross_lang: bool = True

    @property
    def sim_ratio(self) -> float:
        return self.min_sim_ratio_xlang if self.cross_lang else self.min_sim_ratio

    def sim_threshold(self, sim_ref: float | None) -> float:
        """The bar `sim` must reach: ratio x the character's baseline, never below the floor."""
        return max(self.sim_ratio * sim_ref if sim_ref is not None else 0.0, self.min_sim_abs)

    @classmethod
    def from_project(cls, data: dict[str, Any]) -> Thresholds:
        qa = data.get("qa", {}) or {}
        post = data.get("post", {}) or {}
        tts = data.get("tts", {}) or {}
        base = cls()
        return cls(
            max_cer=float(qa.get("max_cer", base.max_cer)),
            min_sim_ratio=float(qa.get("min_sim_ratio", base.min_sim_ratio)),
            min_sim_ratio_xlang=float(qa.get("min_sim_ratio_xlang", base.min_sim_ratio_xlang)),
            min_sim_abs=float(qa.get("min_sim_abs", base.min_sim_abs)),
            min_identity_margin=float(qa.get("min_identity_margin", base.min_identity_margin)),
            max_duration_ratio=float(qa.get("max_duration_ratio", base.max_duration_ratio)),
            fixed_length_tolerance=float(post.get("fixed_length_tolerance", base.fixed_length_tolerance)),
            peak_ceiling_db=float(post.get("true_peak_db", base.peak_ceiling_db)),
            silence_db=float(qa.get("silence_db", base.silence_db)),
            max_attempts=int(tts.get("max_attempts", base.max_attempts)),
            cross_lang=is_cross_language(data.get("source_lang"), data.get("target_lang")),
        )


def evaluate(
    *,
    th: Thresholds,
    exists: bool,
    dur_s: float | None,
    active_rms_db: float | None,
    peak_db: float | None,
    clip_frac: float | None,
    has_text: bool,
    cer: float | None,
    sim: float | None,
    sim_ref: float | None,
    ratio: float | None,
    fixed_length: bool,
    qc_error: bool = False,
    margin: float | None = None,
    nonverbal: bool = False,
) -> tuple[str, list[str]]:
    """Apply the thresholds; returns (verdict, reasons)."""
    reasons: list[str] = []
    if not exists:
        return "fail", ["missing_file"]
    if not dur_s or dur_s < 0.1:
        return "fail", ["empty"]
    if active_rms_db is not None and active_rms_db < th.silence_db:
        return "fail", ["silent"]
    if qc_error:
        reasons.append("qc_error")
    if not has_text:
        reasons.append("no_dub_text")
    elif cer is not None and cer > th.max_cer and not nonverbal:
        reasons.append("cer")
    if not nonverbal:
        if sim is not None and sim < th.sim_threshold(sim_ref):
            reasons.append("sim")
        if margin is not None and margin < th.min_identity_margin:
            reasons.append("identity")
    limit = 1.0 + th.fixed_length_tolerance if fixed_length else th.max_duration_ratio
    if ratio is not None and ratio > limit:
        reasons.append("duration")
    clipped = clip_frac is not None and clip_frac > 0.0005
    if (peak_db is not None and peak_db > th.peak_ceiling_db + th.peak_margin_db) or clipped:
        reasons.append("peak")
    return ("pass" if not reasons else "fail"), reasons


@dataclass
class AutoDecision:
    decision: str | None  # None: nothing can be decided automatically
    attention: bool
    note: str


def auto_decide(verdict: str, reasons: list[str], attempt: int, th: Thresholds, shortened: int = 0) -> AutoDecision:
    """Decision for one QA row.

    pass -> accept. Quality failures (cer/sim/identity/peak/empty/silent) -> regen while attempts remain,
    then keep_original with attention. Too long -> shorten (the translator agent rewrites the
    text), at most MAX_SHORTEN times, then keep_original with attention.
    """
    if verdict == "pass":
        return AutoDecision("accept", False, "all checks passed")
    blocking = BLOCKING_REASONS.intersection(reasons)
    if blocking:
        return AutoDecision(None, True, "needs attention: " + ",".join(sorted(blocking)))
    quality = sorted(QUALITY_REASONS.intersection(reasons))
    too_long = "duration" in reasons
    if quality and attempt < th.max_attempts:
        return AutoDecision("regen", False, f"{','.join(quality)} failed on attempt {attempt}/{th.max_attempts}")
    if too_long and shortened < MAX_SHORTEN:
        return AutoDecision("shorten", True, "too long for the original timing: shorten the dub text")
    if too_long:
        return AutoDecision("keep_original", True, f"still too long after {shortened} shortened texts")
    return AutoDecision(
        "keep_original", True, f"{','.join(quality)} still failing after {attempt} attempts: original kept"
    )


def needs_attention(decision_row: dict[str, Any] | None, verdict: str | None) -> bool:
    """Outstanding item: an auto decision flagged for review, or a failed row without decision."""
    if decision_row is None:
        return verdict == "fail"
    if decision_row.get("by") in ("agent", "human"):
        return False
    return bool(decision_row.get("attention"))


def validate_agent_row(row: dict[str, Any], known_ids: set[str]) -> list[str]:
    """Problems of one decisions.agent.jsonl row (empty list = valid)."""
    problems: list[str] = []
    rid = row.get("id")
    if not isinstance(rid, str) or not rid:
        problems.append("missing 'id'")
    elif rid not in known_ids:
        problems.append(f"unknown id '{rid}'")
    decision = row.get("decision")
    if decision not in DECISIONS:
        problems.append(f"decision must be one of {'|'.join(DECISIONS)}, got {decision!r}")
    attempt = row.get("attempt")
    if attempt is not None and (not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1):
        problems.append("attempt must be a positive integer")
    note = row.get("note")
    if note is not None and not isinstance(note, str):
        problems.append("note must be a string")
    return problems
