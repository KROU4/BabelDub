"""Two-pass best-of (tts.best_of_mode=score): keep every candidate, rank them with the qc models, keep one."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from babeldub.audio.io import write_wav
from babeldub.commands import voice
from babeldub.core import layout
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import latest_by, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_OK
from babeldub.core.paths import env_python
from babeldub.core.project import Project
from babeldub.qa import rank
from babeldub.qa.metrics import cer, contour_correlation, f0_range_ratio, prosody_similarity

SR = 24000
LID = "fmod:Audio/Dialogue_en.bank#fsb0:0001"


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


class FakeScoreQc:
    """qc worker double: candidate c1 has the best MOS and prosody, c2 mumbles (CER gate)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.restarts, self.max_restarts = 0, 3

    def load(self, msg: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "op": "load", "device": "cpu", "info": {}}

    def request(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(msg)
        if msg["op"] != "score":
            return {"ok": False, "error": "unsupported"}
        k = int(Path(msg["wav"]).stem.split("c")[-1])
        contour = [float(np.sin(i / 3.0)) for i in range(40)]
        return {"ok": True, "op": "score", "asr_text": "бормотание" if k == 2 else msg["text"], "cer": None,
                "sim": 0.5, "emo_cos": None, "mos": 4.0 if k == 1 else 3.0,
                "prosody": {"f0_range_st": 6.0 if k == 1 else 3.0, "contour": contour},
                "orig_prosody": {"f0_range_st": 6.0, "contour": contour}}

    def close(self) -> None:
        return None


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    home = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    root = home / "projects" / "sc"
    proj = Project.create(root, {"slug": "sc", "game_name": "Sc", "game_dir": str(tmp_path / "game"),
                                 "target_lang": "ru", "source_lang": "en", "compute": {"device": "cpu"},
                                 "tts": {"engine": "dummy", "ref_strategy": "line", "best_of": 3,
                                         "best_of_mode": "score"},
                                 "post": {"tolerance": 100.0}})
    u = make_uid(LID)
    t = np.arange(int(3.0 * SR)) / SR
    wav = write_wav(root / layout.ORIG_AUDIO_DIR / f"{u}.wav", (0.3 * np.sin(2 * np.pi * 140 * t)).astype(np.float32), SR)
    write_jsonl(root / layout.LINES, [{
        "id": LID, "uid": u, "kind": "dialogue", "locale": "en-US", "speaker_hint": "Zbyszek",
        "audio": {"sr": SR, "ch": 1, "dur_s": 3.0, "wav": proj.rel(wav)}, "flags": [],
        "text": {"src": "This is the original line number one.", "src_lang": "en"}}], schema="babeldub.lines/1")
    write_jsonl(root / layout.TRANSLATIONS, [{"id": LID, "dub_text": "Это реплика номер один, малой.", "lang": "ru"}],
                schema="babeldub.translations/1")
    return proj


@pytest.fixture()
def fake_qc(monkeypatch: pytest.MonkeyPatch) -> FakeScoreQc:
    import babeldub.qa.qc as qc

    fake = FakeScoreQc()
    monkeypatch.setattr(qc, "_make_client", lambda log_path: fake)
    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: [])
    return fake


def take_dir(project: Project) -> list[str]:
    return sorted(p.name for p in project.path(layout.SYNTH_DIR, make_uid(LID)).iterdir())


def test_score_mode_keeps_the_ranked_winner(project: Project, fake_qc: FakeScoreQc,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK and out["best_of_mode"] == "score" and out["score"]["scored"] == 1, out
    rows = read_jsonl(project.path(layout.SYNTH_RESULTS))
    first, final = rows[0], rows[-1]
    assert first["best_of"]["scored"] is False and len(first["best_of"]["cands"]) == 3
    assert final["best_of"]["scored"] is True and final["attempt"] == first["attempt"]
    seeds = [c["seed"] for c in first["best_of"]["cands"]]
    assert final["seed"] == seeds[1] == final["best_of"]["picked_seed"]  # best MOS and prosody
    scores = {s["seed"]: s for s in final["best_of"]["scores"]}
    assert scores[seeds[2]]["passed"] is False and scores[seeds[2]]["cer"] > 0.5  # gated out by CER
    assert final["key"] == voice._sha(first["key"], seeds[1]) and final["wav"] == first["wav"]
    assert take_dir(project) == ["a1.wav"]  # losers deleted
    want = fake_qc.requests[0]["want"]
    assert set(want) == {"cer", "sim", "emo", "mos", "prosody"} and fake_qc.requests[0]["orig_wav"]

    # post takes the scored row; a new synth run sees the line as done
    assert run(capsys, project.root, "post")[0] == EXIT_OK
    assert latest_by(read_jsonl(project.path(layout.POST)))[LID]["src_key"] == final["key"]
    code, out = run(capsys, project.root, "synth")
    assert out["skipped"]["done"] == 1 and out["score"] == {"scored": 0}


def test_without_qc_the_provisional_pick_stands_until_synth_score(project: Project, monkeypatch: pytest.MonkeyPatch,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, project.root, "synth")
    assert out["score"] == {"scored": 0, "pending": 1} and any("synth score" in n for n in out["notes"])
    assert take_dir(project) == ["a1.wav", "a1c0.wav", "a1c1.wav", "a1c2.wav"]
    assert run(capsys, project.root, "synth", "score", "--dry-run")[1]["pending"] == 1
    import babeldub.qa.qc as qc

    monkeypatch.setattr(qc, "_make_client", lambda log_path: FakeScoreQc())
    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: [])
    code, out = run(capsys, project.root, "synth", "score")
    assert code == EXIT_OK and out["scored"] == 1 and out["pending"] == 0 and out["next"] == "babeldub post"
    assert take_dir(project) == ["a1.wav"]
    # the max_attempts count is per attempt: the scored row does not use one up
    jobs, *_ = voice._plan_synth(project, parser().parse_args(["synth", "--force"]), voice.resolve_engine(project, "dummy"),
                                 "line")
    assert jobs[0].attempt == 2


def test_best_of_mode_auto_follows_the_qc_env(project: Project) -> None:
    project.data["tts"]["best_of_mode"] = "auto"
    assert voice.best_of_mode(project) == "length"
    python = env_python("qc")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    assert voice.best_of_mode(project) == "score"


def test_rank_gates_and_weights() -> None:
    def cand(seed: int, **kw: Any) -> rank.Candidate:
        return rank.Candidate(seed=seed, wav=f"c{seed}.wav", speech_s=kw.pop("speech_s", 2.0), **kw)

    # length gate: 3.0 s against a 2.0 s original is out, even with the best MOS
    cands = [cand(1, mos=4.5, speech_s=3.0, cer=0.0), cand(2, mos=3.0, cer=0.02), cand(3, mos=3.5, cer=0.3)]
    win = rank.rank(cands, target_s=2.0, max_cer=0.15)
    assert win.seed == 2 and not cands[0].passed and not cands[2].passed  # CER 0.3 > max(0.15, 0.0 + 0.05)
    # loose CER cap: the best CER is 0.2, so up to 0.25 passes
    cands = [cand(1, cer=0.2, mos=3.0), cand(2, cer=0.24, mos=4.0)]
    assert rank.rank(cands, target_s=2.0, max_cer=0.15).seed == 2
    # voice similarity weighs less for a character with an RVC model
    cands = [cand(1, sim=0.9, mos=3.0), cand(2, sim=0.1, mos=3.2)]
    assert rank.rank(cands, target_s=2.0, max_cer=0.15).seed == 1
    assert rank.rank(cands, target_s=2.0, max_cer=0.15, rvc=True).seed == 2
    # nothing passes: everything is ranked
    cands = [cand(1, speech_s=9.0), cand(2, speech_s=8.0)]
    assert rank.rank(cands, target_s=2.0, max_cer=0.15).seed == 2
    assert rank.length_fit(2.0, 2.0) == 1.0 and rank.length_fit(2.5, 2.0) == pytest.approx(0.0)


def test_prosody_metrics_and_number_cer(monkeypatch: pytest.MonkeyPatch) -> None:
    assert f0_range_ratio(3.0, 6.0) == 0.5 and f0_range_ratio(None, 6.0) is None and f0_range_ratio(0.0, 6.0) is None
    wave = [math.sin(i / 4.0) for i in range(40)]
    assert contour_correlation(wave, [2 * v + 1 for v in wave]) == pytest.approx(1.0)
    assert contour_correlation(wave, [None if i % 2 else -v for i, v in enumerate(wave)]) < 0
    assert contour_correlation(wave[:5], wave) is None  # too short
    assert contour_correlation(wave, wave + wave + wave) is None  # lengths too different
    sim = prosody_similarity({"f0_range_st": 3.0, "contour": wave}, {"f0_range_st": 6.0, "contour": wave})
    assert sim["sim"] == pytest.approx(0.6 * 0.5 + 0.4)
    assert prosody_similarity({"f0_range_st": 3.0}, {"f0_range_st": 6.0})["sim"] == 0.5

    import babeldub.text.normalize as normalize

    words = {25: "двадцать пять"}
    monkeypatch.setattr(normalize, "_num2words", lambda: (lambda value, lang: words[value]))
    assert cer("двадцать пять раз", "25 раз", "ru") == 0.0
    assert cer("двадцать пять раз", "25 раз") > 0.3

    from babeldub.qa.qc import number_cer

    monkeypatch.setattr(normalize, "_num2words", lambda: None)

    class Spell:
        def spell(self, texts: list[str], lang: str) -> list[str]:
            return [t.replace("25", "двадцать пять") for t in texts]

    assert number_cer(Spell(), "двадцать пять раз", "25 раз", "ru") == 0.0  # type: ignore[arg-type]
    assert number_cer(None, "двадцать пять раз", "25 раз", "ru") > 0.3
