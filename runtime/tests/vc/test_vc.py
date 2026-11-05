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
from babeldub.audio.pitch import semitones
from babeldub.commands import vc as vc_cmd
from babeldub.commands import voice
from babeldub.core import layout
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import append_jsonl, latest_by, read_json, read_jsonl, write_jsonl
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
    assert code == EXIT_OK and out["match"]["mode"] == "eq" and out["match"]["eq"] == 6, out  # new projects: eq
    fitted = latest_by(read_jsonl(root / layout.POST))
    code, out = run(capsys, root, "vc", "--dry-run")
    assert out["would_convert"] == {"Zbyszek": 4} and out["passthrough"] == {"Maciek": 2}, out
    code, out = run(capsys, root, "vc")
    assert code == EXIT_OK and out["converted"] == 4 and out["passthrough"] == {"Maciek": 2}, out
    assert "vc train" in out["notes"][0] and out["next"] == "babeldub qa"
    assert "--proposed-pitch" not in (applio / "calls.log").read_text(encoding="utf-8")  # keeps the dub's F0
    lines = latest_by(read_jsonl(root / layout.LINES))
    post = latest_by(read_jsonl(root / layout.POST))
    for lid, row in post.items():
        orig = probe(root / lines[lid]["audio"]["wav"])
        if lines[lid]["speaker_hint"] == "Maciek":
            assert "vc" not in row and row["wav"].startswith(layout.POST_DIR)
            continue
        assert row["stage"] == "vc" and row["wav"].startswith(layout.VC_AUDIO_DIR) and row["vc"]["char_id"] == "Zbyszek"
        # RVC converts the fitted take before post's channel match, then the channel is matched again
        assert row["pre_vc_wav"] == fitted[lid]["pre_match_wav"] and row["pre_vc_wav"].startswith(layout.POST_PRE_MATCH_DIR)
        assert row["match"]["applied"] and row["pre_match_wav"].startswith(layout.VC_PRE_MATCH_DIR)
        assert row["vc"]["index_rate"] == 0.75
        assert probe(root / row["pre_match_wav"])["frames"] == orig["frames"]
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
    assert all(r["pre_vc_wav"].startswith(layout.POST_PRE_MATCH_DIR) for r in rows.values() if r.get("vc"))

    # a take converted with an older key (e.g. with --proposed-pitch) is converted again from its pre-vc take
    stale = [{**r, "vc": {**r["vc"], "model_key": "0123456789ab"}} for r in rows.values() if r.get("vc")]
    append_jsonl(root / layout.POST, stale)
    code, out = run(capsys, root, "vc", "--index-rate", "0.7")
    assert out["converted"] == 4 and out["up_to_date"] == 0, out

    # another post.match: post fits again, vc converts again, without a channel match now
    project.data["post"]["match"] = "off"
    project.save()
    code, out = run(capsys, root, "post")
    assert out["done"] == 6 and "match" not in out, out
    code, out = run(capsys, root, "vc", "--index-rate", "0.7")
    assert out["converted"] == 4, out
    rows = latest_by(read_jsonl(root / layout.POST))
    assert all("match" not in r and "pre_match_wav" not in r for r in rows.values())
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


def test_convert_for_scoring(project: Project, applio: Path, tmp_path: Path) -> None:
    folder = project.path(layout.VC_DIR, "Zbyszek")
    folder.mkdir(parents=True)
    (folder / "model.pth").write_bytes(b"G")
    (folder / "model.index").write_bytes(b"I")
    (folder / layout.VC_MODEL_JSON).write_text(json.dumps({"char_id": "Zbyszek", "trained": "t"}), encoding="utf-8")
    wavs = [write_wav(tmp_path / f"c{k}.wav", speech(1.0, 24000, 150.0), 24000) for k in range(2)]
    lines = read_jsonl(project.path(layout.LINES))
    jobs = {"Zbyszek": [(f"u_s{k}", w, lines[0]) for k, w in enumerate(wavs)], "Maciek": [("m_s0", wavs[0], lines[4])]}
    project.data["vc"]["pitch"] = "keep"
    out, errors = vc_cmd.convert_for_scoring(project, jobs)
    assert errors == [] and sorted(out) == ["u_s0", "u_s1"]  # Maciek has no model
    assert all(p.exists() and probe(p)["sr"] == 40000 for p in out.values())
    calls = [c for c in (applio / "calls.log").read_text(encoding="utf-8").splitlines() if c.startswith("batch-infer")]
    assert len(calls) == 1 and "--pitch 0" in calls[0]  # vc.pitch keep
    vc_cmd.drop_scoring_files(project, {"Zbyszek"})
    assert not (folder / vc_cmd.SCORE_WORK).exists() and (folder / "model.pth").exists()
    assert vc_cmd.convert_for_scoring(project, {"Maciek": [("m_s0", wavs[0], lines[4])]}) == ({}, [])
    # vc.pitch=line: each candidate is shifted toward its own original line, as `vc` will shift the winner
    project.data["vc"]["pitch"] = "line"
    project.save()
    (applio / "calls.log").unlink()
    jobs = {"Zbyszek": [("u_s0", wavs[0], lines[0]), ("u_s1", wavs[1], lines[2])]}  # originals 110 / 130 Hz
    out, errors = vc_cmd.convert_for_scoring(project, jobs)
    assert errors == [] and sorted(out) == ["u_s0", "u_s1"]
    calls = (applio / "calls.log").read_text(encoding="utf-8").splitlines()
    pitches = sorted(int(c.split("--pitch ")[1].split()[0]) for c in calls)
    assert len(pitches) == 2 and pitches[0] < pitches[1] < 0  # 150 Hz takes go down, more for the 110 Hz line
    vc_cmd.drop_scoring_files(project, {"Zbyszek"})


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
    for e in (10, 20, 50, 99, 100, 130, 150, 200, 300):
        s = rvc.save_every(e)
        assert 10 <= s <= 100 and e % s == 0
    # a prime count has no divisor >= 10: never a checkpoint every epoch
    assert rvc.save_every(101) == 100 and rvc.save_every(127) == 100 and rvc.save_every(7) == 7


def test_auto_epochs_scales_for_small_datasets():
    assert rvc.auto_epochs(30.0) == 20
    assert rvc.auto_epochs(1.5) == 130  # 133 rounded to a multiple of 10
    assert rvc.auto_epochs(0.6) == 300
    assert rvc.auto_epochs(0.0) == 20
    assert rvc.auto_epochs(25.0) == 20  # below the base: the base
    for minutes in (0.5, 0.79, 1.0, 1.57, 2.2, 3.3, 7.0, 9.0):
        e = rvc.auto_epochs(minutes)
        assert e % 10 == 0 and 20 <= e <= 300 and rvc.save_every(e) >= 10


def test_train_command_keeps_only_the_latest_checkpoint(tmp_path: Path) -> None:
    train = rvc.train_commands(tmp_path / "python.exe", "bd_x", tmp_path, 127, "0")[2]
    assert "--save-only-latest" in train
    assert train[train.index("--save-every-epoch") + 1] == "100"


def test_pitch_modes_shift_to_the_originals(project: Project, applio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project.root
    assert run(capsys, root, "vc", "train", "--chars", "Zbyszek", "--epochs", "2", "--minutes", "1")[0] == EXIT_OK
    assert run(capsys, root, "synth")[0] == EXIT_OK
    assert run(capsys, root, "post")[0] == EXIT_OK
    lines = latest_by(read_jsonl(root / layout.LINES))
    post = latest_by(read_jsonl(root / layout.POST))
    zb = [lid for lid, ln in lines.items() if ln["speaker_hint"] == "Zbyszek"]
    expected = {}
    for lid in zb:
        orig = vc_cmd._median_hz(root / lines[lid]["audio"]["wav"])
        dub = vc_cmd._median_hz(root / post[lid]["wav"])
        expected[lid] = vc_cmd._clip_shift(semitones(orig, dub))
    code, out = run(capsys, root, "vc", "--pitch", "line")
    assert code == EXIT_OK and out["converted"] == 4, out
    rows = latest_by(read_jsonl(root / layout.POST))
    assert {lid: rows[lid]["vc"]["pitch_shift_st"] for lid in zb} == expected
    assert all(rows[lid]["vc"]["pitch"] == "line" for lid in zb)
    calls = [c for c in (applio / "calls.log").read_text(encoding="utf-8").splitlines() if c.startswith("batch-infer")]
    assert {int(c.split("--pitch ")[1].split()[0]) for c in calls} == set(expected.values())
    assert len(calls) == len(set(expected.values()))  # one Applio run per distinct shift

    code, out = run(capsys, root, "vc", "--pitch", "char")
    assert code == EXIT_OK and out["converted"] == 4, out  # another mode: another conversion key
    shifts = {latest_by(read_jsonl(root / layout.POST))[lid]["vc"]["pitch_shift_st"] for lid in zb}
    assert len(shifts) == 1
    assert run(capsys, root, "vc", "--pitch", "char")[1]["up_to_date"] == 4


def rich_originals(project: Project, speaker: str) -> None:
    """Originals with highs (harmonics + noise), so no original shows a band limit against the converted takes."""
    for k, line in enumerate(read_jsonl(project.path(layout.LINES))):
        if line.get("speaker_hint") != speaker:
            continue
        secs = line["audio"]["dur_s"] - 0.3
        t = np.arange(int(secs * 44100)) / 44100
        rng = np.random.default_rng(k)
        sig = sum(np.sin(2 * np.pi * m * 120.0 * t) / m for m in range(1, 60)) + 0.5 * rng.standard_normal(t.size)
        sig = 0.3 * sig * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)) / np.max(np.abs(sig))
        audio = np.concatenate([np.zeros(4410), sig, np.zeros(8820)]).astype(np.float32)
        write_wav(project.path(line["audio"]["wav"]), audio, 44100)


def test_character_pair_uses_the_converted_takes_and_the_target_is_part_of_up_to_date(
        project: Project, applio: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = project.root
    rich_originals(project, "Zbyszek")
    monkeypatch.setattr(voice, "MATCH_PAIR_MIN_S", 1.0)  # the dummy's takes are short
    seen: list[dict[str, Any]] = []
    build = voice.ChannelPool._build_pair

    def spy(self: voice.ChannelPool, cid: str) -> Any:
        seen.append({"cid": cid, "dubs": {lid: p for lid, p in self.dubs.items() if self.char_of.get(lid) == cid}})
        return build(self, cid)

    monkeypatch.setattr(voice.ChannelPool, "_build_pair", spy)
    assert run(capsys, root, "vc", "train", "--chars", "Zbyszek", "--epochs", "2", "--minutes", "1")[0] == EXIT_OK
    assert run(capsys, root, "synth")[0] == EXIT_OK
    code, out = run(capsys, root, "post")
    assert code == EXIT_OK and out["match"]["target"] == "character" and out["match"]["pair"] == 4, out
    seen.clear()
    code, out = run(capsys, root, "vc")
    assert code == EXIT_OK and out["converted"] == 4 and out["match"]["pair"] == 4, out
    assert out["match"]["target"] == "character"
    # one pair for the character, measured on its converted takes before the match (not the synth takes),
    # after every shift group was converted
    zb = [s for s in seen if s["cid"] == "Zbyszek"]
    assert len(zb) == 1 and len(zb[0]["dubs"]) == 4
    staged = root / layout.VC_DIR / "Zbyszek" / vc_cmd.STAGED_WORK
    assert all(Path(p).parent == staged for p in zb[0]["dubs"].values())
    assert not staged.exists()  # the staged takes are removed after pass 2
    rows = latest_by(read_jsonl(root / layout.POST))
    lines = latest_by(read_jsonl(root / layout.LINES))
    zb_ids = sorted(lid for lid, ln in lines.items() if ln["speaker_hint"] == "Zbyszek")
    for lid in zb_ids:
        info = rows[lid]["match"]
        assert info["ref"] == "pair" and info["pair_lines"] == 4 and info["target"] == "character", info
        assert rows[lid]["pre_match_wav"].startswith(layout.VC_PRE_MATCH_DIR)
    assert run(capsys, root, "vc")[1]["up_to_date"] == 4

    # one line again: the pair takes the other lines' converted takes before their match from their vc rows
    seen.clear()
    code, out = run(capsys, root, "vc", "--ids", zb_ids[0], "--force")
    assert code == EXIT_OK and out["converted"] == 1, out
    dubs = seen[0]["dubs"]
    assert Path(dubs[zb_ids[0]]).parent == staged
    assert {project.rel(Path(dubs[lid])) for lid in zb_ids[1:]} == {rows[lid]["pre_match_wav"] for lid in zb_ids[1:]}
    assert latest_by(read_jsonl(root / layout.POST))[zb_ids[0]]["match"]["pair_lines"] == 4

    # another target makes the converted takes stale; rows without a target count as `line`
    project.data["post"]["match_target"] = "line"
    project.save()
    code, out = run(capsys, root, "vc")
    assert out["converted"] == 4 and out["match"]["pair"] == 0 and out["match"]["target"] == "line", out
    rows = latest_by(read_jsonl(root / layout.POST))
    assert all(rows[lid]["match"]["target"] == "line" and rows[lid]["match"]["ref"] != "pair" for lid in zb_ids)
    assert run(capsys, root, "vc")[1]["up_to_date"] == 4
    legacy = [{**rows[lid], "match": {k: v for k, v in rows[lid]["match"].items() if k != "target"}} for lid in zb_ids]
    append_jsonl(root / layout.POST, legacy)
    assert run(capsys, root, "vc")[1]["up_to_date"] == 4
    project.data["post"]["match_target"] = "character"
    project.save()
    assert run(capsys, root, "vc")[1]["converted"] == 4


def test_pitch_keep_is_zero_and_unknown_mode_is_usage(project: Project, applio: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    assert vc_cmd.pitch_shifts(project, [], "line") == []
    assert vc_cmd._clip_shift(None) == 0 and vc_cmd._clip_shift(30.0) == 12 and vc_cmd._clip_shift(-13.4) == -12
    project.data["vc"]["pitch"] = "octave"  # a hand-edited project.json
    project.save()
    project.mark_stage("post", "done")
    code, out = run(capsys, project.root, "vc", "--dry-run")
    assert code == EXIT_USAGE and "vc.pitch" in out["error"], out
    _model(project)
    with pytest.raises(BabelDubError) as err:  # `synth score` converts with the same setting: the same error
        vc_cmd.convert_for_scoring(project, {"Zbyszek": [("u_s0", Path("c.wav"), {})]})
    assert err.value.code == EXIT_USAGE and err.value.hint == "babeldub set vc.pitch=line"


def _model(project: Project, cid: str = "Zbyszek") -> bool:
    folder = project.path(layout.VC_DIR, cid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.pth").write_bytes(b"G")
    (folder / "model.index").write_bytes(b"I")
    (folder / layout.VC_MODEL_JSON).write_text(json.dumps({"char_id": cid, "trained": "t"}), encoding="utf-8")
    return True


def _tone(path: Path, f0: float, like: Path | None = None) -> Path:
    """A take at `f0` with the length and rate of `like` (or 2 s at 44.1 kHz)."""
    frames, sr = (probe(like)["frames"], probe(like)["sr"]) if like is not None else (88200, 44100)
    t = np.arange(frames) / sr
    return write_wav(path, (0.3 * np.sin(2 * np.pi * f0 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32), sr)


def test_pitch_char_is_one_shift_over_all_current_takes(project: Project, applio: Path, tmp_path: Path,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    root = project.root
    assert run(capsys, root, "vc", "train", "--chars", "Zbyszek", "--epochs", "2", "--minutes", "1")[0] == EXIT_OK
    assert run(capsys, root, "synth")[0] == EXIT_OK
    assert run(capsys, root, "post")[0] == EXIT_OK
    lines = latest_by(read_jsonl(root / layout.LINES))
    post = latest_by(read_jsonl(root / layout.POST))
    zb = sorted(lid for lid, ln in lines.items() if ln["speaker_hint"] == "Zbyszek")
    for lid, f0 in zip(zb, (100.0, 150.0, 200.0, 250.0), strict=True):  # takes in different registers
        source = root / post[lid]["pre_match_wav"]
        _tone(source, f0, like=source)
    orig = np.median([vc_cmd._median_hz(root / lines[lid]["audio"]["wav"]) for lid in zb])

    def expected(takes: list[Path]) -> int:
        return vc_cmd._clip_shift(semitones(orig, float(np.median([vc_cmd._median_hz(p) for p in takes]))))

    first = expected([root / post[lid]["pre_match_wav"] for lid in zb])
    code, out = run(capsys, root, "vc", "--pitch", "char")
    assert code == EXIT_OK and out["converted"] == 4, out
    rows = latest_by(read_jsonl(root / layout.POST))
    assert {rows[lid]["vc"]["pitch_shift_st"] for lid in zb} == {first}
    # one line alone (or a partial run) keeps the character's shift, not the shift of that one take
    assert expected([root / post[zb[3]]["pre_match_wav"]]) != first
    code, out = run(capsys, root, "vc", "--pitch", "char", "--ids", zb[3], "--force")
    assert code == EXIT_OK and latest_by(read_jsonl(root / layout.POST))[zb[3]]["vc"]["pitch_shift_st"] == first, out

    # a regenerated take moves the character's median: every take is converted again with the new shift
    rows = latest_by(read_jsonl(root / layout.POST))
    regen = _tone(root / layout.POST_DIR / "regen.wav", 300.0, like=root / rows[zb[0]]["pre_vc_wav"])
    new_row = {k: v for k, v in rows[zb[0]].items() if k not in ("vc", "pre_vc_wav", "stage")}
    append_jsonl(root / layout.POST, [{**new_row, "wav": project.rel(regen), "pre_match_wav": project.rel(regen)}])
    second = expected([regen, *(root / rows[lid]["pre_vc_wav"] for lid in zb[1:])])
    assert second != first
    code, out = run(capsys, root, "vc", "--pitch", "char")
    assert code == EXIT_OK and out["converted"] == 4 and out["up_to_date"] == 0, out
    rows = latest_by(read_jsonl(root / layout.POST))
    assert {rows[lid]["vc"]["pitch_shift_st"] for lid in zb} == {second}

    # `synth score` converts candidates with the same character-wide shift, not one from the candidates
    project.data["vc"]["pitch"] = "char"
    project.save()
    (applio / "calls.log").unlink()
    cand = _tone(tmp_path / "cand.wav", 400.0)
    out_files, errors = vc_cmd.convert_for_scoring(project, {"Zbyszek": [("c_s0", cand, lines[zb[0]])]})
    assert errors == [] and list(out_files) == ["c_s0"]
    calls = (applio / "calls.log").read_text(encoding="utf-8").splitlines()
    assert [int(c.split("--pitch ")[1].split()[0]) for c in calls] == [second]
    vc_cmd.drop_scoring_files(project, {"Zbyszek"})


def test_channel_pool_leaves_an_unreadable_mate_out(project: Project) -> None:
    lines = latest_by(read_jsonl(project.path(layout.LINES)))
    zb = sorted(lid for lid, ln in lines.items() if ln["speaker_hint"] == "Zbyszek")
    project.path(lines[zb[2]]["audio"]["wav"]).write_bytes(b"")  # the character's longest original
    pool = voice.ChannelPool(project, lines)
    assert len(pool.extra(lines[zb[3]])) == 2  # the short line gets the readable mates
    assert [lid for lid, _p in pool.cache["Zbyszek"]] == [zb[0], zb[1], zb[3]]  # measured once, without the bad one


def test_an_unreadable_original_is_left_out_of_the_pitch_shift(project: Project, applio: Path,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    root = project.root
    lines = latest_by(read_jsonl(root / layout.LINES))
    zb = sorted(lid for lid, ln in lines.items() if ln["speaker_hint"] == "Zbyszek")
    assert run(capsys, root, "vc", "train", "--chars", "Zbyszek", "--epochs", "2", "--minutes", "1")[0] == EXIT_OK
    assert run(capsys, root, "synth")[0] == EXIT_OK
    project.data["post"]["match"] = "off"  # vc's channel match would need the original too
    project.save()
    assert run(capsys, root, "post")[0] == EXIT_OK
    bad = root / lines[zb[1]]["audio"]["wav"]
    bad.write_bytes(b"")  # a truncated original
    assert vc_cmd._median_hz(bad) is None and vc_cmd._median_hz(root / "missing.wav") is None
    for mode in ("line", "char"):
        code, out = run(capsys, root, "vc", "--pitch", mode)
        assert code == EXIT_OK and out["converted"] == 4, out
        rows = latest_by(read_jsonl(root / layout.POST))
        assert all(rows[lid]["vc"]["pitch"] == mode for lid in zb)
        if mode == "line":
            assert rows[zb[1]]["vc"]["pitch_shift_st"] == 0  # no measurable original: no shift
