"""synth -> post on a synthetic project with the dummy TTS engine (no GPU, no game assets)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from babeldub.audio import dsp
from babeldub.audio.io import probe, read_audio, write_wav
from babeldub.commands import voice
from babeldub.core import layout
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import append_jsonl, latest_by, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_ENV, EXIT_OK, EXIT_USAGE, BabelDubError
from babeldub.core.project import Project
from babeldub.core.tools import find_tool

HAS_FFMPEG = find_tool("ffmpeg") is not None


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
    assert len(out) == 1, out  # exactly one JSON object on stdout
    return code, json.loads(out[0])


def speechlike(seconds: float, sr: int, ch: int, f0: float, lead: float, trail: float, amp: float) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    voice_sig = sum(np.sin(2 * np.pi * k * f0 * t) / k for k in range(1, 6)) * (0.55 + 0.45 * np.sin(2 * np.pi * 4 * t))
    voice_sig = amp * voice_sig / np.max(np.abs(voice_sig))
    full = np.concatenate([np.zeros(int(lead * sr)), voice_sig, np.zeros(int(trail * sr))]).astype(np.float32)
    return np.repeat(full[:, None], ch, axis=1)


LINES = [
    # id suffix, kind, speaker, src text, speech seconds, channels, fixed, dub text
    ("0001", "dialogue", "Zbyszek", "Okay, let's see what's wrong with this old thing.", 2.6, 2, False,
     "Так, посмотрим, что не так с этой старой рухлядью."),
    ("0002", "cutscene", "Zbyszek", "Reporting in, Chief!", 1.1, 1, True, "Отчитываюсь, шеф!"),
    ("0003", "bark", "Maciek", "Hey!", 0.35, 1, False, "Эй!"),
    ("0004", "sfx", None, None, 1.0, 1, False, None),
    ("0005", "dialogue", "Maciek", "No translation yet.", 1.8, 1, False, None),
]


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    home = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    root = home / "projects" / "test-game"
    proj = Project.create(root, {"slug": "test-game", "game_name": "Test", "game_dir": str(tmp_path / "game"),
                                 "target_lang": "ru", "source_lang": "en", "tts": {"engine": "dummy"}})
    sr = 48000
    lines, translations, refs = [], [], []
    for n, (suffix, kind, speaker, src, secs, ch, fixed, dub) in enumerate(LINES):
        line_id = f"fmod:Audio/Dialogue_en.bank#fsb0:{suffix}"
        u = make_uid(line_id)
        audio = speechlike(secs, sr, ch, 110.0 + 20 * n, 0.12, 0.2, 0.25)
        wav = write_wav(root / layout.ORIG_AUDIO_DIR / f"{u}.wav", audio, sr)
        row: dict[str, Any] = {
            "id": line_id, "uid": u, "adapter": "fmod", "asset_id": "fmod:Audio/Dialogue_en.bank",
            "entry": {"key": f"dlg_{suffix}", "sub": "fsb0", "index": n}, "kind": kind, "locale": "en-US",
            "audio": {"sr": sr, "ch": ch, "dur_s": round(len(audio) / sr, 4), "codec": "vorbis", "wav": proj.rel(wav)},
            "speaker_hint": speaker, "scene": "s1", "order": n, "fixed_length": fixed, "flags": [],
        }
        if src:
            row["text"] = {"key": f"dlg_{suffix}", "src": src, "src_lang": "en", "source": "test"}
        lines.append(row)
        if dub:
            translations.append({"id": line_id, "dub_text": dub, "sub_text": dub, "lang": "ru", "by": "agent", "rev": 1})
    # clean character references (as `cast` would pick them)
    for char, f0 in (("Zbyszek", 110.0), ("Maciek", 150.0)):
        ref_wav = write_wav(root / layout.REFS_DIR / char / "ref.wav", speechlike(4.0, sr, 1, f0, 0.05, 0.05, 0.3), sr)
        refs.append({"ref_id": f"{char}:1", "char_id": char, "line_id": None, "wav": proj.rel(ref_wav),
                     "text": f"This is {char} speaking clearly.", "lang": "en", "dur_s": 4.1, "score": 0.9, "chosen": True})
    write_jsonl(root / layout.LINES, lines, schema="babeldub.lines/1")
    write_jsonl(root / layout.TRANSLATIONS, translations, schema="babeldub.translations/1")
    write_jsonl(root / layout.REFS, refs, schema="babeldub.refs/1")
    return proj


def results(project: Project) -> list[dict[str, Any]]:
    return read_jsonl(project.path(layout.SYNTH_RESULTS))


def test_synth_strategy_idempotency_and_attempts(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, project.root, "synth", "--dry-run")
    assert code == EXIT_OK and out["planned"] == 3 and out["skipped"]["no_translation"] == 1

    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK, out
    assert out["done"] == 3 and out["failed"] == 0 and out["remaining"] == 0
    rows = latest_by(results(project))
    by_suffix = {k.rsplit(":", 1)[1]: v for k, v in rows.items()}
    # auto: long transcribed line -> the original line is the voice prompt; short ones -> character ref
    assert by_suffix["0001"]["ref_mode"] == "line"
    assert by_suffix["0001"]["ref_wav"].startswith(layout.ORIG_AUDIO_DIR)
    assert by_suffix["0002"]["ref_mode"] == "char" and by_suffix["0003"]["ref_mode"] == "char"
    assert by_suffix["0003"]["ref_id"] == "Maciek:1"
    for row in rows.values():
        assert row["status"] == "ok" and row["attempt"] == 1 and project.path(row["wav"]).exists()

    code, out = run(capsys, project.root, "synth")  # nothing new to do
    assert code == EXIT_OK and out["done"] == 0 and out["skipped"]["done"] == 3

    # QA asks for a regeneration of 0001 and a different voice prompt for 0003
    id1, id3 = (f"fmod:Audio/Dialogue_en.bank#fsb0:{s}" for s in ("0001", "0003"))
    append_jsonl(project.path(layout.DECISIONS), [
        {"id": id1, "decision": "regen", "attempt": 1, "by": "auto", "note": "", "ts": "x"},
        {"id": id3, "decision": "reref", "attempt": 1, "by": "agent", "note": "", "ts": "x"},
    ])
    code, out = run(capsys, project.root, "synth", "--attempt-next")
    assert code == EXIT_OK and out["done"] == 2
    rows = latest_by(results(project))
    assert rows[id1]["attempt"] == 2 and rows[id1]["seed"] != by_suffix["0001"]["seed"]
    assert rows[id1]["ref_mode"] == "line"
    assert rows[id3]["ref_mode"] == "line"  # only one char ref -> switch to the original line
    code, out = run(capsys, project.root, "synth")  # the reref'd attempt stands; nothing to redo
    assert code == EXIT_OK and out["done"] == 0 and out["skipped"]["done"] == 3

    # attempts are capped by tts.max_attempts
    project.data["tts"]["max_attempts"] = 2
    project.save()
    append_jsonl(project.path(layout.DECISIONS), [{"id": id1, "decision": "regen", "attempt": 2, "by": "auto"}])
    code, out = run(capsys, project.root, "synth", "--attempt-next")
    assert code == EXIT_ATTENTION and out["skipped"]["max_attempts"] == 1 and id1 in out["attention"]

    # a retranslated line is synthesized again without --force
    write_jsonl(project.path(layout.TRANSLATIONS), [
        *read_jsonl(project.path(layout.TRANSLATIONS)),
        {"id": id3, "dub_text": "Эй, ты!", "lang": "ru", "by": "human", "rev": 2},
    ])
    code, out = run(capsys, project.root, "synth", "--ids", make_uid(id3))
    assert code == EXIT_OK and out["done"] == 1
    assert latest_by(results(project))[id3]["text"] == "Эй, ты!"


def test_synth_selects_cast_and_chars(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    ids = [f"fmod:Audio/Dialogue_en.bank#fsb0:{s}" for s in ("0001", "0002", "0003", "0004", "0005")]
    write_jsonl(project.path(layout.CHARACTERS), [
        {"char_id": "Zbyszek", "name": "Zbyszek", "selected": True},
        {"char_id": "Maciek", "name": "Maciek", "selected": False},
    ])
    write_jsonl(project.path(layout.ASSIGN), [
        {"id": ids[0], "char_id": "Zbyszek", "conf": 1.0}, {"id": ids[1], "char_id": "Zbyszek", "conf": 1.0},
        {"id": ids[2], "char_id": "Maciek", "conf": 1.0}, {"id": ids[4], "char_id": "Maciek", "conf": 1.0},
    ])
    code, out = run(capsys, project.root, "synth", "--dry-run")
    assert out["planned"] == 2 and out["skipped"]["not_selected"] == 2
    code, out = run(capsys, project.root, "synth", "--chars", "maciek", "--dry-run")
    assert out["planned"] == 1  # explicit --chars overrides the selection; 0005 has no translation
    code, out = run(capsys, project.root, "synth", "--limit", "1")
    assert code == EXIT_OK and out["done"] == 1 and out["remaining"] == 1 and out["next"] == "babeldub synth"


def test_batching_and_fallback(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, project.root, "synth", "--batch", "2")
    assert code == EXIT_OK and out["done"] == 3 and out["batch"] == 2
    assert sorted(r.get("batch", 1) for r in results(project)) == [1, 2, 2]
    # a batch that fails as a whole is retried line by line
    project.data["tts"]["params"] = {"dummy_fail": "batch"}
    project.save()
    code, out = run(capsys, project.root, "synth", "--force")
    assert code == EXIT_OK and out["done"] == 3
    assert all("batch" not in r for r in results(project)[3:])
    code, out = run(capsys, project.root, "synth", "--force", "--batch", "1")
    assert code == EXIT_OK and out["batch"] == 1 and out["done"] == 3


def test_default_model_prefers_general_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home2"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    catalog = voice._catalog()
    assert catalog is not None
    ref, name, source = voice._resolve_model("qwen3tts", None, "ru", "auto", catalog)
    assert (name, source, ref) == ("Qwen3-TTS-12Hz-1.7B-Base", "hf", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
    for folder in ("Qwen3-TTS-12Hz-1.7B-Base", "qwen3-tts-12hz-1.7b-ru-stress-cf"):
        (home / "models" / folder).mkdir(parents=True)
        (home / "models" / folder / "model.safetensors").write_bytes(b"x")
    ref, name, source = voice._resolve_model("qwen3tts", None, "ru", "auto", catalog)
    assert name == "Qwen3-TTS-12Hz-1.7B-Base" and source == "local" and Path(ref).is_dir()
    _, name, _ = voice._resolve_model("qwen3tts", "qwen3-tts-12hz-1.7b-ru-stress-cf", "ru", "auto", catalog)
    assert name == "qwen3-tts-12hz-1.7b-ru-stress-cf"  # an explicit tts.model wins


def test_engine_checks(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    project.data["tts"]["engine"] = "qwen3tts"
    project.save()
    with pytest.raises(BabelDubError) as err:
        run(capsys, project.root, "synth")
    assert err.value.code == EXIT_ENV and err.value.hint == "babeldub setup --engines qwen3tts"
    code, out = run(capsys, project.root, "synth", "--dry-run")  # planning works without the venv
    assert out["env_ready"] is False and out["planned"] == 3
    project.data["target_lang"] = "pl"
    project.save()
    with pytest.raises(BabelDubError) as err:
        run(capsys, project.root, "synth")
    assert err.value.code == EXIT_USAGE and "chatterbox" in (err.value.hint or "")


def _orig(project: Project, suffix: str) -> tuple[np.ndarray, int]:
    return read_audio(project.path(layout.ORIG_AUDIO_DIR, f"{make_uid(f'fmod:Audio/Dialogue_en.bank#fsb0:{suffix}')}.wav"))


def test_post_without_stretch(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    project.data["tts"]["params"] = {"syllables_per_s": 40.0}  # dummy speaks fast: everything fits
    project.save()
    assert run(capsys, project.root, "synth")[0] == EXIT_OK
    code, out = run(capsys, project.root, "post")
    assert code == EXIT_OK, out
    assert out["done"] == 3 and out["stretch"] == {"none": 3}
    rows = latest_by(read_jsonl(project.path(layout.POST)))
    for suffix in ("0001", "0002", "0003"):
        line_id = f"fmod:Audio/Dialogue_en.bank#fsb0:{suffix}"
        row = rows[line_id]
        info = probe(project.path(row["wav"]))
        orig, o_sr = _orig(project, suffix)
        assert info["sr"] == o_sr == 48000 and info["ch"] == orig.shape[1]
        assert row["lead_s"] == pytest.approx(0.12, abs=0.02)  # edge silence copied from the original
        assert row["peak_db"] <= -0.95
        assert row["lufs_out"] == pytest.approx(row["level_ref"], abs=1.0)  # loudness matched to the original
    assert rows["fmod:Audio/Dialogue_en.bank#fsb0:0002"]["dur_s"] <= probe(project.path(layout.ORIG_AUDIO_DIR, f"{make_uid('fmod:Audio/Dialogue_en.bank#fsb0:0002')}.wav"))["dur_s"] + 1e-3
    code, out = run(capsys, project.root, "post")
    assert code == EXIT_OK and out["done"] == 0 and out["up_to_date"] == 3


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_post_stretches_and_flags_over_length(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    project.data["tts"]["params"] = {"syllables_per_s": 2.5}  # dummy speaks slowly: lines run long
    project.save()
    assert run(capsys, project.root, "synth")[0] == EXIT_OK
    code, out = run(capsys, project.root, "post", "--stretch-method", "atempo")
    rows = latest_by(read_jsonl(project.path(layout.POST)))
    fixed = rows["fmod:Audio/Dialogue_en.bank#fsb0:0002"]
    assert fixed["fixed_length"] is True
    assert fixed["stretch"] == pytest.approx(0.88, abs=1e-3)  # clamped by post.max_stretch
    assert fixed["stretch_method"] == "atempo"
    assert fixed["over_length"] is True and fixed["id"] in out["over_length"]
    assert code == EXIT_ATTENTION
    for row in rows.values():
        if row["stretch"] < 1.0:
            assert row["ratio"] < row["ratio_raw"]
    data, sr = read_audio(project.path(fixed["wav"]))
    assert dsp.true_peak_db(data, sr) <= -0.95
