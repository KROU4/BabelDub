"""Synthetic dubbed project and a fake qc worker for QA tests (no game assets, no torch)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from babeldub.core import layout
from babeldub.core.ids import line_id, uid
from babeldub.core.jsonl import write_jsonl
from babeldub.core.output import BabelDubError, emit_error, set_pretty
from babeldub.core.project import Project

SR = 16000
DIM = 192


def write_voice(path: Path, dur_s: float, amp: float = 0.2, seed: int = 0) -> None:
    t = np.arange(int(dur_s * SR)) / SR
    rng = np.random.default_rng(seed)
    sig = amp * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    sig = sig + (0.005 * amp / 0.2) * rng.standard_normal(t.size)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), sig.astype(np.float32), SR)


def key(path: Path | str) -> str:
    return str(Path(path).resolve()).lower()


class FakeQc:
    def __init__(self) -> None:
        self.vectors: dict[str, np.ndarray] = {}
        self.texts: dict[str, str] = {}
        self.prosody: dict[str, dict[str, Any]] = {}
        self.mos: dict[str, float] = {}
        self.fail: set[str] = set()
        self.loads: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.closed = 0
        self.restarts = 0
        self.max_restarts = 3

    def load(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.loads.append(msg)
        return {"ok": True, "op": "load", "device": msg.get("device", "cpu"), "info": {}}

    def request(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(msg)
        if "wav" not in msg:  # text ops (spell): numbers stay as they are
            return {"ok": True, "op": msg["op"], "texts": list(msg.get("texts") or [])}
        k = key(msg["wav"])
        if k in self.fail:
            return {"ok": False, "op": msg["op"], "id": msg.get("id"), "error": "decode error"}
        if msg["op"] == "embed":
            vec = self.vectors.get(k)
            if vec is None:
                return {"ok": False, "op": "embed", "id": msg.get("id"), "error": "no embedding"}
            return {"ok": True, "op": "embed", "id": msg.get("id"), "emb": [float(x) for x in vec]}
        if msg["op"] == "asr":
            return {"ok": True, "op": "asr", "id": msg.get("id"), "text": self.texts.get(k, "")}
        if msg["op"] == "prosody" and k in self.prosody:
            return {"ok": True, "op": "prosody", "id": msg.get("id"), **self.prosody[k]}
        if msg["op"] == "mos" and k in self.mos:
            return {"ok": True, "op": "mos", "id": msg.get("id"), "mos": self.mos[k]}
        return {"ok": False, "op": msg["op"], "error": "unsupported"}

    def close(self) -> None:
        self.closed += 1


class Voices:
    def __init__(self, seed: int = 11) -> None:
        self.rng = np.random.default_rng(seed)
        self.centroids: dict[str, np.ndarray] = {}

    def centroid(self, voice: str) -> np.ndarray:
        if voice not in self.centroids:
            c = self.rng.standard_normal(DIM)
            self.centroids[voice] = c / np.linalg.norm(c)
        return self.centroids[voice]

    def emb(self, voice: str, spread: float = 0.35) -> np.ndarray:
        noise = self.rng.standard_normal(DIM)
        v = self.centroid(voice) + spread * noise / np.linalg.norm(noise)
        return (v / np.linalg.norm(v)).astype(np.float32)

    def mix(self, weights: dict[str, float]) -> np.ndarray:
        """A voice between speakers, e.g. {"hero": 0.6, "villain": 0.8} (random voices are ~orthogonal)."""
        v = sum(w * self.centroid(name) for name, w in weights.items())
        return (v / np.linalg.norm(v)).astype(np.float32)


class DubProject:
    """Project after `post`: originals, cast, translations and post files of one or more attempts."""

    def __init__(self, project: Project, qc: FakeQc, voices: Voices) -> None:
        self.project = project
        self.qc = qc
        self.voices = voices
        self.lines: list[dict[str, Any]] = []
        self.assign: list[dict[str, Any]] = []
        self.translations: list[dict[str, Any]] = []
        self.post: list[dict[str, Any]] = []
        self.refs: list[dict[str, Any]] = []

    def line(self, char: str, dur_s: float = 3.0, fixed_length: bool = False, flags: tuple[str, ...] = (),
             vec: np.ndarray | None = None) -> str:
        idx = len(self.lines)
        lid = line_id("fmod", "Data/Dialogue.bank", "fsb0", idx)
        u = uid(lid)
        rel = f"{layout.ORIG_AUDIO_DIR}/{u}.wav"
        write_voice(self.project.path(rel), dur_s, seed=idx)
        self.qc.vectors[key(self.project.path(rel))] = self.voices.emb(char) if vec is None else vec
        self.lines.append(
            {
                "id": lid, "uid": u, "kind": "dialogue", "locale": "en-US",
                "audio": {"sr": SR, "ch": 1, "dur_s": dur_s, "wav": rel},
                "speaker_hint": char, "fixed_length": fixed_length,
                "text": {"src": f"Original line {idx}.", "src_lang": "en"}, "flags": list(flags),
            }
        )
        self.assign.append({"id": lid, "char_id": char, "conf": 1.0})
        return lid

    def translate(self, lid: str, text: str) -> None:
        self.translations.append({"id": lid, "dub_text": text, "lang": "ru", "by": "agent", "rev": 1})

    def dub(self, lid: str, attempt: int = 1, voice: str | None = None, asr: str | None = None,
            dur_s: float = 3.0, ratio: float = 1.0, amp: float = 0.2, vec: np.ndarray | None = None) -> Path:
        line = next(x for x in self.lines if x["id"] == lid)
        rel = f"{layout.POST_DIR}/{line['uid']}.wav"
        wav = self.project.path(rel)
        write_voice(wav, dur_s, amp=amp, seed=100 + attempt)
        if vec is not None:
            self.qc.vectors[key(wav)] = vec
        elif voice is not None:
            self.qc.vectors[key(wav)] = self.voices.emb(voice)
        if asr is not None:
            self.qc.texts[key(wav)] = asr
        self.post.append({"id": lid, "attempt": attempt, "wav": rel, "dur_s": dur_s,
                          "target_s": line["audio"]["dur_s"], "ratio": ratio, "peak_db": -3.0})
        return wav

    def ref(self, char: str, lid: str) -> None:
        line = next(x for x in self.lines if x["id"] == lid)
        self.refs.append({"ref_id": f"{char}:{line['uid']}", "char_id": char, "line_id": lid,
                          "wav": line["audio"]["wav"], "text": line["text"]["src"], "lang": "en",
                          "dur_s": line["audio"]["dur_s"], "score": 1.0, "chosen": True})

    def save(self) -> None:
        p = self.project
        write_jsonl(p.path(layout.LINES), self.lines, schema="babeldub.lines/1")
        write_jsonl(p.path(layout.ASSIGN), self.assign, schema="babeldub.assign/1")
        write_jsonl(p.path(layout.REFS), self.refs, schema="babeldub.refs/1")
        write_jsonl(p.path(layout.TRANSLATIONS), self.translations, schema="babeldub.translations/1")
        write_jsonl(p.path(layout.POST), self.post, schema="babeldub.post/1")
        chars = sorted({a["char_id"] for a in self.assign})
        write_jsonl(p.path(layout.CHARACTERS), [{"char_id": c, "name": c.title(), "selected": True} for c in chars],
                    schema="babeldub.characters/1")
        for stage in ("extract", "cast", "post"):
            p.mark_stage(stage, "done")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    monkeypatch.delenv("BABELDUB_PROJECT", raising=False)
    return home


@pytest.fixture
def project(home: Path) -> Project:
    return Project.create(
        home / "projects" / "demo",
        {"slug": "demo", "game_name": "Demo Game", "game_dir": str(home / "game"),
         "target_lang": "ru", "source_lang": "en", "compute": {"device": "cpu"}},
    )


@pytest.fixture
def fake_qc(monkeypatch: pytest.MonkeyPatch) -> FakeQc:
    import babeldub.commands.cast as cast_cmd
    import babeldub.qa.qc as qc

    fake = FakeQc()
    monkeypatch.setattr(qc, "_make_client", lambda log_path: fake)
    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: [])
    monkeypatch.setattr(cast_cmd, "qc_missing", lambda *a, **k: [])
    return fake


@pytest.fixture
def dub(project: Project, fake_qc: FakeQc) -> DubProject:
    return DubProject(project, fake_qc, Voices())


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project")
    parser.add_argument("--pretty", action="store_true")


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]):
    from babeldub.commands import cast as cast_cmd
    from babeldub.commands import qa as qa_cmd

    def _run(argv: list[str]) -> tuple[int, dict[str, Any]]:
        parser = argparse.ArgumentParser(prog="babeldub")
        sub = parser.add_subparsers(dest="command")
        cast_cmd.register(sub, _common)
        qa_cmd.register(sub, _common)
        args = parser.parse_args(argv)
        set_pretty(False)
        capsys.readouterr()
        try:
            code = int(args.func(args))
        except BabelDubError as err:
            code = emit_error(err)
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1, f"expected exactly one JSON line, got {out!r}"
        return code, json.loads(out[0])

    return _run
