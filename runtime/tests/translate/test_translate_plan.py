from babeldub.core import layout
from babeldub.core.jsonl import read_json, write_jsonl
from babeldub.core.project import Project


def _lines(line, n, official_every=1):
    return [
        line(i, src=f"Line number {i}.", official=(f"Реплика {i}." if i % official_every == 0 else None))
        for i in range(1, n + 1)
    ]


def test_plan_auto_official(bd, project_with_lines, line):
    project = project_with_lines(_lines(line, 20))
    code, out = bd("translate", "plan")
    assert code == 0, out
    assert out["mode"] == "official" and out["configured"] == "auto"
    assert out["official_share"] == 1.0 and out["need_agent"] == 0
    assert out["next"] == "babeldub translate use-official"
    assert Project.load(str(project.root)).data["translation"]["resolved_mode"] == "official"
    assert read_json(project.path(layout.PROGRESS))["translate"]["status"] == "running"


def test_plan_auto_falls_back_to_translate(bd, project_with_lines, line):
    project_with_lines(_lines(line, 20, official_every=2))
    code, out = bd("translate", "plan")
    assert out["mode"] == "translate" and out["with_official"] == 10 and out["need_agent"] == 20
    assert out["next"] == "babeldub batch make"


def test_plan_threshold_is_95_percent(bd, project_with_lines, line):
    lines = _lines(line, 20)
    lines[0]["text"]["tgt_official"] = None  # 19/20 = 95%
    project_with_lines(lines)
    assert bd("translate", "plan")[1]["mode"] == "official"


def test_plan_adapt_and_no_text(bd, project_with_lines, line):
    lines = _lines(line, 4) + [line(9, src=None), line(10, kind="sfx", src=None)]
    project_with_lines(lines, mode="adapt")
    code, out = bd("translate", "plan")
    assert out["mode"] == "adapt" and out["need_agent"] == 4
    assert out["lines"] == 5 and out["no_text"] == 1 and "no text" in out["warning"]


def test_plan_requires_lines(bd, game):
    bd("init", game, "--lang", "ru")
    code, out = bd("translate", "plan")
    assert code == 2 and "extract" in out["hint"]


def test_use_official_copies_and_is_idempotent(bd, project_with_lines, line):
    lines = _lines(line, 6, official_every=2)
    project = project_with_lines(lines)
    code, out = bd("translate", "use-official")
    assert code == 3 and out["copied"] == 3 and out["missing"] == 3
    assert out["missing_file"].endswith("missing_official.txt")
    raw = project.path(layout.TRANSLATIONS).read_text(encoding="utf-8").splitlines()
    assert '"_schema":"babeldub.translations/1"' in raw[0]
    from babeldub.core.jsonl import read_jsonl

    rows = read_jsonl(project.path(layout.TRANSLATIONS))
    assert rows[0]["by"] == "official" and rows[0]["rev"] == 1 and rows[0]["lang"] == "ru"
    assert rows[0]["dub_text"] == rows[0]["sub_text"] == "Реплика 2."
    assert rows[0]["budget_syll"] == 11 and rows[0]["fit"] in ("ok", "under")
    code, again = bd("translate", "use-official")
    assert again["copied"] == 0 and again["unchanged"] == 3


def test_use_official_keeps_agent_translations(bd, project_with_lines, line):
    lines = _lines(line, 2)
    project = project_with_lines(lines)
    write_jsonl(project.path(layout.TRANSLATIONS), [{"id": lines[0]["id"], "dub_text": "Своё", "by": "agent", "rev": 1}])
    code, out = bd("translate", "use-official")
    assert code == 0 and out["copied"] == 1 and out["kept_agent"] == 1
    code, out = bd("translate", "use-official", "--force")
    assert out["copied"] == 1
    assert read_json(project.path(layout.PROGRESS))["translate"]["status"] == "done"
