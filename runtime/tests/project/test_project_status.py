import json
from pathlib import Path

from babeldub.core import layout
from babeldub.core.jsonl import read_json, write_json, write_jsonl


def test_status_fresh_project(bd, game):
    bd("init", game, "--lang", "ru")
    code, out = bd("status")
    assert code == 0
    assert list(out["stages"]) == layout.STAGES
    assert out["stages"]["init"] == "done" and out["stages"]["scan"] == "pending"
    assert out["next_stage"] == "scan" and out["next"] == "babeldub scan"
    assert out["counts"]["lines"] == 0


def test_status_counts(bd, project_with_lines, line):
    lines = [line(1), line(2), line(3, kind="sfx", src=None), line(4, scene="a1_m02", src=None)]
    project = project_with_lines(lines)
    ids = [ln["id"] for ln in lines]
    write_jsonl(project.path(layout.CHARACTERS), [
        {"char_id": "c1", "name": "Zbyszek", "selected": True},
        {"char_id": "c2", "name": "Maciek", "selected": False},
    ])
    write_jsonl(project.path(layout.ASSIGN), [
        {"id": ids[0], "char_id": "c1", "conf": 1.0},
        {"id": ids[1], "char_id": "c1", "conf": 1.0},
        {"id": ids[3], "char_id": "c2", "conf": 0.9},
    ])
    write_jsonl(project.path(layout.TRANSLATIONS), [{"id": ids[0], "dub_text": "Так", "by": "agent", "rev": 1}])
    write_jsonl(project.path(layout.SYNTH_RESULTS), [
        {"id": ids[0], "attempt": 1, "status": "error"},
        {"id": ids[0], "attempt": 2, "status": "ok"},
        {"id": ids[1], "attempt": 1, "status": "error"},
    ])
    write_jsonl(project.path(layout.QA), [{"id": ids[0], "verdict": "pass"}, {"id": ids[1], "verdict": "fail"}])
    write_json(project.path(layout.BUILD_MANIFEST), {"containers": [{"replaced": 2}]})
    write_json(project.path(layout.INSTALL_LOG), {"installed": [{"relpath": "a"}], "state": "installed"})
    project.mark_stage("cast", "attention", uncertain=["c2"])
    code, out = bd("status")
    counts = out["counts"]
    assert counts["lines"] == 4 and counts["voiced"] == 2 and counts["linked"] == 2
    assert counts["characters"] == 2 and counts["selected"] == 1
    assert counts["translated"] == 1 and counts["synth_ok"] == 1
    assert counts["qa_pass"] == 1 and counts["qa_fail"] == 1
    assert counts["built"] == 1 and counts["replaced"] == 2 and counts["installed"] == 1
    # cast is finished with items to decide: reported in `attention`, the pipeline goes on
    assert code == 3 and out["next_stage"] == "translate" and out["attention"] == {"cast": {"uncertain": ["c2"]}}


def test_status_failed_stage_is_next(bd, project_with_lines, line):
    project = project_with_lines([line(1)])
    project.mark_stage("link", "attention", unlinked=3)
    project.mark_stage("cast", "failed", error="boom")
    code, out = bd("status")
    assert code == 3 and out["next_stage"] == "cast" and out["next"] == "babeldub cast"
    assert out["attention"] == {"link": {"unlinked": 3}, "cast": {"error": "boom"}}


def test_status_all_done(bd, game):
    _, init = bd("init", game, "--lang", "ru")
    progress_path = Path(init["project"]) / layout.PROGRESS
    write_json(progress_path, {s: {"status": "done"} for s in layout.STAGES})
    code, out = bd("status")
    assert code == 0 and out["done"] and out["next"] is None


def test_set_typed_values(bd, game):
    _, init = bd("init", game, "--lang", "ru")
    code, out = bd("set", "tts.best_of=2", "qa.max_cer=0.2", "cast.list=Anna, Bob", "install.backup=false",
                   "tts.model=Qwen3-TTS-12Hz-1.7B-Base", "post.tempo_max=1", "game_name=LBR")
    assert code == 0, out
    data = read_json(Path(init["project"]) / layout.PROJECT_JSON)
    assert data["tts"]["best_of"] == 2 and data["qa"]["max_cer"] == 0.2
    assert data["cast"]["list"] == ["Anna", "Bob"] and data["install"]["backup"] is False
    assert data["tts"]["model"] == "Qwen3-TTS-12Hz-1.7B-Base" and data["post"]["tempo_max"] == 1.0
    assert isinstance(data["post"]["tempo_max"], float) and data["game_name"] == "LBR"
    assert out["changed"]["tts.best_of"] == {"old": 3, "new": 2}
    events = (Path(init["project"]) / layout.EVENTS).read_text(encoding="utf-8").splitlines()
    assert json.loads(events[-1])["stage"] == "set"


def test_set_calibration_settings(bd, game):
    _, init = bd("init", game, "--lang", "ru")
    code, out = bd("set", "cast.merge_threshold=0.8", "qa.min_sim_ratio_xlang=0.5", "qa.min_sim_abs=0.2",
                   "qa.min_identity_margin=-0.1", "qa.min_sim_ratio=0.7")
    assert code == 0 and "warnings" not in out, out
    data = read_json(Path(init["project"]) / layout.PROJECT_JSON)
    assert data["cast"]["merge_threshold"] == 0.8
    assert data["qa"]["min_sim_ratio_xlang"] == 0.5 and data["qa"]["min_sim_abs"] == 0.2
    assert data["qa"]["min_identity_margin"] == -0.1 and data["qa"]["min_sim_ratio"] == 0.7
    assert out["changed"]["qa.min_sim_ratio_xlang"] == {"old": 0.45, "new": 0.5}
    # a project created before these keys existed still gets typed values and no warning
    del data["qa"]["min_sim_abs"]
    write_json(Path(init["project"]) / layout.PROJECT_JSON, data)
    code, out = bd("set", "qa.min_sim_abs=0.3")
    assert code == 0 and "warnings" not in out and out["changed"]["qa.min_sim_abs"] == {"old": None, "new": 0.3}
    assert bd("set", "qa.min_sim_abs=low")[0] == 2


def test_set_validation(bd, game):
    bd("init", game, "--lang", "ru")
    assert bd("set", "cast.policy=everyone")[0] == 2
    assert bd("set", "translation.mode=adapt")[0] == 0
    assert bd("set", "tts.best_of=many")[0] == 2
    assert bd("set", "install.backup=maybe")[0] == 2
    assert bd("set", "slug=x")[0] == 2
    assert bd("set", "target_lang=pl")[0] == 2
    assert bd("set", "no-equals-sign")[0] == 2
    assert bd("set")[0] == 2
    code, out = bd("set", "tts.speed_hack=1")
    assert code == 0 and "not a known setting" in out["warnings"][0]
