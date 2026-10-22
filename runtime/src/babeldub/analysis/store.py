"""inventory/analysis.jsonl (one row per analyzed line) and inventory/analysis.npz (vectors by line uid)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from babeldub.analysis.classes import CLASSES, KEEP_ORIGINAL_CLASSES
from babeldub.core import layout
from babeldub.core.jsonl import latest_by, read_jsonl, write_jsonl
from babeldub.core.project import Project

ANALYSIS_SCHEMA = "babeldub.analysis/1"


def load_analysis(project: Project) -> dict[str, dict[str, Any]]:
    """Latest analysis row per line id (empty before `babeldub analyze`)."""
    return latest_by(read_jsonl(project.path(layout.ANALYSIS)))


def write_analysis(project: Project, rows: dict[str, dict[str, Any]]) -> None:
    write_jsonl(project.path(layout.ANALYSIS), list(rows.values()), schema=ANALYSIS_SCHEMA)


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


def emotion_vectors(project: Project) -> dict[str, np.ndarray]:
    """{line uid: emotion embedding} of the originals (only with the opt-in emotion model)."""
    prefix = "emotion_"
    vectors = load_vectors(project.path(layout.ANALYSIS_NPZ))
    return {k[len(prefix):]: v for k, v in vectors.items() if k.startswith(prefix)}
