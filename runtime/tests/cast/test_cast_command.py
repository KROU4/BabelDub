from __future__ import annotations

import json

from babeldub.core import layout
from babeldub.core.jsonl import read_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_OK, EXIT_USAGE


def _build(project, lines_factory) -> None:
    f = lines_factory
    # metadata characters: Zbyszek dominates, Maciek second, a tiny Fireman part
    for i in range(8):
        f.add("Zbyszek", 6.0 + (i % 3), text=f"Zbyszek line {i}.")
    f.add("Zbyszek", 1.2, text="Hm.")
    f.add("Zbyszek", 7.0, text="With music.", flags=("has_bed",))
    for i in range(4):
        f.add("Maciek", 5.0, text=f"Maciek line {i}.")
    f.add("Fireman", 2.0, text="Fire!")
    # lines without speaker metadata: two different voices
    for i in range(3):
        f.add(None, 4.0, text=f"Voice A {i}.", voice="voiceA")
    for i in range(2):
        f.add(None, 3.5, text=f"Voice B {i}.", voice="voiceB")
    f.add("Zbyszek", 3.0, kind="sfx", text=None)  # not voiced: ignored
    f.save()


def test_cast_with_qc_clusters_tiers_and_refs(project, fake_qc, make_lines, run) -> None:
    f = make_lines(fake_qc)
    _build(project, f)
    code, out = run(["cast", "--project", str(project.root)])
    assert code == EXIT_OK, out
    assert out["ok"] is True
    assert out["clusters"] == 2 and out["uncertain"] == [] and out["no_ref"] == []
    assert out["main"] == ["Zbyszek", "Maciek"]
    chars = {c["char_id"]: c for c in read_jsonl(project.path(layout.CHARACTERS))}
    assert set(chars) == {"zbyszek", "maciek", "fireman", "cluster_01", "cluster_02"}
    assert chars["zbyszek"]["source"] == "metadata" and chars["zbyszek"]["tier"] == "main"
    assert chars["cluster_01"]["n_lines"] == 3 and chars["cluster_01"]["source"] == "cluster"
    assert chars["cluster_02"]["n_lines"] == 2
    assert chars["zbyszek"]["selected"] and chars["maciek"]["selected"] and not chars["fireman"]["selected"]
    assign = {r["id"]: r for r in read_jsonl(project.path(layout.ASSIGN))}
    assert len(assign) == 20  # every voiced line, the sfx line is not cast
    voice_a = [ln["id"] for ln in f.lines if ln["text"] and ln["text"]["src"].startswith("Voice A")]
    assert {assign[i]["char_id"] for i in voice_a} == {"cluster_01"}
    refs = read_jsonl(project.path(layout.REFS))
    by_char: dict[str, list] = {}
    for r in refs:
        by_char.setdefault(r["char_id"], []).append(r)
    assert set(by_char) == {"zbyszek", "maciek"}
    assert len(by_char["zbyszek"]) == 3 and sum(r["chosen"] for r in by_char["zbyszek"]) == 1
    bed_id = next(ln["id"] for ln in f.lines if "has_bed" in ln["flags"])
    for r in refs:
        assert r["line_id"] != bed_id
        assert 3.0 <= r["dur_s"] <= 12.0 and r["text"]
        assert project.path(r["wav"]).exists()
        assert r["centroid_cos"] is not None
    sheet = project.path(layout.CASTING_SHEET).read_text(encoding="utf-8")
    assert "Лист кастинга" in sheet and "cluster_01" in sheet and "Voice A" in sheet
    assert fake_qc.loads and fake_qc.loads[0]["sv_model"].endswith("spkrec-ecapa-voxceleb")
    assert "asr_model" not in fake_qc.loads[0]
    assert fake_qc.closed == 1
    assert project.path("cast/embeddings.npz").exists()
    assert project.progress()["cast"]["status"] == "done"

    # re-run: embeddings come from the cache, the worker is not started again
    fake_qc.loads.clear()
    code, out = run(["cast", "--project", str(project.root)])
    assert code == EXIT_OK and out["clusters"] == 2
    assert fake_qc.loads == []


def test_cast_without_qc_groups_unknown(project, no_qc, make_lines, run) -> None:
    f = make_lines(None)
    _build(project, f)
    code, out = run(["cast", "--project", str(project.root)])
    assert code == EXIT_ATTENTION
    assert out["uncertain"] == ["unknown"]
    assert any("qc worker unavailable" in w for w in out["warnings"])
    chars = {c["char_id"]: c for c in read_jsonl(project.path(layout.CHARACTERS))}
    assert chars["unknown"]["n_lines"] == 5 and chars["unknown"]["tier"] == "minor"
    assert not chars["unknown"]["selected"]
    # without the two voice clusters Zbyszek alone holds 74 % of the ranked speech -> only main
    assert out["main"] == ["Zbyszek"]
    refs = read_jsonl(project.path(layout.REFS))
    assert {r["char_id"] for r in refs} == {"zbyszek"}
    assert all(r["centroid_cos"] is None for r in refs)
    assert project.progress()["cast"]["status"] == "attention"

    # policy all: the mixed `unknown` group is dubbed line by line, it never gets a character reference
    code, out = run(["cast", "select", "--policy", "all", "--project", str(project.root)])
    assert code == EXIT_OK, out
    assert out["count"] == 4 and out["no_ref"] == []
    refs = read_jsonl(project.path(layout.REFS))
    assert {r["char_id"] for r in refs} == {"zbyszek", "maciek", "fireman"}


def test_apply_labels_and_select(project, fake_qc, make_lines, run) -> None:
    f = make_lines(fake_qc)
    _build(project, f)
    assert run(["cast", "--project", str(project.root)])[0] == EXIT_OK
    labels = project.path(layout.CAST_LABELS_AGENT)
    labels.write_text(
        json.dumps({"char_id": "cluster_01", "name": "Tadeusz", "name_tgt": "Тадеуш", "gender": "m", "age": "adult"})
        + "\n"
        + json.dumps({"char_id": "zbyszek", "gender": "m", "age": "old"})
        + "\n",
        encoding="utf-8",
    )
    code, out = run(["cast", "apply-labels", "--project", str(project.root)])
    assert code == EXIT_OK and out["updated"] == 2
    chars = {c["char_id"]: c for c in read_jsonl(project.path(layout.CHARACTERS))}
    assert chars["cluster_01"]["name"] == "Tadeusz" and chars["cluster_01"]["gender"] == "m"
    assert chars["zbyszek"]["age"] == "old"
    assert "Тадеуш" in project.path(layout.CASTING_SHEET).read_text(encoding="utf-8")

    # labels survive a re-run of cast
    assert run(["cast", "--project", str(project.root)])[0] == EXIT_OK
    chars = {c["char_id"]: c for c in read_jsonl(project.path(layout.CHARACTERS))}
    assert chars["cluster_01"]["name"] == "Tadeusz"

    # select by target-language name and id: refs are picked for the newly selected
    code, out = run(["cast", "select", "--chars", "Тадеуш,fireman", "--project", str(project.root)])
    assert code == EXIT_OK, out
    assert out["policy"] == "list" and out["count"] == 2 and out["new_refs"] >= 2
    from babeldub.core.project import Project

    data = Project.load(str(project.root)).data
    assert data["cast"]["policy"] == "list" and data["cast"]["list"] == ["Тадеуш", "fireman"]
    refs = read_jsonl(project.path(layout.REFS))
    assert {"cluster_01", "fireman"} <= {r["char_id"] for r in refs}
    fire = [r for r in refs if r["char_id"] == "fireman"]
    assert fire and fire[0]["dur_s"] == 2.0  # only a 2 s line: duration rule relaxed
    assert any("relaxed" in w for w in out["warnings"])

    code, out = run(["cast", "select", "--chars", "Nobody", "--project", str(project.root)])
    assert code == EXIT_USAGE and out["unknown"] == ["Nobody"]


def test_apply_labels_rejects_invalid_file(project, fake_qc, make_lines, run) -> None:
    f = make_lines(fake_qc)
    _build(project, f)
    run(["cast", "--project", str(project.root)])
    before = project.path(layout.CHARACTERS).read_text(encoding="utf-8")
    project.path(layout.CAST_LABELS_AGENT).write_text('{"char_id": "ghost", "gender": "male"}\n', encoding="utf-8")
    code, out = run(["cast", "apply-labels", "--project", str(project.root)])
    assert code == EXIT_USAGE and out["ok"] is False
    assert any("ghost" in e for e in out["errors"]) and any("gender" in e for e in out["errors"])
    assert project.path(layout.CHARACTERS).read_text(encoding="utf-8") == before


def test_cast_requires_extract(home, run) -> None:
    from babeldub.core.project import Project

    p = Project.create(home / "projects" / "x", {"slug": "x", "game_name": "X", "game_dir": str(home), "target_lang": "ru"})
    code, out = run(["cast", "--project", str(p.root)])
    assert code == EXIT_USAGE and "extract" in out["error"]
