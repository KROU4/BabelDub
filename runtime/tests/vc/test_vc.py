"""`babeldub vc train` / `babeldub vc` with a fake Applio checkout (core.py run by this Python)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from babeldub.audio.io import probe, write_wav
from babeldub.commands import vc as vc_cmd
from babeldub.commands import voice
from babeldub.core import layout
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import latest_by, read_json, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_OK, EXIT_USAGE, BabelDubError, emit_error
from babeldub.core.project import Project
from babeldub.pack import planner
from babeldub.vc import rvc

FAKE_CORE = '''
"""Fake Applio core.py: the commands BabelDub calls, with the files they leave behind."""
import os
import sys

import numpy as np
import soundfile as sf

args = sys.argv[1:]
cmd = args[0]
opt = {args[i]: args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else True
       for i in range(1, len(args)) if args[i].startswith("--")}
with open("calls.log", "a", encoding="utf-8") as fh:
    fh.write(" ".join(args) + "\\n")
if cmd in ("preprocess", "extract", "train", "index"):
    logs = os.path.join("logs", opt["--model-name"])
    os.makedirs(logs, exist_ok=True)
    if cmd == "preprocess":
        assert any(n.endswith(".wav") for n in os.listdir(opt["--dataset-path"]))
    if cmd == "train":
        epochs = int(opt["--total-epoch"])
        open(os.path.join(logs, f"{opt['--model-name']}_{epochs}e_{epochs * 7}s.pth"), "wb").write(b"G" * 100)
    if cmd == "index":
        open(os.path.join(logs, f"{opt['--model-name']}.index"), "wb").write(b"I" * 10)
elif cmd == "batch-infer":
    assert os.path.isdir(opt["--output-folder"]) and os.path.exists(opt["--pth-path"])
    for name in os.listdir(opt["--input-folder"]):
        data, sr = sf.read(os.path.join(opt["--input-folder"], name), dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        n = int(len(mono) * 40000 / sr) + 37  # 40 kHz, a few samples of slack like a real converter
        out = 0.5 * np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono)
        sf.write(os.path.join(opt["--output-folder"], os.path.splitext(name)[0] + "_output.wav"), out, 40000)
else:
    sys.exit(f"unknown command {cmd}")
'''


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--project")
        sp.add_argument("--pretty", action="store_true")

    voice.register(sub, common)
    vc_cmd.register(sub, common)
    return p


def run(capsys: pytest.CaptureFixture[str], project: Path, *argv: str) -> tuple[int, dict[str, Any]]:
    args = parser().parse_args([*argv, "--project", str(project)])
    try:
        code = args.func(args)
    except BabelDubError as err:
        code = emit_error(err)
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1, out
    return code, json.loads(out[0])


def speech(seconds: float, sr: int, f0: float) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    sig = np.sin(2 * np.pi * f0 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    return np.concatenate([np.zeros(int(0.1 * sr)), 0.3 * sig, np.zeros(int(0.2 * sr))]).astype(np.float32)


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    home = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    root = home / "projects" / "vc-game"
    proj = Project.create(root, {"slug": "vc-game", "game_name": "VC", "game_dir": str(tmp_path / "game"),
                                 "target_lang": "ru", "source_lang": "en",
                                 "tts": {"engine": "dummy", "ref_strategy": "line", "best_of": 1,
                                         "params": {"syllables_per_s": 14.0}},
                                 "post": {"tolerance": 100.0}})
    lines, translations = [], []
    for n, (char, secs) in enumerate([("Zbyszek", 4.0), ("Zbyszek", 3.0), ("Zbyszek", 5.0), ("Zbyszek", 1.0),
                                      ("Maciek", 3.0), ("Maciek", 2.5)], 1):
        lid = f"fmod:Audio/Dialogue_en.bank#fsb0:{n:04d}"
        u = make_uid(lid)
        wav = write_wav(root / layout.ORIG_AUDIO_DIR / f"{u}.wav", speech(secs, 44100, 100.0 + 10 * n), 44100)
        lines.append({"id": lid, "uid": u, "kind": "dialogue", "asset_id": "fmod:Audio/Dialogue_en.bank",
                      "entry": {"key": f"k{n}"}, "audio": {"sr": 44100, "ch": 1, "dur_s": secs + 0.3, "wav": proj.rel(wav)},
                      "speaker_hint": char, "scene": "s", "order": n, "flags": [],
                      "text": {"key": f"k{n}", "src": f"Original line {n} of {char}.", "src_lang": "en"}})
        translations.append({"id": lid, "dub_text": f"Реплика {n}, персонаж {char}.", "lang": "ru", "by": "agent"})
    write_jsonl(root / layout.LINES, lines, schema="babeldub.lines/1")
    write_jsonl(root / layout.TRANSLATIONS, translations, schema="babeldub.translations/1")
    proj.mark_stage("extract", "done")
    return proj


@pytest.fixture()
def applio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "applio"
    folder.mkdir()
    (folder / "core.py").write_text(FAKE_CORE, encoding="utf-8")
    monkeypatch.setattr(rvc, "require_applio", lambda: (Path(sys.executable), folder))
    monkeypatch.setattr(vc_cmd, "resolve_device", lambda setting: "cuda")
    return folder


def test_train_convert_and_idempotency(project: Project, applio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project.root
    code, out = run(capsys, root, "vc", "train", "--chars", "Zbyszek", "--epochs", "2", "--minutes", "1", "--dry-run")
    assert code == EXIT_OK and out["plan"][0]["files"] == 3 and out["plan"][0]["action"] == "train", out  # 1 s line < 2 s
    code, out = run(capsys, root, "vc", "train", "--chars", "zbyszek", "--epochs", "2", "--minutes", "1")
    assert code == EXIT_OK and [t["char_id"] for t in out["trained"]] == ["Zbyszek"], out
    meta = read_json(root / layout.VC_DIR / "Zbyszek" / layout.VC_MODEL_JSON)
    assert meta["epochs"] == 2 and meta["files"] == 3 and meta["source_pth"].endswith("_2e_14s.pth")
    assert (root / layout.VC_DIR / "Zbyszek" / "model.pth").read_bytes() == b"G" * 100
    assert len(list((root / layout.VC_DIR / "Zbyszek" / "dataset").glob("*.wav"))) == 3
    calls = (applio / "calls.log").read_text(encoding="utf-8").splitlines()
    assert [c.split()[0] for c in calls] == ["preprocess", "extract", "train", "index"]
    assert not (applio / "logs" / meta["applio_name"]).exists()  # training leftovers are removed
    assert "--sample-rate 40000" in calls[0] and "--f0-method rmvpe" in calls[1] and "--total-epoch 2" in calls[2]
    code, out = run(capsys, root, "vc", "train", "--chars", "Zbyszek")
    assert out["skipped"] == ["Zbyszek"] and out["trained"] == []
    assert run(capsys, root, "vc", "train", "--chars", "Nobody")[0] == EXIT_USAGE

    code, out = run(capsys, root, "synth")
    assert code == EXIT_OK, out
    code, out = run(capsys, root, "post")
    assert code == EXIT_OK, out
    code, out = run(capsys, root, "vc", "--dry-run")
    assert out["would_convert"] == {"Zbyszek": 4} and out["passthrough"] == {"Maciek": 2}, out
    code, out = run(capsys, root, "vc")
    assert code == EXIT_OK and out["converted"] == 4 and out["passthrough"] == {"Maciek": 2}, out
    assert "vc train" in out["notes"][0] and out["next"] == "babeldub qa"
    assert "--proposed-pitch" in (applio / "calls.log").read_text(encoding="utf-8")
    lines = latest_by(read_jsonl(root / layout.LINES))
    post = latest_by(read_jsonl(root / layout.POST))
    for lid, row in post.items():
        orig = probe(root / lines[lid]["audio"]["wav"])
        if lines[lid]["speaker_hint"] == "Maciek":
            assert "vc" not in row and row["wav"].startswith(layout.POST_DIR)
            continue
        assert row["stage"] == "vc" and row["wav"].startswith(layout.VC_AUDIO_DIR) and row["vc"]["char_id"] == "Zbyszek"
        assert row["pre_vc_wav"].startswith(layout.POST_DIR) and row["vc"]["index_rate"] == 0.5
        info = probe(root / row["wav"])
        assert (info["sr"], info["frames"]) == (44100, orig["frames"])  # exact length after the 40 kHz conversion
        uid = lines[lid]["uid"]
        assert planner._post_wav(project, uid, row) == root / row["wav"]  # the build packs the converted take
    assert project.progress()["vc"]["status"] == "done"

    code, out = run(capsys, root, "vc")
    assert out["converted"] == 0 and out["up_to_date"] == 4
    code, out = run(capsys, root, "post")
    assert out["done"] == 0 and out["up_to_date"] == 6  # post does not overwrite the converted takes
    code, out = run(capsys, root, "vc", "--index-rate", "0.7")
    assert out["converted"] == 4  # other settings: converted again from the pre-conversion take
    rows = latest_by(read_jsonl(root / layout.POST))
    assert all(r["pre_vc_wav"].startswith(layout.POST_DIR) for r in rows.values() if r.get("vc"))


def test_train_needs_a_gpu(project: Project, applio: Path, monkeypatch: pytest.MonkeyPatch,
                           capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(vc_cmd, "resolve_device", lambda setting: "cpu")
    code, out = run(capsys, project.root, "vc", "train", "--chars", "Zbyszek")
    assert code == EXIT_USAGE and "GPU" in out["error"]


def test_missing_applio_is_exit_env(project: Project, capsys: pytest.CaptureFixture[str],
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vc_cmd, "resolve_device", lambda setting: "cuda")
    code, out = run(capsys, project.root, "vc", "train", "--chars", "Zbyszek")
    assert code == 4 and out["hint"].startswith("babeldub setup --engines applio")


def test_rvc_helpers(tmp_path: Path) -> None:
    name = rvc.model_name("low-budget-repairs", "збышек")
    assert name.startswith("bd_lowbudgetrepai") and name.isascii()
    assert rvc.model_name("a", "b") != rvc.model_name("a", "c")
    logs = tmp_path / name
    logs.mkdir()
    for f in (f"{name}_10e_70s.pth", f"{name}_20e_140s.pth", "G_140.pth", f"{name}.index"):
        (logs / f).write_bytes(b"x")
    pth, index = rvc.trained_files(logs, name)
    assert pth is not None and pth.name.endswith("_20e_140s.pth") and index is not None


def test_save_every_respects_applio_limit_and_saves_final_epoch():
    # Applio rejects --save-every-epoch > 100; the final epoch must still be a save point
    assert rvc.save_every(20) == 20
    assert rvc.save_every(150) == 75
    assert rvc.save_every(300) == 100
    assert rvc.save_every(101) == 1
    for e in (1, 20, 99, 100, 150, 200, 300):
        s = rvc.save_every(e)
        assert 1 <= s <= 100 and e % s == 0


def test_auto_epochs_scales_for_small_datasets():
    assert rvc.auto_epochs(30.0) == 20
    assert rvc.auto_epochs(1.5) == 133
    assert rvc.auto_epochs(0.6) == 300
    assert rvc.auto_epochs(0.0) == 20
