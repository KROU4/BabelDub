"""Line-class rules on the dummy engine: keep_original classes, short words, glossary pronunciation, stress."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from babeldub.analysis.store import ANALYSIS_SCHEMA
from babeldub.audio.io import write_wav
from babeldub.commands import voice
from babeldub.core import layout
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import append_jsonl, latest_by, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_OK
from babeldub.core.project import Project


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--project")
        sp.add_argument("--pretty", action="store_true")

    voice.register(sub, common)
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
    return np.concatenate([np.zeros(int(0.1 * sr)), 0.3 * sig / np.max(np.abs(sig)), np.zeros(int(0.15 * sr))]).astype(np.float32)


def lid(n: int) -> str:
    return f"fmod:Audio/Dialogue_en.bank#fsb0:{n:04d}"


CLASSES = {1: "speech", 2: "interjection", 3: "nonverbal", 4: "short_word", 5: "mixed"}
TEXTS = {1: "Это реплика номер один, малой.", 2: "Хе-хе.", 3: "Ааа!", 4: "Стоять!", 5: "Ха! Ну ты даёшь."}


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    home = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    root = home / "projects" / "cls"
    proj = Project.create(root, {"slug": "cls", "game_name": "Cls", "game_dir": str(tmp_path / "game"),
                                 "target_lang": "ru", "source_lang": "en",
                                 "tts": {"engine": "dummy", "ref_strategy": "line", "best_of": 1},
                                 "post": {"tolerance": 100.0}})
    sr = 24000
    lines, translations, analysis = [], [], []
    for n, cls in CLASSES.items():
        u = make_uid(lid(n))
        secs = 0.8 if cls == "short_word" else 3.0
        wav = write_wav(root / layout.ORIG_AUDIO_DIR / f"{u}.wav", speech(secs, sr, 120.0 + n), sr)
        lines.append({"id": lid(n), "uid": u, "kind": "dialogue", "locale": "en-US",
                      "audio": {"sr": sr, "ch": 1, "dur_s": round(secs + 0.25, 4), "wav": proj.rel(wav)},
                      "speaker_hint": "Zbyszek", "scene": "s1", "order": n, "fixed_length": False, "flags": [],
                      "text": {"src": f"Original line {n}.", "src_lang": "en"}})
        translations.append({"id": lid(n), "dub_text": TEXTS[n], "lang": "ru", "by": "agent", "rev": 1})
        analysis.append({"id": lid(n), "uid": u, "class": cls, "speech_s": secs})
    write_jsonl(root / layout.LINES, lines, schema="babeldub.lines/1")
    write_jsonl(root / layout.TRANSLATIONS, translations, schema="babeldub.translations/1")
    write_jsonl(root / layout.ANALYSIS, analysis, schema=ANALYSIS_SCHEMA)
    return proj


def results(project: Project) -> dict[str, dict[str, Any]]:
    return latest_by(read_jsonl(project.path(layout.SYNTH_RESULTS)))


def test_keep_original_classes_are_not_synthesized(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK, out
    assert out["skipped"]["keep_original"] == 2 and out["done"] == 3
    assert set(results(project)) == {lid(1), lid(4), lid(5)}
    decisions = read_jsonl(project.path(layout.DECISIONS))
    assert {(d["id"], d["decision"], d["class"], d["by"]) for d in decisions} == {
        (lid(2), "keep_original", "interjection", "auto"), (lid(3), "keep_original", "nonverbal", "auto")}
    run(capsys, project.root, "synth")
    assert len(read_jsonl(project.path(layout.DECISIONS))) == 2  # written once

    # a human asks for a dub of the interjection: it is synthesized
    append_jsonl(project.path(layout.DECISIONS), [{"id": lid(2), "decision": "regen", "by": "human", "attempt": None}])
    code, out = run(capsys, project.root, "synth")
    assert out["done"] == 1 and out["skipped"]["keep_original"] == 1 and lid(2) in results(project)


def test_keep_original_classes_setting(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    project.data["tts"]["keep_original_classes"] = []
    project.save()
    code, out = run(capsys, project.root, "synth", "--dry-run")
    assert out["would_run"] == 5 and "keep_original" not in out["skipped"]


def test_short_word_uses_the_line_and_more_candidates(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, project.root, "synth", "--ids", lid(4))
    assert code == EXIT_OK, out
    row = results(project)[lid(4)]
    assert row["ref_mode"].startswith("line") and row["ref_id"] == f"line:{make_uid(lid(4))}"
    assert "short_word" in row["ref_reason"] and "prompt_id" not in row
    assert row["best_of"]["n"] == voice.SHORT_WORD_BEST_OF
    # one regen at most: the second attempt is the last
    for attempt in (2, 3):
        append_jsonl(project.path(layout.SYNTH_RESULTS), [{**row, "attempt": attempt, "status": "error", "wav": None}])
    code, out = run(capsys, project.root, "synth", "--ids", lid(4), "--force", "--dry-run")
    assert out["would_run"] == 1  # --force ignores the limit
    project.data["tts"]["max_attempts"] = 5
    project.save()
    code, out = run(capsys, project.root, "synth", "--ids", lid(4))
    assert out["done"] == 0 and out["skipped"].get("done", 0) == 1  # its text is done


def test_short_word_bootstrap_has_no_target_prompt(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    project.data["tts"].update(ref_strategy="bootstrap", best_of=2)
    project.save()
    code, out = run(capsys, project.root, "synth", "--ids", lid(4))
    assert code == EXIT_OK and out["by_ref"] == {"line-xvec": 1}, out  # the dummy is a transcript engine
    assert "prompt_id" not in results(project)[lid(4)]


def test_glossary_pronounce_and_stress_modes(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    write_jsonl(project.path(layout.GLOSSARY), [{"term": "Zbyszek", "translation": "Збышек", "pronounce": "Збы́шек"}],
                schema="babeldub.glossary/1")
    append_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": lid(1), "dub_text": "Збышек, позови Збышека.", "lang": "ru", "by": "agent", "rev": 2}])
    run(capsys, project.root, "synth", "--ids", lid(1))
    assert results(project)[lid(1)]["text"] == "Збышек, позови Збышека."  # tts.stress=off: plain text
    project.data["tts"]["stress"] = "on"
    project.save()
    code, out = run(capsys, project.root, "synth", "--ids", lid(1))
    assert results(project)[lid(1)]["text"] == "ЗбЫшек, позови ЗбЫшека."  # dummy reads a CAPITAL vowel
    assert any("ruaccent" in n for n in out["notes"])  # no qc env: glossary marks only


class FakeAccentSession:
    calls: list[list[str]] = []

    def __enter__(self) -> FakeAccentSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def accent(self, texts: list[str]) -> list[str]:
        FakeAccentSession.calls.append(list(texts))
        return [t.replace("замок", "зам+ок") for t in texts]


def test_stress_on_marks_homographs_through_the_qc_worker(project: Project, capsys: pytest.CaptureFixture[str],
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    import babeldub.qa.qc as qc

    FakeAccentSession.calls = []
    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: [])
    monkeypatch.setattr(qc.QcSession, "open", classmethod(lambda cls, *a, **k: FakeAccentSession()))
    append_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": lid(1), "dub_text": "Открой замок.", "lang": "ru", "by": "agent", "rev": 2}])
    project.data["tts"]["stress"] = "on"
    project.save()
    run(capsys, project.root, "synth", "--ids", lid(1))
    assert results(project)[lid(1)]["text"] == "Открой замОк."
    assert FakeAccentSession.calls == [["Открой замок."]]
    assert read_jsonl(project.path(voice.ACCENT_CACHE))[0]["marked"] == "Открой зам+ок."
    code, out = run(capsys, project.root, "synth", "--ids", lid(1))
    assert out["skipped"]["done"] == 1 and len(FakeAccentSession.calls) == 1  # cached: the line stays done


def test_stress_mode_resolution(project: Project) -> None:
    from babeldub.envsetup.catalog import load_catalog

    catalog = load_catalog()
    vox = voice.EngineChoice("voxcpm", Path("x"), "w", ["ru"], "VoxCPM2", "VoxCPM2", "local")
    stress_model = voice.EngineChoice("qwen3tts", Path("x"), "w", ["ru"], "m", "qwen3-tts-12hz-1.7b-ru-stress-cf", "local")
    assert voice.stress_mode(project, vox, catalog) == "none"
    assert voice.stress_mode(project, stress_model, catalog) == "acute"
    project.data["tts"]["stress"] = "on"
    assert voice.stress_mode(project, vox, catalog) == "upper"
