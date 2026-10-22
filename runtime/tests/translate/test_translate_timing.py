"""Syllable budget = original speech span x the character's measured rate; original pauses as ' / '."""

from pathlib import Path

import numpy as np
import pytest

from babeldub.audio.io import write_wav
from babeldub.core import layout
from babeldub.core.jsonl import read_jsonl, write_jsonl


def _tone(seconds: float, sr: int) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.3 * np.sin(2 * np.pi * 150 * t)).astype(np.float32)


def _orig(project, line, spec):
    """Write the line's original: ("w", s) = speech, ("p", s) = silence."""
    sr = 16000
    data = np.concatenate([_tone(s, sr) if k == "w" else np.zeros(int(s * sr), np.float32) for k, s in spec])
    write_wav(project.path(line["audio"]["wav"]), data, sr)


def _rows(project, bid="b0001"):
    rows = read_jsonl(Path(project.path(layout.BATCHES_DIR, f"{bid}.in.jsonl")))
    return rows[0], {r["id"]: r for r in rows[1:]}


def test_budget_uses_speech_span_and_marks_pauses(bd, project_with_lines, line):
    paused = line(0, src="We fix the pump today, then we go home now.", dur=3.0)
    plain = line(1, src="Okay, let's see.", dur=2.0)
    analysed = line(2, src="Fine.", dur=2.0)
    project = project_with_lines([paused, plain, analysed])
    _orig(project, paused, [("p", 0.3), ("w", 0.8), ("p", 0.4), ("w", 0.8), ("p", 0.7)])
    write_jsonl(project.path("inventory/analysis.jsonl"),
                [{"id": analysed["id"], "speech_s": 1.2, "vad_segments": [[0.1, 0.5], [0.6, 1.3]]}])
    code, out = bd("batch", "make")
    assert code == 0, out
    header, rows = _rows(project)
    assert "src_segmented" in header["rules"] and "stress homographs" in header["rules"]
    row = rows[paused["id"]]
    assert row["dur_s"] == 3.0 and row["speech_s"] == pytest.approx(2.0, abs=0.05)
    assert row["rate_syl_s"] == 5.5 and row["budget_syll"] == 11  # 2.0 s of speech, not the 3.0 s file
    assert row["src_segmented"] == "We fix the pump today, / then we go home now."
    # no original audio: the file duration; no pause to show
    assert rows[plain["id"]]["speech_s"] == 2.0 and rows[plain["id"]]["budget_syll"] == 11
    assert "src_segmented" not in rows[plain["id"]]
    # analysis.jsonl wins; its 100 ms gap is not a pause worth marking
    assert rows[analysed["id"]]["speech_s"] == 1.2 and rows[analysed["id"]]["budget_syll"] == 7
    assert "src_segmented" not in rows[analysed["id"]]


def _accepted(project, lines, *, stale=()):
    write_jsonl(project.path(layout.ASSIGN), [{"id": ln["id"], "char_id": "zbyszek"} for ln in lines])
    write_jsonl(project.path(layout.POST), [{"id": ln["id"], "attempt": 1, "ratio_raw": 1.0, "target_s": 2.0}
                                            for ln in lines])
    write_jsonl(project.path(layout.DECISIONS),
                [{"id": ln["id"], "decision": "accept", "attempt": 2 if ln["id"] in stale else 1} for ln in lines])
    # 8 syllables in 2.0 s of raw dub speech: 4.0 syllables per second
    write_jsonl(project.path(layout.TRANSLATIONS), [{"id": ln["id"], "dub_text": "ма " * 8, "rev": 1}
                                                    for ln in lines])


@pytest.mark.parametrize(("n_accepted", "stale", "rate", "budget"), [(3, False, 4.0, 8), (2, False, 5.5, 11),
                                                                     (3, True, 5.5, 11)])
def test_budget_uses_the_characters_measured_rate(bd, project_with_lines, line, n_accepted, stale, rate, budget):
    done = [line(i) for i in range(n_accepted)]
    new = line(10, dur=2.0)
    project = project_with_lines([*done, new])
    _accepted(project, done, stale={done[0]["id"]} if stale else set())
    write_jsonl(project.path(layout.ASSIGN), [{"id": ln["id"], "char_id": "zbyszek"} for ln in [*done, new]])
    code, out = bd("batch", "make")
    assert code == 0, out
    _, rows = _rows(project)
    assert list(rows) == [new["id"]]
    assert rows[new["id"]]["rate_syl_s"] == rate and rows[new["id"]]["budget_syll"] == budget
