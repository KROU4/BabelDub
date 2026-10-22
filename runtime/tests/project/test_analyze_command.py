"""`babeldub analyze` with a fake qc worker: measurements, classes, subtitle_mismatch, idempotence."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

import babeldub.qa.qc as qc
from babeldub.analysis.store import emotion_vectors, load_analysis
from babeldub.core import layout
from babeldub.core.jsonl import latest_by, read_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_ENV, EXIT_OK, EXIT_USAGE


class FakeQc:
    """Answers by the wav's uid: `heard` maps uid -> what the ASR of the original hears."""

    restarts = 0
    max_restarts = 3

    def __init__(self, heard: dict[str, str], speech: dict[str, float]) -> None:
        self.heard = heard
        self.speech = speech
        self.requests: list[dict[str, Any]] = []
        self.loads: list[dict[str, Any]] = []
        self.fail: set[str] = set()

    def load(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.loads.append(msg)
        return {"ok": True, "op": "load", "device": msg.get("device", "cpu"), "info": {}}

    def request(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(msg)
        uid = Path(msg["wav"]).stem
        if uid in self.fail:
            return {"ok": False, "op": msg["op"], "error": "decode error"}
        speech = self.speech.get(uid, 2.0)
        if msg["op"] == "vad":
            return {"ok": True, "segments": [[0.1, 0.1 + speech]], "speech_s": speech, "dur_s": speech + 0.2}
        if msg["op"] == "asr_words":
            text = self.heard.get(uid, "")
            words = [{"w": w, "start": 0.1, "end": 0.4, "prob": 0.9} for w in text.split()]
            return {"ok": True, "text": text, "lang": "en", "words": words, "avg_logprob": -0.3,
                    "no_speech_prob": 0.02}
        if msg["op"] == "prosody":
            return {"ok": True, "f0_median_hz": 150.0, "f0_range_st": 6.5, "rms_db": -21.0, "voiced_ratio": 0.7}
        if msg["op"] == "emotion":
            return {"ok": True, "label": "happy", "score": 0.8, "emb": [0.1, 0.2, 0.3]}
        return {"ok": False, "error": f"unexpected op {msg['op']}"}

    def close(self) -> None:
        return None


def _write_wavs(project, lines, seconds: float = 1.0) -> None:
    for ln in lines:
        path = project.path(ln["audio"]["wav"])
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), np.zeros(int(16000 * seconds), dtype=np.float32), 16000)


@pytest.fixture
def setup(project_with_lines, line, monkeypatch):
    def build(specs: list[dict[str, Any]], heard: dict[int, str], speech: dict[int, float] | None = None):
        lines = [line(**spec) for spec in specs]
        project = project_with_lines(lines)
        _write_wavs(project, lines)
        fake = FakeQc({lines[i]["uid"]: t for i, t in heard.items()},
                      {lines[i]["uid"]: s for i, s in (speech or {}).items()})
        monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: [])
        monkeypatch.setattr(qc, "_make_client", lambda log_path: fake)
        return project, lines, fake

    return build


def test_analyze_classes_rows_and_mismatch_flag(bd, setup):
    project, lines, fake = setup(
        [
            {"index": 1, "src": "Okay, let's see what we have here."},
            {"index": 2, "src": "Hehe..."},
            {"index": 3, "src": "Freeze!"},
            {"index": 4, "src": "Haha, okay, you got me.", "flags": ["nonverbal"]},
            {"index": 5, "src": "Where is the hammer?"},  # the audio says something else
            {"index": 6, "src": None},  # no subtitle, the ASR hears nothing: a grunt
            {"index": 7, "src": "Beep.", "kind": "sfx"},  # not voiced: never analyzed
        ],
        heard={0: "Okay, let's see what we have here.", 1: "Hehehe.", 2: "Freeze!", 3: "Haha okay you got me",
               4: "I told you to leave the truck alone", 5: ""},
        speech={2: 0.6},
    )
    code, out = bd("analyze")
    assert code == EXIT_OK, out
    assert out["analyzed"] == 6 and out["lines"] == 6 and out["failed"] == 0
    assert out["classes"] == {"speech": 2, "short_word": 1, "interjection": 1, "nonverbal": 1, "mixed": 1}
    assert out["subtitle_mismatch"] == 1 and out["mismatch_ids"] == [lines[4]["id"]]
    assert out["emotion"].startswith("prosody only") and out["next"] == "babeldub cast"
    assert {r["op"] for r in fake.requests} == {"vad", "asr_words", "prosody"}
    assert fake.loads[0].get("asr_model") and "sv_model" not in fake.loads[0]  # vad+asr run: no ECAPA
    rows = load_analysis(project)
    first = rows[lines[0]["id"]]
    assert first["class"] == "speech" and first["asr_vs_sub_cer"] == 0.0 and first["asr_conf"] == 0.9
    assert first["vad_segments"] == [[0.1, 2.1]] and first["speech_s"] == 2.0 and first["uid"] == lines[0]["uid"]
    assert first["prosody"]["f0_range_st"] == 6.5 and first["prosody"]["syl_rate"] > 0
    assert first["emotion"] is None and first["wav_sha1"]
    assert rows[lines[2]["id"]]["class"] == "short_word"
    assert rows[lines[5]["id"]]["class"] == "nonverbal"
    canon = latest_by(read_jsonl(project.path(layout.LINES)))
    assert "subtitle_mismatch" in canon[lines[4]["id"]]["flags"]
    assert all("subtitle_mismatch" not in canon[ln["id"]]["flags"] for ln in lines if ln is not lines[4])
    assert canon[lines[3]["id"]]["flags"] == ["nonverbal"]  # other flags and fields untouched
    assert canon[lines[0]["id"]]["text"] == lines[0]["text"]
    assert project.progress()["analyze"]["status"] == "done"


def test_analyze_is_idempotent_and_follows_text_and_wav_changes(bd, setup):
    project, lines, fake = setup(
        [{"index": 1, "src": "Where is the hammer?"}, {"index": 2, "src": "Fine."}],
        heard={0: "I told you to leave the truck alone", 1: "Fine."},
    )
    assert bd("analyze")[1]["subtitle_mismatch"] == 1
    n = len(fake.requests)
    code, out = bd("analyze")
    assert code == EXIT_OK and out["analyzed"] == 0 and out["up_to_date"] == 2 and len(fake.requests) == n
    # the subtitle is fixed (link/agent): the class and the flag follow without measuring again
    from babeldub.core.jsonl import write_jsonl

    canon = list(latest_by(read_jsonl(project.path(layout.LINES))).values())
    canon[0]["text"]["src"] = "I told you to leave the truck alone"
    write_jsonl(project.path(layout.LINES), canon, schema="babeldub.lines/1")
    code, out = bd("analyze")
    assert out["subtitle_mismatch"] == 0 and out["analyzed"] == 0 and out["flags_changed"] == 1
    assert "subtitle_mismatch" not in latest_by(read_jsonl(project.path(layout.LINES)))[lines[0]["id"]]["flags"]
    # a touched but identical wav is reused (sha1); a changed one is measured again
    wav = project.path(lines[1]["audio"]["wav"])
    os.utime(wav, ns=(1, 1))
    assert bd("analyze")[1]["analyzed"] == 0
    sf.write(str(wav), np.ones(8000, dtype=np.float32) * 0.1, 16000)
    code, out = bd("analyze")
    assert out["analyzed"] == 1 and out["up_to_date"] == 1
    assert bd("analyze", "--force")[1]["analyzed"] == 2


def test_analyze_ids_limit_and_errors(bd, setup):
    project, lines, fake = setup(
        [{"index": 1}, {"index": 2}, {"index": 3}], heard={0: "Okay, let's see.", 1: "Okay, let's see.",
                                                            2: "Okay, let's see."},
    )
    code, out = bd("analyze", "--ids", lines[1]["uid"])
    assert code == EXIT_OK and out["analyzed"] == 1 and out["lines"] == 1
    assert "analyze" not in project.progress()  # a partial run does not finish the stage
    assert bd("analyze", "--ids", "nope")[0] == EXIT_USAGE
    fake.fail.add(lines[2]["uid"])
    code, out = bd("analyze", "--limit", "1")
    assert code == EXIT_ATTENTION and out["analyzed"] == 1 and out["remaining"] == 1, out
    assert project.progress()["analyze"]["status"] == "attention"
    code, out = bd("analyze")
    assert code == EXIT_ATTENTION and out["failed"] == 1 and "decode error" in out["errors"][0]


def test_analyze_stores_emotion_vectors_when_the_opt_in_model_exists(bd, setup, home):
    project, lines, fake = setup([{"index": 1}], heard={0: "Okay, let's see."})
    folder = home / "models" / qc.DEFAULT_EMOTION_MODEL
    folder.mkdir(parents=True)
    (folder / "model.pt").write_bytes(b"x")
    code, out = bd("analyze")
    assert code == EXIT_OK and out["emotion"] == "emotion2vec+", out
    assert fake.loads[0]["emotion_model"] == str(folder)
    row = load_analysis(project)[lines[0]["id"]]
    assert row["emotion"] == {"label": "happy", "score": 0.8}
    vec = emotion_vectors(project)[lines[0]["uid"]]
    assert np.allclose(vec, [0.1, 0.2, 0.3])


def test_analyze_needs_the_qc_env(bd, project_with_lines, line):
    project = project_with_lines([line(1)])
    _write_wavs(project, [line(1)])
    code, out = bd("analyze")
    assert code == EXIT_ENV and "setup --engines qc" in out["hint"]
    assert "analyze" not in project.progress()


def test_analyze_limit_counts_stale_lines_as_remaining(bd, setup):
    project, lines, fake = setup(
        [{"index": 1}, {"index": 2}, {"index": 3}], heard={i: "Okay, let's see." for i in range(3)},
    )
    assert bd("analyze")[0] == EXIT_OK
    for ln in lines:  # re-extract: every wav changed, every row is stale (but still there)
        sf.write(str(project.path(ln["audio"]["wav"])), np.ones(8000, dtype=np.float32) * 0.1, 16000)
    code, out = bd("analyze", "--limit", "1")
    assert code == EXIT_ATTENTION and out["analyzed"] == 1 and out["remaining"] == 2, out
    assert project.progress()["analyze"]["status"] == "attention"
    assert out["next"].startswith("`babeldub analyze` again")
    code, out = bd("analyze")
    assert code == EXIT_OK and out["analyzed"] == 2 and "remaining" not in out
    assert project.progress()["analyze"]["status"] == "done"
