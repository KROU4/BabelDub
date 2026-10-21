import json
from pathlib import Path

from babeldub.commands.translate import term_in_source, translation_present
from babeldub.core import layout
from babeldub.core.jsonl import read_jsonl, write_jsonl


def test_matching_helpers():
    assert term_in_source("Zbyszek", "Where is ZBYSZEK's hammer?")
    assert term_in_source("repair", "Repairs are done")
    assert not term_in_source("Ann", "Annual report")
    assert translation_present("Збышек", "Это молоток Збышека.")
    assert translation_present("ёлка", "Под елкой")
    assert translation_present("Низкобюджетный ремонт", "Добро пожаловать в «Низкобюджетный ремонт»!")
    assert not translation_present("Збышек", "Это молоток Мачека.")


def test_glossary_apply_and_check(bd, project_with_lines, line):
    lines = [
        line(1, src="Zbyszek, bring the hammer!"),
        line(2, src="Maciek is late again."),
        line(3, src="Nothing special here."),
    ]
    project = project_with_lines(lines)
    agent = project.path(layout.GLOSSARY_AGENT)
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(json.dumps({"term": "Zbyszek"}) + "\n", encoding="utf-8")
    code, out = bd("glossary", "apply")
    assert code == 2 and out["applied"] == 0

    agent.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in [
            {"term": "Zbyszek", "translation": "Збышек", "pronounce": "Збы́шек"},
            {"term": "Maciek", "translation": "Мачек", "note": "brother"},
        ]) + "\n",
        encoding="utf-8",
    )
    code, out = bd("glossary", "apply")
    assert code == 0 and out["added"] == 2 and out["total"] == 2
    assert read_jsonl(project.path(layout.GLOSSARY))[0]["pronounce"] == "Збы́шек"
    code, out = bd("glossary", "apply")
    assert out["added"] == 0 and out["updated"] == 0

    write_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": lines[0]["id"], "dub_text": "Збышек, неси молоток!", "by": "agent"},
        {"id": lines[1]["id"], "dub_text": "Мацек опять опаздывает.", "by": "agent"},
        {"id": lines[2]["id"], "dub_text": "Ничего особенного.", "by": "agent"},
    ])
    code, out = bd("glossary", "check")
    assert code == 3 and out["total"] == 1 and out["checked"] == 3
    assert out["violations"] == [f"{lines[1]['id']}: Maciek -> Мачек"]
    rows = read_jsonl(Path(out["file"]))
    assert rows[0]["dub_text"] == "Мацек опять опаздывает."

    write_jsonl(project.path(layout.TRANSLATIONS), [{"id": lines[1]["id"], "dub_text": "Мачека нет.", "by": "agent"}])
    code, out = bd("glossary", "check")
    assert code == 0 and out["total"] == 0


def test_glossary_check_without_glossary(bd, project_with_lines, line):
    project = project_with_lines([line(1)])
    code, out = bd("glossary", "check")
    assert code == 0 and out["terms"] == 0
    project.path(layout.GLOSSARY_AGENT).parent.mkdir(parents=True, exist_ok=True)
    project.path(layout.GLOSSARY_AGENT).write_text('{"term": "a", "translation": "б"}\n', encoding="utf-8")
    code, out = bd("glossary", "check")
    assert code == 3 and out["next"] == "babeldub glossary apply"
    assert bd("glossary", "apply")[0] == 0
