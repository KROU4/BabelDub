"""Delivery tags at the translation stage: batch make hints, batch submit validates and stores `delivery`."""

import json
from pathlib import Path

from babeldub.analysis.prosody_tags import VOCABULARY
from babeldub.core import layout
from babeldub.core.jsonl import latest_by, read_json, read_jsonl, write_jsonl


def _write_out(path, rows):
    Path(path).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _in_rows(path):
    rows = read_jsonl(Path(path))
    return rows[0], rows[1:]


def _analysis(lines, shout_index):
    """Plain prosody for every line of the character, a shout on one of them."""
    rows = []
    for n, ln in enumerate(lines):
        loud = n == shout_index
        rows.append({"id": ln["id"], "uid": ln["uid"], "dur_s": 3.0, "speech_s": 2.5, "class": "speech",
                     "prosody": {"f0_median_hz": 220.0 if loud else 130.0 + (n % 3 - 1), "f0_range_st": 6.0,
                                 "rms_db": -10.0 if loud else -20.0 + 0.1 * (n % 5 - 2), "voiced_ratio": 0.6,
                                 "syl_rate": 4.5}})
    return rows


def test_batch_make_adds_measured_delivery_hints(bd, project_with_lines, line):
    lines = [line(i, src=f"Line number {i}.") for i in range(8)]
    project = project_with_lines(lines)
    write_jsonl(project.path(layout.ANALYSIS), _analysis(lines, 3), schema="babeldub.analysis/1")
    code, out = bd("batch", "make")
    assert code == 0, out
    header, rows = _in_rows(project.path(layout.BATCHES_DIR, "b0001.in.jsonl"))
    assert header["delivery_tags"] == list(VOCABULARY)
    assert "delivery_hint" in header["rules"] and "delivery = []" in header["rules"] and "whispered" in header["rules"]
    hints = {r["id"]: r["delivery_hint"] for r in rows}
    assert hints[lines[3]["id"]] == ["shouting"]
    assert all(hints[ln["id"]] == [] for n, ln in enumerate(lines) if n != 3)


def test_batch_make_without_analysis_gives_empty_hints(bd, project_with_lines, line):
    project = project_with_lines([line(i, src=f"Line {i}.") for i in range(3)])
    bd("batch", "make")
    _, rows = _in_rows(project.path(layout.BATCHES_DIR, "b0001.in.jsonl"))
    assert [r["delivery_hint"] for r in rows] == [[], [], []]


def test_batch_submit_validates_and_stores_delivery(bd, project_with_lines, line):
    lines = [line(i, src=f"Line {i}.") for i in range(4)]
    project = project_with_lines(lines)
    bd("batch", "make")
    out_path = project.path(layout.BATCHES_DIR, "b0001.out.jsonl")
    ids = [ln["id"] for ln in lines]
    _write_out(out_path, [
        {"id": ids[0], "dub_text": "Реплика ноль.", "delivery": ["sarcastic"]},
        {"id": ids[1], "dub_text": "Реплика один.", "delivery": ["calm", "sad", "soft", "slow"]},
        {"id": ids[2], "dub_text": "Реплика два."},
        {"id": ids[3], "dub_text": "Реплика три.", "delivery": "calm"},
    ])
    code, out = bd("batch", "submit", "b0001")
    assert code == 2 and out["errors_total"] == 3, out
    assert "unknown delivery tag 'sarcastic'" in " ".join(out["errors"]) and "at most 3" in " ".join(out["errors"])
    assert all(tag in out["hint"] for tag in VOCABULARY)
    errors = read_json(project.path(layout.BATCHES_DIR, "b0001.errors.json"))["errors"]
    assert {e["field"] for e in errors} == {"delivery"}
    assert not project.path(layout.TRANSLATIONS).exists()  # all or nothing

    _write_out(out_path, [
        {"id": ids[0], "dub_text": "Реплика ноль.", "delivery": ["Urgent", "quick"]},
        {"id": ids[1], "dub_text": "Реплика один.", "delivery": []},
        {"id": ids[2], "dub_text": "Реплика два."},
        {"id": ids[3], "dub_text": "Реплика три.", "delivery": ["whispered"]},
    ])
    code, out = bd("batch", "submit", "b0001")
    assert code == 0 and out["merged"] == 4, out
    merged = latest_by(read_jsonl(project.path(layout.TRANSLATIONS)))
    assert merged[ids[0]]["delivery"] == ["urgent", "quick"]
    assert merged[ids[1]]["delivery"] == []  # explicitly neutral
    assert "delivery" not in merged[ids[2]]  # not given: synth takes the measured tags
    assert merged[ids[3]]["delivery"] == ["whispered"]


def test_rework_keeps_the_agent_delivery_and_null_is_not_given(bd, project_with_lines, line):
    lines = [line(i, src=f"Line {i}.") for i in range(4)]
    project = project_with_lines(lines)
    ids = [ln["id"] for ln in lines]
    bd("batch", "make")
    _write_out(project.path(layout.BATCHES_DIR, "b0001.out.jsonl"), [
        {"id": ids[0], "dub_text": "Реплика ноль.", "delivery": ["whispered"]},
        {"id": ids[1], "dub_text": "Реплика один.", "delivery": None},  # null = not given, not an error
        {"id": ids[2], "dub_text": "Реплика два.", "delivery": []},
        {"id": ids[3], "dub_text": "Реплика три.", "delivery": ["sad"]},
    ])
    code, out = bd("batch", "submit", "b0001")
    assert code == 0 and out["merged"] == 4, out
    assert "delivery" not in latest_by(read_jsonl(project.path(layout.TRANSLATIONS)))[ids[1]]

    # rework: retranslate (--ids) and shorten (a QA decision) rows show the current delivery
    write_jsonl(project.path(layout.DECISIONS), [{"id": ids[2], "decision": "shorten", "attempt": 1, "by": "auto"}])
    code, out = bd("batch", "make", "--ids", f"{ids[0]},{ids[1]},{ids[3]}", "--from-decisions")
    assert code == 0 and out["lines"] == 4, out
    header, rows = _in_rows(project.path(layout.BATCHES_DIR, "b0002.in.jsonl"))
    assert "current_delivery" in header["rules"]
    by_id = {r["id"]: r for r in rows}
    assert by_id[ids[0]]["task"] == "retranslate" and by_id[ids[0]]["current_delivery"] == ["whispered"]
    assert "current_delivery" not in by_id[ids[1]]
    assert by_id[ids[2]]["task"] == "shorten" and by_id[ids[2]]["current_delivery"] == []
    _write_out(project.path(layout.BATCHES_DIR, "b0002.out.jsonl"), [
        {"id": ids[0], "dub_text": "Ноль."},  # left out: the whisper stays
        {"id": ids[1], "dub_text": "Один.", "delivery": None},  # nothing to keep: the hint applies
        {"id": ids[2], "dub_text": "Два."},  # explicitly neutral stays neutral
        {"id": ids[3], "dub_text": "Три.", "delivery": ["calm"]},  # changed
    ])
    code, out = bd("batch", "submit", "b0002")
    assert code == 0 and out["merged"] == 4, out
    merged = latest_by(read_jsonl(project.path(layout.TRANSLATIONS)))
    assert merged[ids[0]]["rev"] == 2 and merged[ids[0]]["dub_text"] == "Ноль."
    assert merged[ids[0]]["delivery"] == ["whispered"]
    assert "delivery" not in merged[ids[1]]
    assert merged[ids[2]]["delivery"] == []
    assert merged[ids[3]]["delivery"] == ["calm"]

    # a fresh translate row never inherits: no delivery accepts the hint
    _write_out(project.path(layout.BATCHES_DIR, "b0001.out.jsonl"), [
        {"id": ids[0], "dub_text": "Реплика ноль."}, {"id": ids[1], "dub_text": "Реплика один."},
        {"id": ids[2], "dub_text": "Реплика два."}, {"id": ids[3], "dub_text": "Реплика три."},
    ])
    code, out = bd("batch", "submit", "b0001", "--force")
    assert code == 0, out
    assert "delivery" not in latest_by(read_jsonl(project.path(layout.TRANSLATIONS)))[ids[0]]
