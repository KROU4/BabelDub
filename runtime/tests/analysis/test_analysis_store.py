"""analysis.npz keeps only the emotion vectors of lines whose current analysis row measured emotion."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from babeldub.analysis.store import emotion_vectors, load_vectors, save_vectors, write_analysis
from babeldub.core import layout
from babeldub.core.project import Project


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setenv("BABELDUB_HOME", str(tmp_path / "home"))
    return Project.create(tmp_path / "home" / "projects" / "st",
                          {"slug": "st", "game_name": "St", "game_dir": str(tmp_path / "game"),
                           "target_lang": "ru", "source_lang": "en"})


def test_remeasure_without_emotion_drops_the_stale_vector(project: Project) -> None:
    npz = project.path(layout.ANALYSIS_NPZ)
    vec = np.ones(4, dtype=np.float32)
    save_vectors(npz, {"emotion_u1": vec, "emotion_u2": vec, "emotion_gone": vec, "other_u2": vec})
    rows = {
        "a": {"id": "a", "uid": "u1", "emotion": {"label": "happy", "score": 0.9}},
        "b": {"id": "b", "uid": "u2", "emotion": None},  # re-measured without the emotion model
    }
    write_analysis(project, rows)
    assert sorted(load_vectors(npz)) == ["emotion_u1", "other_u2"]
    assert sorted(emotion_vectors(project)) == ["u1"]
    # nothing stale: the file is left as it is
    before = npz.stat().st_mtime_ns
    write_analysis(project, rows)
    assert npz.stat().st_mtime_ns == before
