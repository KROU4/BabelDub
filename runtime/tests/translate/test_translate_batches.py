import json
from pathlib import Path

from babeldub.core import layout
from babeldub.core.jsonl import read_json, read_jsonl, write_jsonl


def _write_out(path, rows):
    Path(path).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _scene_lines(line):
    lines = [line(i, scene="a1_m01", src=f"Line {i} of the long scene.") for i in range(70)]
    lines += [line(100 + i, scene="a1_m02", src=f"Short {i}.") for i in range(10)]
    lines += [line(200 + i, scene="a1_m03", src=f"Other {i}.") for i in range(10)]
    lines.append(line(300, scene="a1_m03", kind="sfx", src=None))
    return lines


def _in_rows(path):
    rows = read_jsonl(Path(path))
    return rows[0], rows[1:]


def test_batch_make_cuts_by_scene_and_size(bd, project_with_lines, line):
    project = project_with_lines(_scene_lines(line))
    code, out = bd("batch", "make", "--size", "60")
    assert code == 0, out
    assert out["batches"] == ["b0001", "b0002"] and out["lines"] == 90 and out["mode"] == "translate"
    header, rows = _in_rows(project.path(layout.BATCHES_DIR, "b0001.in.jsonl"))
    assert header["_batch"] == "b0001" and header["scene"] == "a1_m01" and header["target_lang"] == "ru"
    assert header["n"] == 60 and header["out"] == "b0001.out.jsonl" and "dub_text" in header["rules"]
    first = rows[0]
    assert set(first) >= {"id", "key", "speaker", "src", "tgt_official", "dur_s", "budget_syll", "fixed_length",
                          "placeholders", "context_prev", "context_next"}
    assert first["budget_syll"] == 11 and first["speaker"] == "Zbyszek"
    assert first["context_prev"] is None and first["context_next"] == "Zbyszek: Line 1 of the long scene."
    header2, rows2 = _in_rows(project.path(layout.BATCHES_DIR, "b0002.in.jsonl"))
    assert header2["scenes"] == ["a1_m01", "a1_m02", "a1_m03"] and header2["scene"] is None and len(rows2) == 30
    boundary = next(r for r in rows2 if r["src"] == "Short 0.")
    assert boundary["context_prev"] is None
    index = read_json(project.path(layout.BATCHES_DIR, "index.json"))
    assert index["batches"]["b0001"]["status"] == "open"

    code, again = bd("batch", "make")
    assert again["batches"] == [] and again["open"] == 2
    code, forced = bd("batch", "make", "--force", "--size", "100")
    assert forced["batches"] == ["b0001"] and forced["removed"] == ["b0001", "b0002"]
    assert not project.path(layout.BATCHES_DIR, "b0002.in.jsonl").exists()


def test_batch_header_has_glossary_and_style(bd, project_with_lines, line):
    lines = [line(1, src="{ColorStart}{CharacterName}:{ColorEnd} Zbyszek fixes the <b>sink</b>."), line(2, src="Hi.")]
    project = project_with_lines(lines)
    write_jsonl(project.path(layout.GLOSSARY), [
        {"term": "Zbyszek", "translation": "Збышек"},
        {"term": "Maciek", "translation": "Мачек"},
    ])
    project.path(layout.STYLE_BIBLE).write_text("Разговорный стиль, на «ты».", encoding="utf-8")
    bd("batch", "make")
    header, rows = _in_rows(project.path(layout.BATCHES_DIR, "b0001.in.jsonl"))
    assert header["glossary"] == [{"term": "Zbyszek", "translation": "Збышек"}]
    assert header["style"] == "Разговорный стиль, на «ты»."
    assert rows[0]["placeholders"] == ["{ColorStart}", "{CharacterName}", "{ColorEnd}", "<b>", "</b>"]


def test_batch_cycle_errors_then_merge(bd, project_with_lines, line):
    lines = [line(i, src=f"Hello {{name}}, number {i}.", dur=2.0) for i in range(3)]
    project = project_with_lines(lines)
    bd("batch", "make")
    code, nxt = bd("batch", "next")
    assert nxt["batch"] == "b0001" and nxt["state"] == "new" and nxt["remaining"] == 1
    out_path = Path(nxt["out"])
    ids = [ln["id"] for ln in lines]
    _write_out(out_path, [
        {"id": ids[0], "dub_text": "Привет, {name}, номер ноль."},
        {"id": ids[1], "dub_text": "Привет, номер один."},  # lost {name}
        {"id": "fmod:bogus#x:0001", "dub_text": "Лишнее"},
        {"id": ids[2], "dub_text": "   "},
    ])
    with out_path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert bd("batch", "next")[1]["state"] == "ready"
    code, res = bd("batch", "submit", "b0001")
    assert code == 2 and not res["ok"] and res["merged"] == 0
    assert res["errors_total"] == 4
    text = " ".join(res["errors"])
    assert "invalid JSON" in text and "placeholders" in text and "not in this batch" in text and "empty" in text
    assert not project.path(layout.TRANSLATIONS).exists()
    assert Path(res["errors_file"]).is_file()
    assert bd("batch", "next")[1]["state"] == "error"

    _write_out(out_path, [
        {"id": ids[0], "dub_text": "Привет, {name}, номер ноль.", "notes": "ok"},
        {"id": ids[1], "dub_text": "Привет, {name}, номер один.", "sub_text": "Привет, {name}! №1"},
        {"id": ids[2], "dub_text": "Привет, {name}, номер два."},
    ])
    code, res = bd("batch", "submit", "b0001")
    assert code == 0, res
    assert res["merged"] == 3 and res["over_budget"] == [] and res["remaining"] == 0
    rows = {r["id"]: r for r in read_jsonl(project.path(layout.TRANSLATIONS))}
    assert rows[ids[1]]["sub_text"] == "Привет, {name}! №1" and rows[ids[1]]["by"] == "agent"
    assert rows[ids[0]]["notes"] == "ok" and rows[ids[0]]["batch"] == "b0001" and rows[ids[0]]["rev"] == 1
    assert not Path(res.get("errors_file", project.path(layout.BATCHES_DIR, "b0001.errors.json"))).exists()
    assert read_json(project.path(layout.PROGRESS))["translate"]["status"] == "done"
    code, again = bd("batch", "submit", "b0001")
    assert again["already_merged"] and code == 0
    code, done = bd("batch", "next")
    assert done["batch"] is None and done["done"]


def test_missing_ids_are_reported(bd, project_with_lines, line):
    lines = [line(i, src=f"Line {i}.") for i in range(2)]
    project = project_with_lines(lines)
    bd("batch", "make")
    out = project.path(layout.BATCHES_DIR, "b0001.out.jsonl")
    _write_out(out, [{"id": lines[0]["id"], "dub_text": "Реплика.", "variants": "short"}])
    code, res = bd("batch", "submit", "b0001")
    assert code == 2 and res["errors_total"] == 2
    assert any("missing in .out" in e for e in res["errors"]) and any("variants" in e for e in res["errors"])
    assert bd("batch", "submit", "b0099")[0] == 2


def test_over_budget_picks_fitting_variant(bd, project_with_lines, line):
    lines = [line(0, src="Yes.", dur=0.6, fixed=True), line(1, src="No.", dur=0.6)]
    project = project_with_lines(lines)
    bd("batch", "make")
    out = project.path(layout.BATCHES_DIR, "b0001.out.jsonl")
    long_text = "Да, конечно, я абсолютно согласен с этим предложением."
    _write_out(out, [
        {"id": lines[0]["id"], "dub_text": long_text, "variants": ["Да, конечно.", "Да."]},
        {"id": lines[1]["id"], "dub_text": long_text},
    ])
    code, res = bd("batch", "submit", "b0001")
    assert code == 3 and res["ok"]
    assert res["over_budget"] == [lines[1]["id"]] and res["over_fixed"] == [] and res["picked_variants"] == 1
    rows = {r["id"]: r for r in read_jsonl(project.path(layout.TRANSLATIONS))}
    picked = rows[lines[0]["id"]]
    assert picked["dub_text"] == "Да." and picked["picked_variant"] == 1 and picked["agent_text"] == long_text
    assert picked["fit"] != "over" and rows[lines[1]["id"]]["fit"] == "over"


def test_retranslate_ids_and_decisions(bd, project_with_lines, line):
    lines = [line(i, src=f"Line {i}.") for i in range(3)]
    project = project_with_lines(lines)
    write_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": ln["id"], "dub_text": f"Реплика {i}.", "by": "agent", "rev": 1} for i, ln in enumerate(lines)
    ])
    write_jsonl(project.path(layout.DECISIONS), [{"id": lines[2]["id"], "decision": "shorten"}])
    code, out = bd("batch", "make")
    assert out["batches"] == []
    code, out = bd("batch", "make", "--ids", lines[0]["id"], "--from-decisions")
    assert out["batches"] == ["b0001"] and out["lines"] == 2
    _, rows = _in_rows(project.path(layout.BATCHES_DIR, "b0001.in.jsonl"))
    tasks = {r["id"]: (r["task"], r["current"]) for r in rows}
    assert tasks == {lines[0]["id"]: ("retranslate", "Реплика 0."), lines[2]["id"]: ("shorten", "Реплика 2.")}
    assert bd("batch", "make", "--ids", "nope")[0] == 2
    _write_out(project.path(layout.BATCHES_DIR, "b0001.out.jsonl"), [
        {"id": lines[0]["id"], "dub_text": "Новая реплика."}, {"id": lines[2]["id"], "dub_text": "Коротко."},
    ])
    code, res = bd("batch", "submit", "b0001")
    assert code == 0
    latest = {r["id"]: r for r in read_jsonl(project.path(layout.TRANSLATIONS))}
    assert latest[lines[0]["id"]]["rev"] == 2 and latest[lines[0]["id"]]["dub_text"] == "Новая реплика."


def test_adapt_mode_batches_official_lines(bd, project_with_lines, line):
    lines = [line(i, src=f"Line {i}.", official=f"Офиц {i}.") for i in range(2)]
    project = project_with_lines(lines, mode="adapt")
    bd("translate", "use-official")
    code, out = bd("batch", "make")
    assert out["mode"] == "adapt" and out["lines"] == 2
    _, rows = _in_rows(project.path(layout.BATCHES_DIR, "b0001.in.jsonl"))
    assert rows[0]["task"] == "adapt" and rows[0]["tgt_official"] == "Офиц 0." and rows[0]["current"] == "Офиц 0."


def test_scenes_keep_game_order_not_alphabetical(bd, project_with_lines, line):
    lines = [line(1, scene="zeta_intro", src="First."), line(2, scene="alpha_outro", src="Last.")]
    project = project_with_lines(lines)
    bd("batch", "make", "--size", "1")
    _, rows = _in_rows(project.path(layout.BATCHES_DIR, "b0001.in.jsonl"))
    assert rows[0]["src"] == "First."


def test_keep_original_classes_are_not_translated(bd, project_with_lines, line):
    lines = [line(1, src="Okay, let's go."), line(2, src="Hehe..."), line(3, src="Ugh!")]
    project = project_with_lines(lines)
    write_jsonl(project.path(layout.ANALYSIS), [
        {"id": lines[0]["id"], "class": "speech"}, {"id": lines[1]["id"], "class": "interjection"},
        {"id": lines[2]["id"], "class": "nonverbal"}], schema="babeldub.analysis/1")
    code, out = bd("translate", "plan")
    assert out["need_agent"] == 1 and out["keep_original"] == 2, out
    code, out = bd("batch", "make")
    assert code == 0 and out["lines"] == 1
    _, rows = _in_rows(project.path(layout.BATCHES_DIR, "b0001.in.jsonl"))
    assert [r["id"] for r in rows] == [lines[0]["id"]]
