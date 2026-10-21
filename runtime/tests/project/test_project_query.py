from pathlib import Path

from babeldub.core import layout
from babeldub.core.jsonl import read_jsonl, write_jsonl


def _setup(project_with_lines, line):
    lines = [
        line(1, src="Okay, let's see."),
        line(2, src="Hand me the hammer.", speaker="Maciek"),
        line(3, kind="sfx", src=None, speaker=None),
        line(4, scene="a1_m02", src="Where is the bathroom?", fixed=True, flags=["duplicate"]),
        line(5, scene="a1_m01", src="Another one."),
    ]
    project = project_with_lines(lines)
    ids = [ln["id"] for ln in lines]
    write_jsonl(project.path(layout.CHARACTERS), [
        {"char_id": "c1", "name": "Zbyszek", "selected": True},
        {"char_id": "c2", "name": "Maciek", "selected": True},
    ])
    write_jsonl(project.path(layout.ASSIGN), [
        {"id": ids[0], "char_id": "c1"}, {"id": ids[1], "char_id": "c2"},
        {"id": ids[3], "char_id": "c1"}, {"id": ids[4], "char_id": "c1"},
    ])
    write_jsonl(project.path(layout.TRANSLATIONS), [{"id": ids[0], "dub_text": "Так, посмотрим.", "by": "agent"}])
    write_jsonl(project.path(layout.QA), [{"id": ids[0], "verdict": "fail"}, {"id": ids[1], "verdict": "pass"}])
    write_jsonl(project.path(layout.DECISIONS), [{"id": ids[0], "decision": "regen"}])
    return project, ids


def test_query_filters_and_fields(bd, project_with_lines, line):
    _, ids = _setup(project_with_lines, line)
    code, out = bd("query", "--where", "kind=dialogue,char=Zbyszek")
    assert code == 0 and out["total"] == 3
    assert set(out["rows"][0]) == {"id", "kind", "char", "scene", "text.src", "translation.dub_text"}
    assert out["rows"][0]["translation.dub_text"] == "Так, посмотрим."
    assert bd("query", "--where", "char=c2")[1]["total"] == 1
    assert bd("query", "--where", "has_text=false")[1]["total"] == 1
    assert bd("query", "--where", "verdict=fail", "--where", "decision=regen")[1]["total"] == 1
    assert bd("query", "--where", "text.src~HAMMER")[1]["total"] == 1
    assert bd("query", "--where", "flags=duplicate")[1]["total"] == 1
    assert bd("query", "--where", "scene!=a1_m01")[1]["total"] == 1
    assert bd("query", "--where", "translated=true")[1]["total"] == 1
    code, out = bd("query", "--fields", "id,audio.dur_s,verdict,char_name", "--limit", "2")
    assert out["total"] == 5 and len(out["rows"]) == 2
    assert out["rows"][0] == {"id": ids[0], "audio.dur_s": 2.0, "verdict": "fail", "char_name": "Zbyszek"}
    assert bd("query", "--where", "bad clause")[0] == 2


def test_query_out_file(bd, project_with_lines, line, tmp_path):
    _setup(project_with_lines, line)
    code, out = bd("query", "--where", "kind=dialogue", "--limit", "1", "--out", tmp_path / "q.jsonl")
    assert len(out["rows"]) == 1 and len(read_jsonl(Path(out["file"]))) == 4


def test_sample_scene_char_ids(bd, project_with_lines, line):
    project, ids = _setup(project_with_lines, line)
    code, out = bd("sample", "--scene", "a1_m01")
    assert code == 0 and out["n"] == 4
    rows = read_jsonl(Path(out["file"]))
    assert [r["order"] for r in rows] == [1, 2, 3, 5]
    assert rows[0]["speaker"] == "Zbyszek" and rows[0]["dub_text"] == "Так, посмотрим."
    assert rows[0]["wav"].endswith(".wav") and Path(rows[0]["wav"]).is_absolute()
    assert Path(out["file"]).parent == project.path("logs", "samples")
    code, out = bd("sample", "--char", "zbyszek", "--n", "2")
    assert out["n"] == 2
    code, out = bd("sample", "--ids", f"{ids[1]},{ids[3]}")
    rows = read_jsonl(Path(out["file"]))
    assert [r["id"] for r in rows] == [ids[1], ids[3]] and rows[1]["fixed_length"] is True
    assert bd("sample", "--ids", "nope")[0] == 2
    assert bd("sample")[0] == 2
    assert bd("sample", "--scene", "missing")[0] == 2
