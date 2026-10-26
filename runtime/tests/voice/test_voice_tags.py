"""ref_strategy=tags (the v0.3 default on VoxCPM2): long original reference + a delivery control text."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from babeldub.analysis.prosody_tags import Measured
from babeldub.audio.io import write_wav
from babeldub.commands import project as project_cmd
from babeldub.commands import voice
from babeldub.core import layout
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import append_jsonl, latest_by, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_OK, BabelDubError
from babeldub.core.project import DEFAULT_SETTINGS, Project

N_LINES = 8
SHOUTS = (3, 4)


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
    root = home / "projects" / "tags"
    proj = Project.create(root, {"slug": "tags", "game_name": "Tags", "game_dir": str(tmp_path / "game"),
                                 "target_lang": "ru", "source_lang": "en",
                                 "tts": {"engine": "dummy", "best_of": 1}, "post": {"tolerance": 100.0}})
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


def results(project: Project) -> dict[str, dict[str, Any]]:
    return latest_by(read_jsonl(project.path(layout.SYNTH_RESULTS)))


def retranslate(project: Project, n: int, **fields: Any) -> None:
    append_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": line_id(n), "dub_text": dub(n), "lang": "ru", "by": "agent", "rev": 2, **fields}])


def judge(project: Project, n: int, attempt: int, reasons: list[str]) -> None:
    """A failed QA row and the automatic regen decision for line n."""
    append_jsonl(project.path(layout.QA), [{"id": line_id(n), "attempt": attempt, "verdict": "fail", "reasons": reasons}])
    append_jsonl(project.path(layout.DECISIONS), [{"id": line_id(n), "decision": "regen", "attempt": attempt, "by": "auto"}])


def accept(project: Project, n: int, attempt: int) -> None:
    append_jsonl(project.path(layout.DECISIONS), [{"id": line_id(n), "decision": "accept", "attempt": attempt, "by": "auto"}])


def reanalyse(project: Project, shouts: tuple[int, ...]) -> None:
    """New analysis rows: lines `shouts` are the shouted ones now (the measured tags drift)."""
    rows = [r for r in read_jsonl(project.path(layout.ANALYSIS)) if "_schema" not in r]
    for row in rows:
        n = int(row["id"][-4:])
        loud = n in shouts
        row["prosody"]["f0_median_hz"] = 220.0 if loud else 130.0 + (n % 3 - 1)
        row["prosody"]["rms_db"] = -10.0 if loud else -20.0 + 0.1 * (n % 5 - 2)
    write_jsonl(project.path(layout.ANALYSIS), rows, schema="babeldub.analysis/1")


def plan(project: Project, *argv: str) -> dict[str, voice.Job]:
    args = parser().parse_args(["synth", *argv])
    jobs, *_ = voice._plan_synth(project, args, voice.resolve_engine(project, "dummy"), "tags")
    return {j.id: j for j in jobs}


# ---- precedence and policy (pure functions) ---------------------------------------------------
def test_final_tags_precedence() -> None:
    measured = Measured(["calm"], {"f0": -2.0, "rms": -1.5})
    agent = {"dub_text": "x", "delivery": ["urgent"]}
    assert voice.plan_delivery("on", agent, measured) == voice.Delivery(["urgent"], "agent", "urgent")
    assert voice.plan_delivery("on", {"dub_text": "x", "delivery": []}, measured) == voice.Delivery([], "agent", None)
    assert voice.plan_delivery("on", {"dub_text": "x"}, measured) == voice.Delivery(["calm"], "measured", "calm")
    assert voice.plan_delivery("on", {"dub_text": "x"}, None) == voice.Delivery([], "none", None)
    assert voice.plan_delivery("on", {"dub_text": "x"}, Measured([], {})) == voice.Delivery([], "none", None)
    assert voice.plan_delivery("measured", agent, measured).source == "measured"  # the agent is ignored
    assert voice.plan_delivery("off", agent, measured) == voice.Delivery([], "none", None)
    quick = Measured(["quick"], {"rate": 1.7})
    assert voice.plan_delivery("on", {}, quick).control == "slightly faster"
    assert voice.plan_delivery("on", {"delivery": ["quick", "sad", "bogus"]}, quick).control == "fast pace, sad"
    # the agent repeating the hint is the hint: measured, with its "slightly faster"
    assert voice.plan_delivery("on", {"delivery": ["Quick"]}, quick) == voice.Delivery(["quick"], "measured", "slightly faster")
    assert voice.plan_delivery("on", {"delivery": ["quick", "bogus"]}, quick).source == "measured"
    assert voice.plan_delivery("on", {"delivery": []}, Measured([], {})) == voice.Delivery([], "agent", None)


def test_regen_drop_rule() -> None:
    assert voice.drops_control("regen", 2, ["cer"])
    assert voice.drops_control("regen", 2, ["cer", "flat"])
    assert not voice.drops_control("regen", 2, ["flat"]) and not voice.drops_control("regen", 2, ["mos"])
    assert voice.drops_control("regen", 3, ["flat"])  # any regen from attempt 3 on
    assert not voice.drops_control("delivery", 3, []) and not voice.drops_control("retry", 4, ["cer"])
    tagged = voice.Delivery(["shouting"], "measured", "shouting")
    assert voice.apply_drop(tagged, True) == voice.Delivery(["shouting"], "dropped", None)
    assert voice.apply_drop(tagged, False) is tagged
    assert voice.apply_drop(voice.Delivery([], "agent", None), True).source == "agent"  # nothing to drop


def test_delivery_matches_a_take() -> None:
    plan_ = voice.Delivery(["shouting"], "measured", "shouting")
    assert voice.delivery_matches({"text": "x"}, plan_)  # a take made before v0.3 / by another strategy stands
    assert voice.delivery_matches({"tags_source": "measured", "control": "shouting"}, plan_)
    assert not voice.delivery_matches({"tags_source": "agent", "control": "urgent"}, plan_)
    assert not voice.delivery_matches({"tags_source": "none", "control": None}, plan_)
    assert voice.delivery_matches({"tags_source": "dropped", "tags": ["shouting"], "control": None}, plan_)
    assert not voice.delivery_matches({"tags_source": "dropped", "tags": ["calm"], "control": None}, plan_)
    assert voice.delivery_matches({"tags_source": "dropped", "tags": ["calm"]}, voice.Delivery([], "agent", None))


def test_effective_strategy() -> None:
    assert voice.effective_strategy("auto", "voxcpm") == ("tags", None)
    assert voice.effective_strategy("auto", "dummy") == ("tags", None)
    assert voice.effective_strategy("auto", "qwen3tts") == ("auto", None)
    strategy, note = voice.effective_strategy("tags", "chatterbox")
    assert strategy == "auto" and note is not None and "needs the voxcpm engine" in note
    assert voice.effective_strategy("bootstrap", "voxcpm") == ("bootstrap", None)  # explicit bootstrap stays
    assert voice.effective_strategy("tags", "voxcpm") == ("tags", None)


# ---- the planner and synth on the dummy engine -------------------------------------------------
def test_default_chain_measures_and_records_tags(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK and out["done"] == N_LINES and out["by_ref"] == {"tags": N_LINES}, out
    assert out["delivery"] == {"by_source": {"measured": 2, "none": N_LINES - 2}, "tags": {"shouting": 2}}
    assert "skipped" not in out or "bootstrap_wait" not in out["skipped"]  # no bootstrap phases
    rows = results(project)
    shout = rows[line_id(3)]
    assert shout["control"] == "shouting" and shout["tags"] == ["shouting"] and shout["tags_source"] == "measured"
    assert shout["ref_mode"] == "tags" and shout["ref_wav"].startswith(layout.SYNTH_PROMPTS_DIR)
    assert shout["ref_id"].startswith("long:Zbyszek:") and ":loud:" not in shout["ref_id"]  # tags_reference=main
    assert "prompt_id" not in shout and "prompt_dropped" not in shout
    plain = rows[line_id(1)]
    assert plain["control"] is None and plain["tags"] == [] and plain["tags_source"] == "none"
    assert plain["ref_id"] == shout["ref_id"] and plain["est_s"] == shout["est_s"]  # control not in the estimate
    code, out = run(capsys, project.root, "synth")
    assert out["done"] == 0 and out["skipped"]["done"] == N_LINES  # idempotent
    code, out = run(capsys, project.root, "status")
    assert out["counts"]["delivery"] == {"by_source": {"none": N_LINES - 2, "measured": 2}, "tags": {"shouting": 2}}


def test_delivery_summary_counts_every_planned_take_status_the_latest_ok(project: Project,
                                                                          capsys: pytest.CaptureFixture[str]) -> None:
    every = {"by_source": {"measured": 2, "none": N_LINES - 2}, "tags": {"shouting": 2}}
    code, out = run(capsys, project.root, "synth", "--dry-run", "--limit", "1")
    assert out["would_run"] == 1 and out["planned"] == N_LINES and out["delivery"] == every, out
    code, out = run(capsys, project.root, "synth", "--limit", "3")
    assert out["done"] == 3 and out["delivery"] == every, out
    run(capsys, project.root, "synth")
    append_jsonl(project.path(layout.SYNTH_RESULTS), [  # a later failed attempt does not hide the ok take
        {"id": line_id(3), "attempt": 2, "status": "error", "wav": None, "text": dub(3), "error": "worker died"}])
    code, out = run(capsys, project.root, "status")
    assert out["counts"]["delivery"] == every, out["counts"]


def test_request_carries_the_control_and_the_key_changes_with_it(project: Project) -> None:
    jobs = plan(project)
    shout, plain = jobs[line_id(3)], jobs[line_id(1)]
    run_ = voice._SynthRun(project, voice.resolve_engine(project, "dummy"), project.path(layout.SYNTH_RESULTS),
                           project.path("synth", "refcache"))
    req = run_.request_for(shout)
    assert req["control"] == "shouting" and req["text"] == shout.text  # the worker builds "(control)text"
    assert "(" not in req["text"] and "prompt_wav" not in req
    assert "control" not in run_.request_for(plain)
    key = shout.key
    retranslate(project, 3, delivery=["urgent"])
    again = plan(project, "--force")[line_id(3)]
    assert again.control == "urgent" and again.key != key and again.text == shout.text
    retranslate(project, 3, delivery=["shouting"])  # the hint repeated: the measured delivery, the same key
    again = plan(project, "--force")[line_id(3)]
    assert again.key == key and again.delivery == voice.Delivery(["shouting"], "measured", "shouting")


def test_agent_delivery_makes_a_new_take_not_a_retranslation(project: Project,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, project.root, "synth")
    first = results(project)[line_id(3)]
    retranslate(project, 3, delivery=["urgent"])  # the agent overrides the measured shout
    retranslate(project, 1, delivery=[])  # explicitly neutral = the request it already had
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK and out["done"] == 1, out
    assert out["delivery"] == {"by_source": {"agent": 1}, "tags": {"urgent": 1}}
    row = results(project)[line_id(3)]
    assert row["attempt"] == 2 and row["reason"] == "delivery" and row["text"] == first["text"]
    assert row["control"] == "urgent" and row["tags_source"] == "agent" and row["key"] != first["key"]
    assert results(project)[line_id(1)]["attempt"] == 1
    assert run(capsys, project.root, "synth")[1]["done"] == 0

    # tts.delivery_tags=measured ignores the agent, off drops every control text: new takes
    run(capsys, project.root, "set", "tts.delivery_tags=measured")
    code, out = run(capsys, project.root, "synth")
    assert out["done"] == 1 and results(project)[line_id(3)]["tags_source"] == "measured"
    run(capsys, project.root, "set", "tts.delivery_tags=off")
    code, out = run(capsys, project.root, "synth")
    assert out["done"] == 1 and out["delivery"] == {"by_source": {"none": 1}, "tags": {}}
    assert results(project)[line_id(4)]["control"] is None and results(project)[line_id(4)]["attempt"] == 2
    # a delivery change is no retranslation: it counts toward tts.max_attempts of the same text
    assert code == EXIT_ATTENTION and out["skipped"]["max_attempts"] == 1 and out["attention"] == [line_id(3)]
    assert results(project)[line_id(3)]["attempt"] == 3


def test_accepted_take_survives_measured_drift_not_an_agent_change(project: Project,
                                                                   capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, project.root, "synth")
    assert results(project)[line_id(3)]["delivery_tags"] == "on"
    accept(project, 3, 1)
    reanalyse(project, (4, 5))  # line 3 measures plain now, line 5 shouted
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK and out["done"] == 1 and out["skipped"]["done"] == N_LINES - 1, out
    assert results(project)[line_id(3)]["attempt"] == 1  # accepted: the drift alone does not burn an attempt
    new = results(project)[line_id(5)]  # not accepted: voiced again with its new tags
    assert new["attempt"] == 2 and new["reason"] == "delivery" and new["control"] == "shouting"
    retranslate(project, 3, delivery=["urgent"])  # the agent's delivery does re-voice an accepted take
    code, out = run(capsys, project.root, "synth")
    row = results(project)[line_id(3)]
    assert out["done"] == 1 and row["attempt"] == 2 and row["reason"] == "delivery" and row["control"] == "urgent"
    accept(project, 3, 2)
    retranslate(project, 3)  # the agent withdraws it (a new revision without delivery): measured, plain now
    assert run(capsys, project.root, "synth")[1]["done"] == 1 and results(project)[line_id(3)]["tags_source"] == "none"


def test_accepted_take_follows_a_delivery_tags_change(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, project.root, "synth")
    accept(project, 3, 1)
    accept(project, 1, 1)
    run(capsys, project.root, "set", "tts.delivery_tags=off")
    code, out = run(capsys, project.root, "synth")
    assert out["done"] == 2, out  # the accepted shout too: the setting changed, not the measurement
    row = results(project)[line_id(3)]
    assert row["attempt"] == 2 and row["control"] is None and row["delivery_tags"] == "off"
    assert results(project)[line_id(1)]["attempt"] == 1
    accept(project, 3, 2)
    run(capsys, project.root, "set", "tts.delivery_tags=on")
    code, out = run(capsys, project.root, "synth")
    assert out["done"] == 2, out  # an accepted take made under `off` gets its tags back
    assert results(project)[line_id(3)]["control"] == "shouting" and results(project)[line_id(3)]["attempt"] == 3


def test_accepted_delivery_stands_rule() -> None:
    measured = voice.Delivery(["shouting"], "measured", "shouting")
    plain = voice.Delivery([], "none", None)
    old = {"tags_source": "measured", "tags": ["calm"], "control": "calm"}  # no delivery_tags: an older take
    assert voice.accepted_delivery_stands(old, measured, "on") and voice.accepted_delivery_stands(old, plain, "on")
    assert not voice.accepted_delivery_stands(old, plain, "off")
    agent = {"tags_source": "agent", "tags": ["urgent"], "control": "urgent", "delivery_tags": "on"}
    assert not voice.accepted_delivery_stands(agent, measured, "measured")  # the agent's tags are ignored now
    assert voice.accepted_delivery_stands(agent, voice.Delivery(["urgent"], "measured", "urgent"), "measured")
    assert not voice.accepted_delivery_stands(old, voice.Delivery(["sad"], "agent", "sad"), "on")
    off = {"tags_source": "none", "tags": [], "control": None, "delivery_tags": "off"}
    assert not voice.accepted_delivery_stands(off, measured, "on") and voice.accepted_delivery_stands(off, plain, "on")
    dropped = {"tags_source": "dropped", "tags": ["calm"], "control": None, "delivery_tags": "on"}
    assert voice.accepted_delivery_stands(dropped, measured, "on")
    assert not voice.accepted_delivery_stands(dropped, voice.Delivery(["sad"], "agent", "sad"), "on")


def test_cer_regen_drops_the_control_flat_regen_keeps_it(project: Project,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    retranslate(project, 2, delivery=["sad"])
    run(capsys, project.root, "synth")
    judge(project, 3, 1, ["flat"])  # delivery: the tags stay, a new seed
    judge(project, 2, 1, ["cer"])  # words: the control text may have garbled them
    code, out = run(capsys, project.root, "synth", "--attempt-next")
    assert code == EXIT_OK and out["done"] == 2, out
    assert out["delivery"] == {"by_source": {"measured": 1, "dropped": 1}, "tags": {"shouting": 1}}
    rows = results(project)
    kept, dropped = rows[line_id(3)], rows[line_id(2)]
    assert kept["attempt"] == 2 and kept["reason"] == "regen" and kept["control"] == "shouting"
    assert dropped["attempt"] == 2 and dropped["control"] is None and dropped["tags_source"] == "dropped"
    assert dropped["tags"] == ["sad"]
    assert run(capsys, project.root, "synth")[1]["done"] == 0  # a dropped take is not re-tagged
    judge(project, 3, 2, ["flat"])  # the third attempt goes without the control text
    run(capsys, project.root, "synth", "--attempt-next")
    third = results(project)[line_id(3)]
    assert third["attempt"] == 3 and third["tags_source"] == "dropped" and third["control"] is None


def test_drop_rule_counts_the_takes_of_the_current_text(project: Project,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, project.root, "synth")
    judge(project, 3, 1, ["flat"])
    run(capsys, project.root, "synth", "--attempt-next")  # take 2 of the first text keeps the tags
    append_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": line_id(3), "dub_text": "Совсем другая реплика, малой.", "lang": "ru", "by": "agent", "rev": 2}])
    run(capsys, project.root, "synth")
    row = results(project)[line_id(3)]
    assert row["attempt"] == 3 and row["reason"] == "retranslated" and row["control"] == "shouting"
    judge(project, 3, 3, ["flat"])  # attempt 4 of the line, but only take 2 of the new text: the tags stay
    run(capsys, project.root, "synth", "--attempt-next")
    row = results(project)[line_id(3)]
    assert row["attempt"] == 4 and row["reason"] == "regen"
    assert row["control"] == "shouting" and row["tags_source"] == "measured"
    judge(project, 3, 4, ["flat"])  # take 3 of the new text drops them
    run(capsys, project.root, "synth", "--attempt-next")
    row = results(project)[line_id(3)]
    assert row["attempt"] == 5 and row["tags_source"] == "dropped" and row["control"] is None


def test_short_word_gets_tags_and_bucket_reference(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    rows = read_jsonl(project.path(layout.ANALYSIS))
    for row in rows:
        if row.get("id") == line_id(8):
            row["class"] = "short_word"
    write_jsonl(project.path(layout.ANALYSIS), [r for r in rows if "_schema" not in r], schema="babeldub.analysis/1")
    retranslate(project, 8, delivery=["shouting"])
    jobs = plan(project)
    short = jobs[line_id(8)]
    assert short.ref.mode == "line" and short.control == "shouting"  # the line itself, still with its tags
    assert jobs[line_id(3)].ref.ref_id.startswith("long:Zbyszek:") and jobs[line_id(3)].bucket == "calm"
    run(capsys, project.root, "set", "tts.tags_reference=bucket")
    jobs = plan(Project.load(str(project.root)))
    assert ":loud:" in jobs[line_id(3)].ref.ref_id and jobs[line_id(3)].bucket == "loud"  # two shouts: 10 s
    assert ":loud:" not in jobs[line_id(1)].ref.ref_id and jobs[line_id(1)].bucket == "calm"


def test_bootstrap_explicit_and_engines_without_control(project: Project, capsys: pytest.CaptureFixture[str],
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    run(capsys, project.root, "set", "tts.ref_strategy=bootstrap")
    code, out = run(capsys, project.root, "synth", "--dry-run")
    assert out["ref_strategy"] == "bootstrap" and "delivery" not in out and set(out["by_ref"]) == {"bootstrap-src"}
    run(capsys, project.root, "set", "tts.ref_strategy=auto")

    def no_measuring(*_: Any, **__: Any) -> None:
        raise AssertionError("an engine without a control text measures no tags")

    monkeypatch.setattr(voice, "measured_tags", no_measuring)
    code, out = run(capsys, project.root, "synth", "--dry-run", "--engine", "qwen3tts", "--device", "cpu")
    assert out["engine"] == "qwen3tts" and out["ref_strategy"] == "auto" and "delivery" not in out
    ignored = [n for n in out["notes"] if n.startswith("control_ignored")]
    assert len(ignored) == 1 and "measured on the originals" in ignored[0] and "translator" not in ignored[0]
    retranslate(project, 2, delivery=["sad"])
    retranslate(project, 5, delivery=[])
    code, out = run(capsys, project.root, "synth", "--dry-run", "--engine", "qwen3tts", "--device", "cpu")
    ignored = [n for n in out["notes"] if n.startswith("control_ignored")]
    assert len(ignored) == 1 and "the translator's on 1 lines" in ignored[0]
    run(capsys, project.root, "set", "tts.ref_strategy=tags")
    code, out = run(capsys, project.root, "synth", "--dry-run", "--engine", "chatterbox", "--device", "cpu")
    assert out["ref_strategy"] == "auto" and any("needs the voxcpm engine" in n for n in out["notes"])
    run(capsys, project.root, "set", "tts.delivery_tags=off")
    code, out = run(capsys, project.root, "synth", "--dry-run", "--engine", "qwen3tts", "--device", "cpu")
    assert not any(n.startswith("control_ignored") for n in out["notes"])


def test_settings_defaults_and_validation(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    assert DEFAULT_SETTINGS["tts"]["ref_strategy"] == "auto"
    assert DEFAULT_SETTINGS["tts"]["tags_reference"] == "main" and DEFAULT_SETTINGS["tts"]["delivery_tags"] == "on"
    code, out = run(capsys, project.root, "set", "tts.ref_strategy=tags", "tts.tags_reference=bucket",
                    "tts.delivery_tags=measured")
    assert code == EXIT_OK and "warnings" not in out, out
    assert out["changed"]["tts.tags_reference"] == {"old": "main", "new": "bucket"}
    for bad in ("tts.ref_strategy=prosody", "tts.tags_reference=nearest", "tts.delivery_tags=maybe"):
        with pytest.raises(BabelDubError) as err:
            run(capsys, project.root, "set", bad)
        assert err.value.code == 2
    project = Project.load(str(project.root))
    project.data["tts"]["delivery_tags"] = "loud"
    project.save()
    with pytest.raises(BabelDubError, match="tts.delivery_tags"):
        plan(project)
    for raw, setting in ((False, "off"), (True, "on"), (None, "on")):  # hand-edited project.json values
        project.data["tts"]["delivery_tags"] = raw
        assert voice.delivery_setting(project) == setting
    project.data["tts"]["delivery_tags"] = False  # not "on": the measured shouts get no control text
    jobs = plan(project)
    assert len(jobs) == N_LINES and all(job.delivery == voice.Delivery([], "none", None) for job in jobs.values())
    project.save()
    code, out = run(capsys, project.root, "set", "tts.delivery_tags=on")  # `set` repairs it with a word
    assert code == EXIT_OK and out["changed"]["tts.delivery_tags"] == {"old": False, "new": "on"}, out
