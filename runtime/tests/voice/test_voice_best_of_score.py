"""Two-pass best-of (tts.best_of_mode=score): keep every candidate, rank them with the qc models, keep one."""

from __future__ import annotations

import argparse
import json
import math
import re
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
        k = int(re.search(r"a\d+c(\d+)", Path(msg["wav"]).name).group(1))  # a1c<k>.wav or its converted copy
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


class CrashingScorer:
    """A qc session whose scoring dies on the second line of the pass."""

    def __init__(self, crash_on: str | None) -> None:
        self.crash_on = crash_on
        self.fake = FakeScoreQc()

    def score(self, key: str, wav: Path, **kw: Any) -> dict[str, Any]:
        if self.crash_on and key.startswith(self.crash_on):
            raise RuntimeError("worker died")
        return self.fake.request({"op": "score", "wav": str(wav), "text": kw.get("text")})

    def spell(self, texts: list[str], lang: str) -> list[str]:
        return texts


def test_score_crash_mid_pass_keeps_finished_lines_consistent(project: Project,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    lid2 = LID.replace("0001", "0002")
    u2 = make_uid(lid2)
    lines = read_jsonl(project.path(layout.LINES))
    wav2 = project.path(layout.ORIG_AUDIO_DIR, f"{u2}.wav")
    wav2.write_bytes(project.path(lines[0]["audio"]["wav"]).read_bytes())
    lines.append({**lines[0], "id": lid2, "uid": u2, "audio": {**lines[0]["audio"], "wav": project.rel(wav2)}})
    write_jsonl(project.path(layout.LINES), lines, schema="babeldub.lines/1")
    tr = read_jsonl(project.path(layout.TRANSLATIONS))
    write_jsonl(project.path(layout.TRANSLATIONS), [*tr, {**tr[0], "id": lid2}], schema="babeldub.translations/1")
    assert run(capsys, project.root, "synth")[1]["score"] == {"scored": 0, "pending": 2}  # no qc env yet

    rows = sorted(voice._pending_scores(project), key=lambda r: r["id"])
    first, second = rows
    win_bytes = project.path(first["best_of"]["cands"][1]["wav"]).read_bytes()  # c1 wins
    with pytest.raises(RuntimeError):
        voice.score_candidates(project, rows, CrashingScorer(crash_on=second["id"]))

    latest = latest_by(read_jsonl(project.path(layout.SYNTH_RESULTS)))
    done = latest[first["id"]]
    assert done["best_of"]["scored"] is True and done["key"] == voice._sha(first["key"], done["seed"])
    assert project.path(done["wav"]).read_bytes() == win_bytes  # the row describes the audio on disk
    assert sorted(p.name for p in project.path(done["wav"]).parent.iterdir()) == ["a1.wav"]
    left = latest[second["id"]]
    assert left["best_of"]["scored"] is False and all(project.path(c["wav"]).exists() for c in left["best_of"]["cands"])

    # the next pass scores only the unfinished line
    out = voice.score_candidates(project, voice._pending_scores(project), CrashingScorer(crash_on=None))
    assert out["scored"] == 1 and out["pending"] == 0
    assert latest_by(read_jsonl(project.path(layout.SYNTH_RESULTS)))[second["id"]]["best_of"]["scored"] is True


def _rvc_model(project: Project, cid: str) -> None:
    folder = project.path(layout.VC_DIR, cid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.pth").write_bytes(b"G")
    (folder / "model.index").write_bytes(b"I")
    (folder / layout.VC_MODEL_JSON).write_text(json.dumps({"char_id": cid, "trained": "t"}), encoding="utf-8")


def test_rvc_characters_are_converted_before_scoring(project: Project, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    import babeldub.commands.vc as vc_cmd
    import babeldub.qa.qc as qc

    assert run(capsys, project.root, "synth")[1]["score"]["pending"] == 1  # no qc env yet
    cid = str(voice._pending_scores(project)[0]["char_id"])
    _rvc_model(project, cid)
    write_jsonl(project.path(voice.ANALYSIS), [{"id": f"x{i}", "prosody": {"f0_median_hz": 120.0 + i}} for i in range(3)],
                schema="babeldub.analysis/1")
    write_jsonl(project.path(layout.ASSIGN), [{"id": LID, "char_id": cid, "conf": 1.0},
                                             *({"id": f"x{i}", "char_id": cid, "conf": 1.0} for i in range(3))])
    calls: list[dict[str, Any]] = []

    def fake_convert(proj: Project, jobs: dict[str, list[tuple[str, Path, dict[str, Any]]]], wait_lock: float = 0.0):
        calls.append(jobs)
        out = {}
        for c, items in jobs.items():
            work = proj.path(layout.VC_DIR, c, vc_cmd.SCORE_WORK, "out")
            work.mkdir(parents=True, exist_ok=True)
            for name, wav, line in items:
                assert line["id"] == LID
                out[name] = work / f"{wav.stem}_output.wav"
                out[name].write_bytes(wav.read_bytes())
        return out, []

    monkeypatch.setattr(vc_cmd, "convert_for_scoring", fake_convert)
    fake = FakeScoreQc()
    monkeypatch.setattr(qc, "_make_client", lambda log_path: fake)
    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: [])
    code, out = run(capsys, project.root, "synth", "score")
    assert code == EXIT_OK and out["scored"] == 1, out
    assert list(calls[0]) == [cid] and len(calls[0][cid]) == 3
    scored = [r for r in fake.requests if r["op"] == "score"]
    assert all("_output.wav" in r["wav"] for r in scored)  # the converted takes were measured
    assert all(r["f0_hint_hz"] == 121.0 for r in scored)  # the character's median F0 bounds pyin
    final = latest_by(read_jsonl(project.path(layout.SYNTH_RESULTS)))[LID]
    assert final["best_of"]["scored_after_rvc"] is True and final["best_of"]["sim_weight"] == 0.3
    assert final["best_of"]["weights"]["sim"] == 0.3 and final["wav"].endswith("a1.wav")
    assert not project.path(layout.VC_DIR, cid, vc_cmd.SCORE_WORK).exists()  # converted copies removed


def test_without_applio_rvc_characters_keep_the_low_sim_weight(project: Project, monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    import babeldub.commands.vc as vc_cmd
    import babeldub.qa.qc as qc
    from babeldub.core.output import EXIT_ENV, BabelDubError

    run(capsys, project.root, "synth")
    cid = str(voice._pending_scores(project)[0]["char_id"])
    _rvc_model(project, cid)

    def no_applio() -> None:
        raise BabelDubError("Applio is not installed", code=EXIT_ENV)

    monkeypatch.setattr(vc_cmd.rvc, "require_applio", no_applio)
    monkeypatch.setattr(qc, "_make_client", lambda log_path: FakeScoreQc())
    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: [])
    code, out = run(capsys, project.root, "synth", "score")
    assert code == EXIT_OK and out["scored"] == 1 and "RVC before scoring skipped" in out["notes"][0], out
    final = latest_by(read_jsonl(project.path(layout.SYNTH_RESULTS)))[LID]
    assert final["best_of"]["scored_after_rvc"] is False and final["best_of"]["sim_weight"] == 0.05


def test_a_partial_rvc_failure_scores_the_whole_line_unconverted(project: Project, monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    import babeldub.commands.vc as vc_cmd
    import babeldub.qa.qc as qc

    run(capsys, project.root, "synth")
    cid = str(voice._pending_scores(project)[0]["char_id"])
    _rvc_model(project, cid)

    def half_convert(proj: Project, jobs: dict[str, list[tuple[str, Path, dict[str, Any]]]], wait_lock: float = 0.0):
        name, wav, _line = jobs[cid][0]  # only the first candidate converts, Applio fails on the others
        work = proj.path(layout.VC_DIR, cid, vc_cmd.SCORE_WORK, "out")
        work.mkdir(parents=True, exist_ok=True)
        out = work / f"{wav.stem}_output.wav"
        out.write_bytes(wav.read_bytes())
        return {name: out}, [f"{n}: no RVC output (Applio exit code 1)" for n, _w, _l in jobs[cid][1:]]

    monkeypatch.setattr(vc_cmd, "convert_for_scoring", half_convert)
    fake = FakeScoreQc()
    monkeypatch.setattr(qc, "_make_client", lambda log_path: fake)
    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: [])
    code, out = run(capsys, project.root, "synth", "score")
    assert code == EXIT_OK and out["scored"] == 1 and "scored unconverted" in out["notes"][0], out
    scored = [r for r in fake.requests if r["op"] == "score"]
    assert len(scored) == 3 and not any("_output.wav" in r["wav"] for r in scored)  # never a mix of the two
    final = latest_by(read_jsonl(project.path(layout.SYNTH_RESULTS)))[LID]
    assert final["best_of"]["scored_after_rvc"] is False and final["best_of"]["sim_weight"] == 0.05


def test_synth_score_notes_an_unavailable_second_asr(project: Project, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    import babeldub.qa.qc as qc

    class NoGigaQc(FakeScoreQc):
        def request(self, msg: dict[str, Any]) -> dict[str, Any]:
            reply = super().request(msg)
            return {**reply, "unavailable": ["asr2"], "asr2_error": "RuntimeError: GigaAM failed to load (OSError: x)"}

    run(capsys, project.root, "synth")
    monkeypatch.setattr(qc, "_make_client", lambda log_path: NoGigaQc())
    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: [])
    monkeypatch.setattr(qc, "asr2_model_dir", lambda settings: project.root / "giga")
    code, out = run(capsys, project.root, "synth", "score")
    assert code == EXIT_OK and out["scored"] == 1, out
    assert [n for n in out["notes"] if "second ASR" in n] == [
        "second ASR (GigaAM) unavailable: RuntimeError: GigaAM failed to load (OSError: x)"]


def test_an_invalid_vc_pitch_is_a_usage_error_in_synth_score(project: Project, monkeypatch: pytest.MonkeyPatch,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    import babeldub.qa.qc as qc
    from babeldub.core.output import EXIT_USAGE, BabelDubError

    run(capsys, project.root, "synth")
    _rvc_model(project, str(voice._pending_scores(project)[0]["char_id"]))
    project.data.setdefault("vc", {})["pitch"] = "lines"  # a hand-edited project.json
    project.save()
    monkeypatch.setattr(qc, "_make_client", lambda log_path: FakeScoreQc())
    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: [])
    with pytest.raises(BabelDubError) as err:
        run(capsys, project.root, "synth", "score")
    assert err.value.code == EXIT_USAGE and "vc.pitch" in str(err.value) and err.value.hint == "babeldub set vc.pitch=line"
    assert latest_by(read_jsonl(project.path(layout.SYNTH_RESULTS)))[LID]["best_of"]["scored"] is False


def test_post_redoes_fixed_length_line_when_analysis_segments_change(project: Project, fake_qc: FakeScoreQc,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    lines = read_jsonl(project.path(layout.LINES))
    lines[0]["fixed_length"] = True
    write_jsonl(project.path(layout.LINES), lines, schema="babeldub.lines/1")
    analysis = project.path(voice.ANALYSIS)
    write_jsonl(analysis, [{"id": LID, "vad_segments": [[0.1, 2.5]]}], schema="babeldub.analysis/1")
    assert run(capsys, project.root, "synth")[0] == EXIT_OK
    assert run(capsys, project.root, "post")[1]["done"] == 1
    assert latest_by(read_jsonl(project.path(layout.POST)))[LID]["vad_key"] is not None
    assert run(capsys, project.root, "post")[1]["up_to_date"] == 1
    # `analyze` re-ran after post: the line is placed again on the new segments
    write_jsonl(analysis, [{"id": LID, "vad_segments": [[0.3, 2.8]]}], schema="babeldub.analysis/1")
    out = run(capsys, project.root, "post")[1]
    assert out["done"] == 1 and out["up_to_date"] == 0


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
    # voice similarity weighs less for a character whose RVC model converts the winner later
    cands = [cand(1, sim=0.9, mos=3.0), cand(2, sim=0.1, mos=4.0)]
    assert rank.rank(cands, target_s=2.0, max_cer=0.15).seed == 1
    assert rank.rank(cands, target_s=2.0, max_cer=0.15, rvc=True).seed == 2
    # nothing passes: everything is ranked
    cands = [cand(1, speech_s=9.0), cand(2, speech_s=8.0)]
    assert rank.rank(cands, target_s=2.0, max_cer=0.15).seed == 2
    assert rank.length_fit(2.0, 2.0) == 1.0 and rank.length_fit(2.5, 2.0) == pytest.approx(0.0)
    # a fixed_length (cutscene) line weighs the length more than the expressiveness
    cands = [cand(1, prosody_sim=1.0, speech_s=1.7), cand(2, prosody_sim=0.5, speech_s=2.0)]
    assert rank.rank(cands, target_s=2.0, max_cer=0.15).seed == 1
    assert rank.rank(cands, target_s=2.0, max_cer=0.15, fixed_length=True).seed == 2


def test_rank_mos_gate_liveliness_and_register() -> None:
    def cand(seed: int, **kw: Any) -> rank.Candidate:
        return rank.Candidate(seed=seed, wav=f"c{seed}.wav", speech_s=kw.pop("speech_s", 2.0), **kw)

    # MOS gate: below the character's floor (median 3.5 - mos_drop 1.0) is out, even with the best voice
    cands = [cand(1, mos=2.0, sim=0.9), cand(2, mos=3.0, sim=0.5)]
    win = rank.rank(cands, target_s=2.0, max_cer=0.15, mos_floor=2.5)
    assert win.seed == 2 and cands[0].mos_gate is False and not cands[0].passed and cands[1].mos_gate is True
    assert rank.rank(cands, target_s=2.0, max_cer=0.15).seed == 1  # no floor: no gate
    assert cands[0].mos_gate is None
    # every candidate under the floor: the CER/length-gated ones are ranked, the MOS gate is waived
    cands = [cand(1, mos=1.5, sim=0.9), cand(2, mos=2.0, sim=0.2), cand(3, mos=4.0, speech_s=9.0)]
    assert rank.rank(cands, target_s=2.0, max_cer=0.15, mos_floor=2.5).seed == 1
    assert cands[2].score == 0.0  # outside the ranked pool

    # liveliness: a flatter take than the original loses, a livelier one does not gain
    orig = {"f0_range_st": 6.0, "rms_std_db": 4.0}
    assert rank.liveliness({"f0_range_st": 3.0, "rms_std_db": 4.0}, orig) == pytest.approx(0.75)
    assert rank.liveliness({"f0_range_st": 9.0, "rms_std_db": 8.0}, orig) == 1.0
    assert rank.liveliness({"f0_range_st": 3.0}, {"f0_range_st": 6.0, "rms_std_db": 0.0}) == 0.5
    assert rank.liveliness({}, orig) is None
    cands = [cand(1, liveliness=0.4), cand(2, liveliness=1.0)]
    assert rank.rank(cands, target_s=2.0, max_cer=0.15).seed == 2

    # F0-median term of the prosody score: 1 at the original's median, 0 at 6 semitones
    assert rank.f0_median_fit(0.0) == 1.0 and rank.f0_median_fit(-3.0) == 0.5 and rank.f0_median_fit(12.0) == 0.0
    cands = [cand(1, prosody_sim=0.8, f0_shift_st=5.0), cand(2, prosody_sim=0.7, f0_shift_st=0.5)]
    win = rank.rank(cands, target_s=2.0, max_cer=0.15)
    assert win.seed == 2 and cands[1].parts["prosody"] == pytest.approx((0.7 + 1 - 0.5 / 6) / 2)
    assert set(cands[0].summary()) >= {"seed", "speech_s", "cer", "sim", "mos", "emo_cos", "prosody_sim", "passed",
                                       "score", "f0_shift_st", "liveliness", "mos_gate"}


def test_rank_weights_setting() -> None:
    assert rank.effective_weights() == {"prosody": 0.25, "mos": 0.1, "liveliness": 0.2, "length": 0.15, "sim": 0.3}
    assert rank.effective_weights(rvc=True)["sim"] == 0.05
    fixed = rank.effective_weights(fixed_length=True)
    assert fixed == {"prosody": 0.175, "mos": 0.1, "liveliness": 0.125, "length": 0.3, "sim": 0.3}
    assert rank.weights_from({"sim": 0.5, "bogus": 1, "mos": "x"})["sim"] == 0.5
    assert rank.check_weights(None) is None and rank.check_weights({"sim": 0.4}) is None
    assert "unknown" in rank.check_weights({"timbre": 0.4})
    assert "from 0 to 1" in rank.check_weights({"sim": 1.5})
    assert "section" in rank.check_weights(0.3)
    zero = dict.fromkeys(rank.SCORE_KEYS, 0)
    assert "more than 0" in rank.check_weights(zero)
    # a project's weights change the winner
    cands = [rank.Candidate(1, "c1.wav", 2.0, sim=0.9, liveliness=0.2),
             rank.Candidate(2, "c2.wav", 2.0, sim=0.3, liveliness=1.0)]
    assert rank.rank(cands, target_s=2.0, max_cer=0.15).seed == 1
    assert rank.rank(cands, target_s=2.0, max_cer=0.15, weights={"sim": 0.05, "liveliness": 0.5}).seed == 2


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
