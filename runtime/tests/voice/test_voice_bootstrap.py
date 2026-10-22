"""Default dubbing chain on the dummy engine: bootstrap prompts, best of N, plain text, engine auto."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from babeldub.audio.fit import speech_span
from babeldub.audio.io import read_audio, write_wav
from babeldub.commands import project as project_cmd
from babeldub.commands import voice
from babeldub.core import layout
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import append_jsonl, latest_by, read_json, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_OK
from babeldub.core.paths import env_python
from babeldub.core.project import Project

N_LINES = 7


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


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    home = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    root = home / "projects" / "boot"
    proj = Project.create(root, {"slug": "boot", "game_name": "Boot", "game_dir": str(tmp_path / "game"),
                                 "target_lang": "ru", "source_lang": "en",
                                 "tts": {"engine": "dummy", "ref_strategy": "bootstrap", "best_of": 2},
                                 "post": {"tolerance": 100.0}})
    sr = 48000
    lines, translations = [], []
    for n in range(1, N_LINES + 1):
        lid = line_id(n)
        u = make_uid(lid)
        secs = 5.0 if n <= 5 else 3.0
        wav = write_wav(root / layout.ORIG_AUDIO_DIR / f"{u}.wav", speech(secs, sr, 110.0 + n), sr)
        lines.append({
            "id": lid, "uid": u, "adapter": "fmod", "asset_id": "fmod:Audio/Dialogue_en.bank",
            "entry": {"key": f"dlg_{n}", "sub": "fsb0", "index": n}, "kind": "dialogue", "locale": "en-US",
            "audio": {"sr": sr, "ch": 1, "dur_s": round(secs + 0.25, 4), "wav": proj.rel(wav)},
            "speaker_hint": "Zbyszek", "scene": "s1", "order": n, "fixed_length": False, "flags": [],
            "text": {"key": f"dlg_{n}", "src": f"This is the original line number {n}.", "src_lang": "en"},
        })
        translations.append({"id": lid, "dub_text": f"Это реплика номер {n}, малой.", "lang": "ru", "by": "agent", "rev": 1})
    write_jsonl(root / layout.LINES, lines, schema="babeldub.lines/1")
    write_jsonl(root / layout.TRANSLATIONS, translations, schema="babeldub.translations/1")
    return proj


def results(project: Project) -> dict[str, dict[str, Any]]:
    return latest_by(read_jsonl(project.path(layout.SYNTH_RESULTS)))


def judge(project: Project, verdicts: dict[str, tuple[str, float]]) -> None:
    """What `qa` would write: a current qa row and a decision per line (id -> (decision, cer))."""
    post = latest_by(read_jsonl(project.path(layout.POST)))
    qa_rows, decisions = [], []
    for lid, (decision, cer) in verdicts.items():
        row = post[lid]
        qa_rows.append({"id": lid, "attempt": row["attempt"], "wav": row["wav"], "cer": cer,
                        "verdict": "pass" if decision == "accept" else "fail"})
        decisions.append({"id": lid, "decision": decision, "attempt": row["attempt"], "by": "auto"})
    append_jsonl(project.path(layout.QA), qa_rows)
    append_jsonl(project.path(layout.DECISIONS), decisions)
    project.mark_stage("qa", "done")


def test_bootstrap_phases(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    # phase 1: the first 4 plain lines with the long original reference only, the rest waits
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK, out
    assert out["done"] == 4 and out["by_ref"] == {"bootstrap-src": 4} and out["skipped"]["bootstrap_wait"] == 3
    assert "wait" in out["next"] and "babeldub qa" in out["next"]
    assert project.progress()["synth"]["bootstrap_wait"] == 3
    rows = results(project)
    first = [line_id(n) for n in range(1, 5)]
    assert sorted(rows) == first
    long_ref = rows[first[0]]["ref_wav"]
    assert long_ref.startswith(layout.SYNTH_PROMPTS_DIR) and rows[first[0]]["ref_id"].startswith("long:Zbyszek:")
    data, sr = read_audio(project.path(long_ref))
    assert 15.0 <= data.shape[0] / sr <= 20.0  # 3-4 clean originals with 0.35 s gaps
    assert all("prompt_id" not in r for r in rows.values())

    assert run(capsys, project.root, "post")[0] == EXIT_OK
    judge(project, {first[0]: ("accept", 0.0), first[1]: ("accept", 0.05), first[2]: ("accept", 0.02),
                    first[3]: ("regen", 0.4)})
    for stage in layout.STAGES[: layout.STAGES.index("qa")]:
        if stage != "synth":  # keeps its bootstrap_wait
            project.mark_stage(stage, "done")
    code, out = run(capsys, project.root, "status")
    assert out["next_stage"] == "synth" and out["bootstrap_wait"] == 3 and "phase 2" in out["next"]

    # phase 2: a frozen target-language prompt from the accepted lines (lowest CER first)
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK and out["done"] == 3 and out["by_ref"] == {"bootstrap-tgt": 3}, out
    bank = read_json(project.path(layout.SYNTH_PROMPTS_DIR, "Zbyszek", "tgt.json"))["bank"]
    assert list(bank) == ["calm"]  # no analysis: every line is calm
    meta = bank["calm"]
    assert meta["lines"] == [first[0]]  # lowest CER first; a ~5 s piece is a whole 5-10 s prompt
    texts = {lid: r["text"] for lid, r in results(project).items()}
    assert meta["text"] == " ".join(texts[lid] for lid in meta["lines"])
    for n in range(5, N_LINES + 1):
        row = results(project)[line_id(n)]
        assert row["ref_mode"] == "bootstrap-tgt" and row["prompt_id"] == meta["ref_id"] and row["bucket"] == "calm"
        assert row["ref_wav"] == long_ref  # the timbre still comes from the original actor
    assert project.progress()["synth"]["bootstrap_wait"] == 0
    # the regenerated phase-1 line now gets the prompt too
    code, out = run(capsys, project.root, "synth", "--attempt-next")
    assert out["done"] == 1 and out["by_ref"] == {"bootstrap-tgt": 1}
    assert results(project)[first[3]]["attempt"] == 2


def test_bootstrap_without_enough_accepted_lines_goes_on_with_the_reference(project: Project,
                                                                           capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, project.root, "synth")
    run(capsys, project.root, "post")
    first = [line_id(n) for n in range(1, 5)]
    judge(project, {first[0]: ("accept", 0.0), first[1]: ("accept", 0.0), first[2]: ("keep_original", 0.6),
                    first[3]: ("keep_original", 0.7)})
    code, out = run(capsys, project.root, "synth")
    assert out["done"] == 3 and out["by_ref"] == {"bootstrap-src": 3}, out
    row = results(project)[line_id(6)]
    assert "bootstrap_min_accepted" in row["ref_reason"]
    assert not project.path(layout.SYNTH_PROMPTS_DIR, "Zbyszek", "tgt.json").exists()


def test_best_of_keeps_the_take_closest_to_the_original_length(project: Project,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    project.data["tts"].update(ref_strategy="line", best_of=3)
    project.save()
    code, out = run(capsys, project.root, "synth", "--ids", line_id(1))
    assert code == EXIT_OK and out["best_of"] == 3, out
    row = results(project)[line_id(1)]
    best = row["best_of"]
    assert best["n"] == 3 and best["usable"] == 3 and best["picked_seed"] == row["seed"]
    target = best["target_speech_s"]
    orig, sr = read_audio(project.path(layout.ORIG_AUDIO_DIR, f"{make_uid(line_id(1))}.wav"))
    assert target == pytest.approx(speech_span(orig, sr), abs=1e-3)
    take, t_sr = read_audio(project.path(row["wav"]))
    picked = speech_span(take, t_sr)
    assert min(abs(math.log(s / target)) for s in best["speech_s"]) == pytest.approx(abs(math.log(picked / target)), abs=1e-3)
    assert sorted(p.name for p in project.path(layout.SYNTH_DIR, make_uid(line_id(1))).iterdir()) == ["a1.wav"]


def test_stress_marks_are_removed_for_plain_models(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    append_jsonl(project.path(layout.TRANSLATIONS), [
        {"id": line_id(1), "dub_text": "Ну что, мало́й, пое́хали.", "lang": "ru", "by": "human", "rev": 2}])
    project.data["tts"].update(ref_strategy="line", best_of=1)
    project.save()
    assert run(capsys, project.root, "synth", "--ids", line_id(1))[0] == EXIT_OK
    assert results(project)[line_id(1)]["text"] == "Ну что, малой, поехали."
    assert voice.keeps_stress("qwen3-tts-12hz-1.7b-ru-stress-cf") and not voice.keeps_stress("VoxCPM2")


def test_engine_auto_prefers_voxcpm_on_the_gpu(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    project.data["tts"].update(engine="auto", ref_strategy="auto")
    project.save()
    code, out = run(capsys, project.root, "synth", "--dry-run", "--device", "cuda")
    assert out["engine"] == "qwen3tts-fast" and "voxcpm env is not installed" in out["notes"][0]
    assert out["ref_strategy"] == "auto"
    python = env_python("voxcpm")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    code, out = run(capsys, project.root, "synth", "--dry-run", "--device", "cuda")
    assert out["engine"] == "voxcpm" and out["model"] == "VoxCPM2" and out["notes"] == ["tts.engine=auto -> voxcpm"]
    assert out["ref_strategy"] == "bootstrap" and out["by_ref"] == {"bootstrap-src": 4} and out["best_of"] == 2
    code, out = run(capsys, project.root, "synth", "--dry-run", "--device", "cpu")
    assert out["engine"] == "qwen3tts" and "needs an NVIDIA GPU" in out["notes"][0] and "CUDA only" in out["notes"][1]
    code, out = run(capsys, project.root, "synth", "--dry-run", "--device", "cpu", "--engine", "voxcpm")
    assert out["engine"] == "qwen3tts" and "voxcpm runs on CUDA only" in out["notes"][0]
    project.data["tts"]["ref_strategy"] = "bootstrap"
    project.save()
    code, out = run(capsys, project.root, "synth", "--dry-run", "--device", "cpu")
    assert out["ref_strategy"] == "auto" and any("needs the voxcpm engine" in n for n in out["notes"])
