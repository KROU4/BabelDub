"""inventory/analysis.jsonl (one row per analyzed line) and inventory/analysis.npz (vectors by line uid)."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from babeldub.analysis.classes import CLASSES, KEEP_ORIGINAL_CLASSES
from babeldub.analysis.prosody_tags import Measured, measure
from babeldub.core import layout
from babeldub.core.jsonl import latest_by, read_jsonl, write_jsonl
from babeldub.core.project import Project

ANALYSIS_SCHEMA = "babeldub.analysis/1"


def load_analysis(project: Project) -> dict[str, dict[str, Any]]:
    """Latest analysis row per line id (empty before `babeldub analyze`)."""
    return latest_by(read_jsonl(project.path(layout.ANALYSIS)))


def write_analysis(project: Project, rows: dict[str, dict[str, Any]]) -> None:
    """Rewrite analysis.jsonl and drop `emotion_<uid>` vectors no current row measured (a line re-measured
    without the emotion model, or gone from the project), so a stale embedding is never read back."""
    write_jsonl(project.path(layout.ANALYSIS), list(rows.values()), schema=ANALYSIS_SCHEMA)
    npz = project.path(layout.ANALYSIS_NPZ)
    vectors = load_vectors(npz)
    keep = {vector_key("emotion", str(r["uid"])) for r in rows.values() if r.get("uid") and r.get("emotion")}
    pruned = {k: v for k, v in vectors.items() if not k.startswith("emotion_") or k in keep}
    if len(pruned) != len(vectors):
        save_vectors(npz, pruned)


def load_vectors(path: Path) -> dict[str, np.ndarray]:
    """{name: float32 vector}; names are `<kind>_<uid>` (e.g. `emotion_1f0c3a9e7b21`)."""
    if not path.exists():
        return {}
    with np.load(path, allow_pickle=False) as data:
        return {k: np.asarray(data[k], dtype=np.float32) for k in data.files}


def save_vectors(path: Path, vectors: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.npz")
    np.savez_compressed(tmp, **{k: np.asarray(v, dtype=np.float32) for k, v in vectors.items()})
    tmp.replace(path)


def vector_key(kind: str, uid: str) -> str:
    return f"{kind}_{uid}"


def keep_original_classes(project: Project) -> frozenset[str]:
    """Line classes whose original audio is kept instead of a dub (`tts.keep_original_classes`)."""
    value = project.setting("tts", "keep_original_classes", None)
    if value is None:
        return KEEP_ORIGINAL_CLASSES
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",")]
    return frozenset(str(v) for v in value if str(v) in CLASSES)


def line_classes(analysis: dict[str, dict[str, Any]]) -> dict[str, str]:
    """{line id: class} of analyzed lines."""
    return {lid: str(row["class"]) for lid, row in analysis.items() if row.get("class") in CLASSES}


def keep_original_ids(project: Project, analysis: dict[str, dict[str, Any]] | None = None) -> dict[str, str]:
    """{line id: class} of lines that keep the original audio (interjection, nonverbal by default)."""
    keep = keep_original_classes(project)
    classes = line_classes(load_analysis(project) if analysis is None else analysis)
    return {lid: cls for lid, cls in classes.items() if cls in keep}


def line_characters(project: Project, lines: Iterable[dict[str, Any]] | None = None) -> dict[str, str]:
    """{line id: char_id}: the cast assignment once `cast` ran (characters.jsonl and assign.jsonl exist),
    else the line's speaker_hint (the way `synth` groups lines)."""
    if lines is None:
        lines = latest_by(read_jsonl(project.path(layout.LINES))).values()
    assign = latest_by(read_jsonl(project.path(layout.ASSIGN)))
    cast_done = bool(assign) and bool(read_jsonl(project.path(layout.CHARACTERS)))
    out: dict[str, str] = {}
    for line in lines:
        char_id = (assign.get(line["id"]) or {}).get("char_id") if cast_done else line.get("speaker_hint")
        if char_id:
            out[str(line["id"])] = str(char_id)
    return out


def measured_tags(project: Project, analysis: dict[str, dict[str, Any]] | None = None,
                  lines: Iterable[dict[str, Any]] | None = None) -> dict[str, Measured]:
    """{line id: Measured} delivery tags of the originals (analysis.prosody_tags), computed on demand from
    analysis.jsonl and the cast; a line without analysis or with too few comparable lines has no tags."""
    analysis = load_analysis(project) if analysis is None else analysis
    if not analysis:
        return {}
    return measure(analysis, line_characters(project, lines))


def emotion_vectors(project: Project) -> dict[str, np.ndarray]:
    """{line uid: emotion embedding} of the originals (only with the opt-in emotion model)."""
    prefix = "emotion_"
    vectors = load_vectors(project.path(layout.ANALYSIS_NPZ))
    return {k[len(prefix):]: v for k, v in vectors.items() if k.startswith(prefix)}
