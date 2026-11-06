"""Voice baseline and voice centroid of each character, from his own clean original lines.

Similarity of a dub to the original is judged relative to the baseline (sim >= ratio *
sim_ref), because absolute cosine values differ between voices, and cross-lingual similarity
is lower than monolingual. Cached in qa/baseline.json. The centroids (mean embedding of the
same lines) tell whose voice a dub is closest to (`cast` merges, the QA identity margin).

One definition (SIM_DEFINITION): QA's `sim` and `sim_ref` are both line-to-line cosines. `sim` is the
mean of the dub's cosine to its own original line and to the character's chosen reference line;
`sim_ref` is the mean pairwise cosine of up to BASELINE_LINES longest clean originals. So the ratio
sim / sim_ref compares like with like. `sim_ref_loo` (each clean original against the centroid of the
others) is recorded for reports only: centroid cosines run higher than line-to-line ones and must not
be compared with `sim` or `sim_ref`.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

import numpy as np

from babeldub.cast.characters import UNKNOWN_ID, line_duration, wav_path
from babeldub.cast.cluster import Embedder, centroid_of
from babeldub.core.jsonl import now_iso, read_json, write_json
from babeldub.qa.metrics import mean_loo_centroid_cosine, mean_pairwise_cosine

BASELINE = "qa/baseline.json"
# used when no character has two clean original lines; Low-Budget Repairs measured 0.44-0.65
# between lines of one actor, and 0.39-0.47 between a Qwen3-TTS Russian dub and its English original
DEFAULT_SIM_REF = 0.5
BASELINE_LINES = 12
MIN_CENTROID_LINES = 2  # a voice centroid from a single line is too noisy to compare voices with
SIM_BAD_FLAGS = frozenset({"has_bed", "nonverbal", "alt_take", "no_audio"})
MIN_SIM_S = 1.0
SIM_DEFINITION = ("line-to-line: sim = mean cosine of the dub to its original line and to the chosen reference; "
                  "sim_ref = mean pairwise cosine of the clean originals; sim_ref_loo (reports only) = mean cosine "
                  "of each clean original to the centroid of the others")


def clean_original(root: Path, line: dict[str, Any], min_s: float = MIN_SIM_S) -> Path | None:
    """Original wav usable for voice comparison: no music bed / non-verbal flags, >= `min_s` (1 s)."""
    if SIM_BAD_FLAGS.intersection(line.get("flags") or []):
        return None
    wav = wav_path(root, line)
    if wav is None or not wav.exists():
        return None
    if line_duration(root, line) < min_s:
        return None
    return wav


def char_f0_hints(analysis: dict[str, dict[str, Any]], assign: dict[str, str], min_lines: int = 3) -> dict[str, float]:
    """{char_id: median of the per-line median F0 in analysis.jsonl} of characters with >= `min_lines`
    measured lines: the hint that bounds pyin to one octave either side of the character's voice."""
    values: dict[str, list[float]] = {}
    for lid, row in analysis.items():
        hz = ((row or {}).get("prosody") or {}).get("f0_median_hz")
        cid = assign.get(lid)
        if cid and cid != UNKNOWN_ID and isinstance(hz, (int, float)) and hz > 0:
            values.setdefault(cid, []).append(float(hz))
    return {cid: round(statistics.median(v), 1) for cid, v in values.items() if len(v) >= min_lines}


def clean_items(root: Path, lines: list[dict[str, Any]], limit: int = BASELINE_LINES) -> list[tuple[str, Path]]:
    """(line id, wav) of up to `limit` longest clean originals: the lines of baseline and centroid."""
    items: list[tuple[str, Path]] = []
    for line in sorted(lines, key=lambda x: (-line_duration(root, x), x["id"])):
        wav = clean_original(root, line)
        if wav is not None:
            items.append((line["id"], wav))
            if len(items) >= limit:
                break
    return items


def char_centroids(
    root: Path,
    char_lines: dict[str, list[dict[str, Any]]],
    embedder: Embedder,
    limit: int = BASELINE_LINES,
) -> dict[str, np.ndarray]:
    """{char_id: unit voice centroid} of characters with at least MIN_CENTROID_LINES clean originals."""
    out: dict[str, np.ndarray] = {}
    for cid, lines in char_lines.items():
        if cid == UNKNOWN_ID:  # a mix of voices has no centroid
            continue
        embs = embedder.embed(clean_items(root, lines, limit))
        if len(embs) >= MIN_CENTROID_LINES:
            centroid = centroid_of(list(embs.values()))
            if centroid is not None:
                out[cid] = centroid
    return out


def compute_baselines(
    root: Path,
    char_lines: dict[str, list[dict[str, Any]]],
    embedder: Embedder,
    cached: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """{"chars": {char_id: {"sim_ref", "sim_ref_loo", "n", "source"}}, "default": float, "definition",
    "lines": {line_id: {"orig_sim", "against"}}} (`against`: the voice target's kind, character and embedding
    fingerprint, set by `qa`; missing chars computed; entries written before `sim_ref_loo`
    existed are computed again from the cached embeddings)."""
    data = dict(cached or {})
    chars: dict[str, Any] = dict(data.get("chars") or {}) if not force else {}
    for cid, lines in char_lines.items():
        if cid == UNKNOWN_ID or (cid in chars and "sim_ref_loo" in chars[cid]):
            continue
        embs = list(embedder.embed(clean_items(root, lines)).values())
        value, loo = mean_pairwise_cosine(embs), mean_loo_centroid_cosine(embs)
        chars[cid] = {
            "sim_ref": None if value is None else round(value, 4),
            "sim_ref_loo": None if loo is None else round(loo, 4),
            "n": len(embs),
            "source": "pairwise" if value is not None else "fallback",
        }
    measured = [v["sim_ref"] for v in chars.values() if v.get("sim_ref") is not None]
    default = round(statistics.median(measured), 4) if measured else DEFAULT_SIM_REF
    lines_cache = {} if force else dict(data.get("lines") or {})
    return {"chars": chars, "default": default, "definition": SIM_DEFINITION, "lines": lines_cache,
            "updated": now_iso()}


def sim_ref_for(baseline: dict[str, Any], char_id: str) -> float:
    entry = (baseline.get("chars") or {}).get(char_id) or {}
    value = entry.get("sim_ref")
    return float(value) if value is not None else float(baseline.get("default", DEFAULT_SIM_REF))


def load_baseline(path: Path) -> dict[str, Any]:
    return read_json(path, default=None) or {}


def save_baseline(path: Path, data: dict[str, Any]) -> None:
    write_json(path, data)


MOS_BASELINE = "qa/mos_baseline.json"  # {"model", "chars": {char_id: {median, n}}}
MOS_BASELINE_LINES = 8


class MosBaseline:
    """Median MOS of each character's clean originals, measured once by the session's MOS model and cached
    in qa/mos_baseline.json together with the model's name: a file of another model (or of the format before
    the model was recorded, which was always UTMOS22) is measured again. A failed measurement is not cached."""

    def __init__(self, root: Path, path: Path, session: Any, char_lines: dict[str, list[dict[str, Any]]],
                 rebaseline: bool = False) -> None:
        self.root, self.path, self.session, self.char_lines = root, path, session, char_lines
        data = {} if rebaseline else dict(read_json(path, default={}) or {})
        if data and "chars" not in data:  # v0.3 file: {char_id: {median, n}} measured by UTMOS22
            data = {"model": "utmos22", "chars": data}
        self.model: str | None = data.get("model")
        self.chars: dict[str, Any] = dict(data.get("chars") or {})
        self.tried: dict[str, float | None] = {}

    def median(self, cid: str) -> float | None:
        from babeldub.qa.qc import QcItemError

        used = getattr(self.session, "mos_model", None)
        if used and used != self.model:  # another MOS model: its scale differs, measure again
            self.model, self.chars, self.tried = used, {}, {}
        if (self.chars.get(cid) or {}).get("median") is not None:
            return float(self.chars[cid]["median"])
        if cid not in self.tried:
            values: list[float] = []
            for line_id, wav in clean_items(self.root, self.char_lines.get(cid, []), MOS_BASELINE_LINES):
                try:
                    values.append(self.session.mos(f"orig:{line_id}", wav))
                except QcItemError:
                    continue
            self.tried[cid] = round(statistics.median(values), 3) if values else None
            if values:
                self.chars[cid] = {"median": self.tried[cid], "n": len(values)}
            else:
                self.chars.pop(cid, None)  # an older run may have cached a failed (None) median
            self.model = self.model or getattr(self.session, "mos_model", None)
            write_json(self.path, {"model": self.model, "chars": self.chars})
        return self.tried[cid]
