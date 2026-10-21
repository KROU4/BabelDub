"""Voice baseline and voice centroid of each character, from his own clean original lines.

Similarity of a dub to the original is judged relative to the baseline (sim >= ratio *
sim_ref), because absolute cosine values differ between voices, and cross-lingual similarity
is lower than monolingual. Cached in qa/baseline.json. The centroids (mean embedding of the
same lines) tell whose voice a dub is closest to (`cast` merges, the QA identity margin).
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

import numpy as np

from babeldub.cast.characters import UNKNOWN_ID, line_duration, wav_path
from babeldub.cast.cluster import Embedder, centroid_of
from babeldub.core.jsonl import now_iso, read_json, write_json
from babeldub.qa.metrics import mean_pairwise_cosine

BASELINE = "qa/baseline.json"
# used when no character has two clean original lines; Low-Budget Repairs measured 0.44-0.65
# between lines of one actor, and 0.39-0.47 between a Qwen3-TTS Russian dub and its English original
DEFAULT_SIM_REF = 0.5
BASELINE_LINES = 12
MIN_CENTROID_LINES = 2  # a voice centroid from a single line is too noisy to compare voices with
SIM_BAD_FLAGS = frozenset({"has_bed", "nonverbal", "alt_take", "no_audio"})
MIN_SIM_S = 1.0


def clean_original(root: Path, line: dict[str, Any]) -> Path | None:
    """Original wav usable for voice comparison: no music bed / non-verbal flags, >= 1 s."""
    if SIM_BAD_FLAGS.intersection(line.get("flags") or []):
        return None
    wav = wav_path(root, line)
    if wav is None or not wav.exists():
        return None
    if line_duration(root, line) < MIN_SIM_S:
        return None
    return wav


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
    """{"chars": {char_id: {"sim_ref", "n", "source"}}, "default": float} (missing chars computed)."""
    data = dict(cached or {})
    chars: dict[str, Any] = dict(data.get("chars") or {}) if not force else {}
    for cid, lines in char_lines.items():
        if cid in chars or cid == UNKNOWN_ID:
            continue
        embs = embedder.embed(clean_items(root, lines))
        value = mean_pairwise_cosine(list(embs.values()))
        chars[cid] = {
            "sim_ref": None if value is None else round(value, 4),
            "n": len(embs),
            "source": "pairwise" if value is not None else "fallback",
        }
    measured = [v["sim_ref"] for v in chars.values() if v.get("sim_ref") is not None]
    default = round(statistics.median(measured), 4) if measured else DEFAULT_SIM_REF
    return {"chars": chars, "default": default, "updated": now_iso()}


def sim_ref_for(baseline: dict[str, Any], char_id: str) -> float:
    entry = (baseline.get("chars") or {}).get(char_id) or {}
    value = entry.get("sim_ref")
    return float(value) if value is not None else float(baseline.get("default", DEFAULT_SIM_REF))


def load_baseline(path: Path) -> dict[str, Any]:
    return read_json(path, default=None) or {}


def save_baseline(path: Path, data: dict[str, Any]) -> None:
    write_json(path, data)
