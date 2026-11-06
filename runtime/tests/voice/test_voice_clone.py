"""ref_strategy=clone (opt-in, VoxCPM2): the long original reference as the timbre AND as a continuation prompt
with its exact source transcript, then the dub text; no control text."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from babeldub.audio.io import write_wav
from babeldub.commands import project as project_cmd
from babeldub.commands import voice
from babeldub.core import layout
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import append_jsonl, latest_by, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_OK, BabelDubError
from babeldub.core.project import Project

N_LINES = 8
SHOUTS = (3, 4)
FAST = "banana " * 20  # 60 syllables over ~5 s of speech: outside ICL_RATE_RANGE


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--project")
        sp.add_argument("--pretty", action="store_true")

    voice.register(sub, common)
    project_cmd.register(sub, common)
    return p


def run(capsys: pytest.CaptureFixture[str], project: Path, *argv: str) -> tuple[int, dict[str, Any]]:
    args = parser().parse_args([argv[0], "--project", str(project), *argv[1:]])
    code = args.func(args)
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1, out
    return code, json.loads(out[0])


def speech(seconds: float, sr: int, f0: float) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    sig = sum(np.sin(2 * np.pi * k * f0 * t) / k for k in range(1, 5)) * (0.55 + 0.45 * np.sin(2 * np.pi * 4 * t))
    full = np.concatenate([np.zeros(int(0.1 * sr)), 0.3 * sig / np.max(np.abs(sig)), np.zeros(int(0.15 * sr))])
    return full.astype(np.float32)


def line_id(n: int) -> str:
    return f"fmod:Audio/Dialogue_en.bank#fsb0:{n:04d}"


def dub(n: int) -> str:
    return f"Это реплика номер {n}, малой."


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    home = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    root = home / "projects" / "clone"
    proj = Project.create(root, {"slug": "clone", "game_name": "Clone", "game_dir": str(tmp_path / "game"),
                                 "target_lang": "ru", "source_lang": "en",
                                 "tts": {"engine": "dummy", "best_of": 1, "ref_strategy": "clone"},
                                 "post": {"tolerance": 100.0}})
    sr = 48000
    lines, translations, analysis = [], [], []
    for n in range(1, N_LINES + 1):
        lid = line_id(n)
        u = make_uid(lid)
        wav = write_wav(root / layout.ORIG_AUDIO_DIR / f"{u}.wav", speech(5.0, sr, 110.0 + n), sr)
        lines.append({
            "id": lid, "uid": u, "adapter": "fmod", "asset_id": "fmod:Audio/Dialogue_en.bank",
            "entry": {"key": f"dlg_{n}", "sub": "fsb0", "index": n}, "kind": "dialogue", "locale": "en-US",
            "audio": {"sr": sr, "ch": 1, "dur_s": 5.25, "wav": proj.rel(wav)},
            "speaker_hint": "Zbyszek", "scene": "s1", "order": n, "fixed_length": False, "flags": [],
            "text": {"key": f"dlg_{n}", "src": f"This is the original line number {n}.", "src_lang": "en"},
        })
        translations.append({"id": lid, "dub_text": dub(n), "lang": "ru", "by": "agent", "rev": 1})
        loud = n in SHOUTS
        analysis.append({"id": lid, "uid": u, "dur_s": 5.25, "speech_s": 4.8, "class": "speech",
                         "prosody": {"f0_median_hz": 220.0 if loud else 130.0 + (n % 3 - 1), "f0_range_st": 6.0,
                                     "rms_db": -10.0 if loud else -20.0 + 0.1 * (n % 5 - 2), "voiced_ratio": 0.6,
                                     "syl_rate": 4.5}})
    write_jsonl(root / layout.LINES, lines, schema="babeldub.lines/1")
    write_jsonl(root / layout.TRANSLATIONS, translations, schema="babeldub.translations/1")
    write_jsonl(root / layout.ANALYSIS, analysis, schema="babeldub.analysis/1")
    return proj


def relink(project: Project, texts: dict[int, str]) -> None:
    """New source texts of lines n (an empty string: the line has no transcript)."""
    rows = [r for r in read_jsonl(project.path(layout.LINES)) if "_schema" not in r]
    for row in rows:
        n = int(row["id"][-4:])
        if n in texts:
            row["text"]["src"] = texts[n]
    write_jsonl(project.path(layout.LINES), rows, schema="babeldub.lines/1")


def set_class(project: Project, n: int, cls: str) -> None:
    rows = [r for r in read_jsonl(project.path(layout.ANALYSIS)) if "_schema" not in r]
    for row in rows:
        if row["id"] == line_id(n):
            row["class"] = cls
    write_jsonl(project.path(layout.ANALYSIS), rows, schema="babeldub.analysis/1")


def results(project: Project) -> dict[str, dict[str, Any]]:
    return latest_by(read_jsonl(project.path(layout.SYNTH_RESULTS)))


def judge(project: Project, n: int, attempt: int, reasons: list[str]) -> None:
    append_jsonl(project.path(layout.QA), [{"id": line_id(n), "attempt": attempt, "verdict": "fail", "reasons": reasons}])
    append_jsonl(project.path(layout.DECISIONS), [{"id": line_id(n), "decision": "regen", "attempt": attempt, "by": "auto"}])


def plan(project: Project, *argv: str, strategy: str = "clone",
         notes: list[str] | None = None) -> dict[str, voice.Job]:
    args = parser().parse_args(["synth", *argv])
    jobs, *_ = voice._plan_synth(Project.load(str(project.root)), args, voice.resolve_engine(project, "dummy"),
                                 strategy, notes=notes)
    return {j.id: j for j in jobs}


def transcripts(project: Project) -> dict[str, str | None]:
    """{line id: the usable source transcript _source_transcript gives}, prepared like the planner does."""
    lines = latest_by(read_jsonl(project.path(layout.LINES)))
    transcript = voice._source_transcript(project, voice._text_preparer())
    return {lid: transcript(line) for lid, line in lines.items()}


# ---- strategy resolution and settings ----------------------------------------------------------
def test_effective_strategy_and_setting(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    assert voice.effective_strategy("clone", "voxcpm") == ("clone", None)
    assert voice.effective_strategy("clone", "dummy") == ("clone", None)
    strategy, note = voice.effective_strategy("clone", "qwen3tts")
    assert strategy == "auto" and note is not None and "ref_strategy=clone needs the voxcpm engine" in note
    assert voice.effective_strategy("auto", "voxcpm") == ("tags", None)  # the default does not change
    assert "clone" in voice.LONG_REF_MODES
    code, out = run(capsys, project.root, "set", "tts.ref_strategy=tags")
    assert code == EXIT_OK
    code, out = run(capsys, project.root, "set", "tts.ref_strategy=clone")
    assert code == EXIT_OK and out["changed"]["tts.ref_strategy"] == {"old": "tags", "new": "clone"}, out
    with pytest.raises(BabelDubError):
        run(capsys, project.root, "set", "tts.ref_strategy=cloning")
    code, out = run(capsys, project.root, "synth", "--dry-run")
    assert out["ref_strategy"] == "clone" and out["by_ref"] == {"clone": N_LINES} and "delivery" not in out, out
    code, out = run(capsys, project.root, "synth", "--dry-run", "--engine", "qwen3tts", "--device", "cpu")
    assert out["ref_strategy"] == "auto" and any("ref_strategy=clone needs the voxcpm engine" in n for n in out["notes"])
    assert "clone" not in out["by_ref"]


# ---- the planner and synth on the dummy engine -------------------------------------------------
def test_clone_rows_and_request(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK and out["done"] == N_LINES and out["by_ref"] == {"clone": N_LINES}, out
    assert "delivery" not in out  # no take carries delivery tags
    clone_notes = [n for n in out["notes"] if n.startswith("clone:")]
    assert len(clone_notes) == 1 and f"{N_LINES} takes" in clone_notes[0] and "tts.delivery_tags=on" in clone_notes[0]
    rows = results(project)
    for n in range(1, N_LINES + 1):
        row = rows[line_id(n)]
        assert row["ref_mode"] == "clone" and row["ref_id"].startswith("long:Zbyszek:")
        assert row["prompt_id"] == row["ref_id"] and row["prompt_wav"] == row["ref_wav"]
        assert row["ref_wav"].startswith(layout.SYNTH_PROMPTS_DIR) and row["bucket"] == "calm"
        assert not {"control", "tags", "tags_source", "delivery_tags", "tempo_hint", "prompt_dropped"} & set(row)
    assert run(capsys, project.root, "synth")[1]["skipped"]["done"] == N_LINES  # idempotent

    job = plan(project, "--force")[line_id(3)]
    usable = transcripts(project)
    assert job.prompt is not None and job.control is None and job.delivery is None
    assert job.prompt.text == " ".join(str(usable[lid]) for lid in job.prompt.lines) and len(job.prompt.lines) == 3
    run_ = voice._SynthRun(project, voice.resolve_engine(project, "dummy"), project.path(layout.SYNTH_RESULTS),
                           project.path("synth", "refcache"))
    req = run_.request_for(job)
    assert req["prompt_wav"] == str(job.prompt.wav) == req["ref_wav"] and req["prompt_text"] == job.prompt.text
    assert "control" not in req and req["text"] == job.text and req["params"].get("x_vector_only") is None


def test_delivery_tags_off_has_no_clone_note(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, project.root, "set", "tts.delivery_tags=off")
    code, out = run(capsys, project.root, "synth", "--dry-run")
    assert out["by_ref"] == {"clone": N_LINES} and not any(n.startswith("clone:") for n in out["notes"]), out


def test_parts_need_a_usable_transcript(project: Project) -> None:
    relink(project, {1: "", 2: "  ", 3: FAST, 4: FAST, 5: FAST})
    usable = transcripts(project)
    assert [n for n in range(1, N_LINES + 1) if usable[line_id(n)]] == [6, 7, 8]
    job = plan(project)[line_id(1)]
    assert job.ref.mode == "clone" and job.prompt is not None
    assert sorted(job.prompt.lines) == [line_id(6), line_id(7), line_id(8)]
    assert job.prompt.text == " ".join(str(usable[lid]) for lid in job.prompt.lines)
    assert "banana" not in job.prompt.text and "  " not in job.prompt.text


def test_clone_and_tags_share_the_wav_of_the_same_parts(project: Project) -> None:
    clone_job = plan(project)[line_id(1)]
    tags_job = plan(project, strategy="tags")[line_id(1)]
    assert clone_job.prompt is not None and tags_job.prompt is None
    assert clone_job.ref.wav == tags_job.ref.wav and clone_job.ref.ref_id == tags_job.ref.ref_id  # same parts
    assert clone_job.key != tags_job.key  # the prompt (id + transcript) is part of the synth key
    assert plan(project)[line_id(1)].key == clone_job.key  # deterministic
    relink(project, {n: "This is the original line number one." for n in range(1, N_LINES + 1)})
    again = plan(project)[line_id(1)]
    assert again.ref.wav == clone_job.ref.wav and again.prompt is not None and again.key != clone_job.key
    picked = list(clone_job.prompt.lines)
    relink(project, {int(lid[-4:]): "" for lid in picked})  # other parts: another file and id
    other = plan(project)[line_id(1)]
    assert other.prompt is not None and not set(other.prompt.lines) & set(picked)
    assert other.ref.wav != clone_job.ref.wav and other.ref.ref_id != clone_job.ref.ref_id


def test_fallback_to_tags_without_transcripts(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    relink(project, {n: "" for n in range(1, N_LINES + 1)})
    notes: list[str] = []
    jobs = plan(project, notes=notes)
    shout, plain = jobs[line_id(3)], jobs[line_id(1)]
    assert shout.ref.mode == "tags" and shout.prompt is None and shout.ref_reason == voice.CLONE_FALLBACK
    assert shout.control == "shouting" and shout.delivery is not None and shout.delivery.source == "measured"
    assert plain.ref.mode == "tags" and plain.control is None and plain.ref_reason == voice.CLONE_FALLBACK
    assert not any(n.startswith("clone:") for n in notes)
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK and out["by_ref"] == {"tags": N_LINES}, out
    assert out["delivery"] == {"by_source": {"measured": 2, "none": N_LINES - 2}, "tags": {"shouting": 2}}
    row = results(project)[line_id(3)]
    assert row["ref_reason"] == voice.CLONE_FALLBACK and row["control"] == "shouting" and "prompt_id" not in row


def test_bucket_reference_and_rebucket_on_regen(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, project.root, "set", "tts.tags_reference=bucket")
    jobs = plan(project)
    loud, calm = jobs[line_id(3)], jobs[line_id(1)]
    assert loud.bucket == "loud" and ":loud:" in loud.ref.ref_id and loud.prompt is not None
    assert sorted(loud.prompt.lines) == [line_id(3), line_id(4)]  # the two shouts: 10.5 s of transcribed material
    usable = transcripts(project)
    assert loud.prompt.text == " ".join(str(usable[lid]) for lid in loud.prompt.lines)
    assert calm.bucket == "calm" and ":loud:" not in calm.ref.ref_id
    run(capsys, project.root, "synth")
    assert results(project)[line_id(3)]["bucket"] == "loud"
    judge(project, 3, 1, ["flat"])  # a flat take: the regen takes the next nearest bucket
    code, out = run(capsys, project.root, "synth", "--attempt-next")
    assert code == EXIT_OK and out["done"] == 1, out
    row = results(project)[line_id(3)]
    assert row["attempt"] == 2 and row["reason"] == "regen" and row["ref_mode"] == "clone"
    assert row["bucket"] == "calm" and ":loud:" not in row["prompt_id"] and "control" not in row
    # a loud bucket without enough transcribed material: no loud clone reference, the calm one serves
    relink(project, {3: FAST})
    assert plan(project, "--force")[line_id(4)].bucket == "calm"


def test_short_word_line_first_then_clone_on_regen(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    set_class(project, 8, "short_word")
    append_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": line_id(8), "dub_text": dub(8), "lang": "ru", "by": "agent", "rev": 2, "delivery": ["shouting"]}])
    short = plan(project)[line_id(8)]
    assert short.ref.mode == "line" and short.prompt is None and short.control == "shouting"  # as under tags
    run(capsys, project.root, "synth")
    assert results(project)[line_id(8)]["ref_mode"] == "line"
    judge(project, 8, 1, ["sim"])
    code, out = run(capsys, project.root, "synth", "--attempt-next")
    assert out["done"] == 1, out
    row = results(project)[line_id(8)]
    assert row["attempt"] == 2 and row["ref_mode"] == "clone" and row["prompt_id"] == row["ref_id"]
    assert "control" not in row and "tags_source" not in row


def test_tagged_take_of_a_clone_character_goes_stale(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    set_class(project, 8, "short_word")
    append_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": line_id(8), "dub_text": dub(8), "lang": "ru", "by": "agent", "rev": 2, "delivery": ["shouting"]}])
    run(capsys, project.root, "synth")
    assert results(project)[line_id(8)]["control"] == "shouting"
    append_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": line_id(8), "dub_text": dub(8), "lang": "ru", "by": "agent", "rev": 3, "delivery": ["whispered"]}])
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK and out["done"] == 1 and out["skipped"]["done"] == N_LINES - 1, out  # clone takes stand
    row = results(project)[line_id(8)]
    assert row["attempt"] == 2 and row["reason"] == "delivery" and row["ref_mode"] == "line"
    assert row["control"] != "shouting" and row["tags_source"] == "agent"


def test_a_reref_take_keeps_its_prompt_family_when_its_delivery_changes(project: Project,
                                                                         capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, project.root, "synth")
    append_jsonl(project.path(layout.QA), [{"id": line_id(3), "attempt": 1, "verdict": "fail", "reasons": ["sim"]}])
    append_jsonl(project.path(layout.DECISIONS), [{"id": line_id(3), "decision": "reref", "attempt": 1, "by": "agent"}])
    run(capsys, project.root, "synth", "--attempt-next")
    row = results(project)[line_id(3)]
    assert row["attempt"] == 2 and row["ref_mode"] == "line" and row["control"] == "shouting"
    append_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": line_id(3), "dub_text": dub(3), "lang": "ru", "by": "agent", "rev": 2, "delivery": ["whispered"]}])
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK and out["done"] == 1 and out["skipped"]["done"] == N_LINES - 1, out
    row = results(project)[line_id(3)]
    assert row["attempt"] == 3 and row["reason"] == "delivery" and row["ref_mode"] == "line"  # not a clone take
    assert row["control"] not in (None, "shouting") and row["tags"] == ["whispered"] and row["tags_source"] == "agent"


def test_a_delivery_revoice_keeps_the_char_reference_a_second_reref_chose(project: Project, tmp_path: Path) -> None:
    # two refs, none chosen: reref #1 gave A (top score), reref #2 avoided A and gave B; a delivery
    # re-voice of the B take keeps B instead of going back to the top-ranked A
    refs = []
    for name, score in (("A", 0.9), ("B", 0.8)):
        wav = tmp_path / f"{name}.wav"
        write_wav(wav, np.zeros(16000, dtype=np.float32), 16000)
        refs.append({"ref_id": name, "char_id": "c1", "wav": str(wav), "score": score, "dur_s": 1.0})

    def keep(text: str, _lang: str, _mode: str) -> str:
        return text

    assert voice._char_ref(project, refs, "en", None, keep).ref_id == "A"
    assert voice._char_ref(project, refs, "en", "A", keep).ref_id == "B"
    assert voice._char_ref(project, refs, "en", None, keep, keep_ref="B").ref_id == "B"
    assert voice._char_ref(project, refs, "en", None, keep, keep_ref="gone").ref_id == "A"  # a removed ref falls back


def test_a_fallback_tags_take_stays_tags_when_its_delivery_changes(project: Project,
                                                                   capsys: pytest.CaptureFixture[str]) -> None:
    originals = {n: f"This is the original line number {n}." for n in range(1, N_LINES + 1)}
    relink(project, {n: "" for n in originals})
    run(capsys, project.root, "synth")
    assert results(project)[line_id(3)]["ref_mode"] == "tags"
    relink(project, originals)  # the character now has a clone reference
    append_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": line_id(3), "dub_text": dub(3), "lang": "ru", "by": "agent", "rev": 2, "delivery": ["whispered"]}])
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK and out["done"] == 1 and out["by_ref"] == {"tags": 1}, out
    row = results(project)[line_id(3)]
    assert row["reason"] == "delivery" and row["ref_mode"] == "tags" and row["tags"] == ["whispered"]
    assert row["control"] and "prompt_id" not in row


def test_switching_strategy_needs_force_or_a_decision(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, project.root, "set", "tts.ref_strategy=tags")
    assert run(capsys, project.root, "synth")[1]["by_ref"] == {"tags": N_LINES}
    run(capsys, project.root, "set", "tts.ref_strategy=clone")
    code, out = run(capsys, project.root, "synth")
    assert out["done"] == 0 and out["skipped"]["done"] == N_LINES, out  # done tags takes stand
    judge(project, 1, 1, ["sim"])
    code, out = run(capsys, project.root, "synth", "--attempt-next")
    assert out["done"] == 1 and results(project)[line_id(1)]["ref_mode"] == "clone", out
    code, out = run(capsys, project.root, "synth", "--force", "--ids", line_id(3))
    row = results(project)[line_id(3)]
    assert out["done"] == 1 and row["reason"] == "force" and row["ref_mode"] == "clone" and "control" not in row
    run(capsys, project.root, "set", "tts.ref_strategy=tags")  # back: clone takes have no delivery, they stand
    code, out = run(capsys, project.root, "synth")
    assert out["done"] == 0 and out["skipped"]["done"] == N_LINES, out
