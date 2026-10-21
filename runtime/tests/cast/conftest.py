"""Synthetic projects and a fake qc worker for cast tests (no game assets, no torch)."""

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


def write_voice(path: Path, dur_s: float, freq: float = 180.0, amp: float = 0.2, seed: int = 0) -> None:
    """Speech-like test signal: modulated tone plus a little noise."""
    t = np.arange(int(dur_s * SR)) / SR
    rng = np.random.default_rng(seed)
    sig = amp * np.sin(2 * np.pi * freq * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    sig = sig + 0.005 * rng.standard_normal(t.size)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), sig.astype(np.float32), SR)


class FakeQc:
    """Stands in for WorkerClient running qc_worker.py: embeddings and ASR text by file path."""

    def __init__(self) -> None:
        self.vectors: dict[str, np.ndarray] = {}
        self.texts: dict[str, str] = {}
        self.loads: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.closed = 0
        self.restarts = 0
        self.max_restarts = 3

    @staticmethod
    def key(path: Path | str) -> str:
        return str(Path(path).resolve()).lower()

    def load(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.loads.append(msg)
        return {"ok": True, "op": "load", "device": msg.get("device", "cpu"), "info": {}}

    def request(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(msg)
        key = self.key(msg["wav"])
        if msg["op"] == "embed":
            vec = self.vectors.get(key)
            if vec is None:
                return {"ok": False, "op": "embed", "id": msg.get("id"), "error": "decode error"}
            return {"ok": True, "op": "embed", "id": msg.get("id"), "emb": [float(x) for x in vec]}
        if msg["op"] == "asr":
            return {"ok": True, "op": "asr", "id": msg.get("id"), "text": self.texts.get(key, "")}
        return {"ok": False, "op": msg["op"], "error": "unsupported"}

    def close(self) -> None:
        self.closed += 1


class Voices:
    """Random but fixed speaker centroids; line embeddings scatter around them."""

    def __init__(self, seed: int = 7) -> None:
        self.rng = np.random.default_rng(seed)
        self.centroids: dict[str, np.ndarray] = {}

    def emb(self, voice: str, spread: float = 0.35) -> np.ndarray:
        if voice not in self.centroids:
            c = self.rng.standard_normal(DIM)
            self.centroids[voice] = c / np.linalg.norm(c)
        noise = self.rng.standard_normal(DIM)
        v = self.centroids[voice] + spread * noise / np.linalg.norm(noise)
        return (v / np.linalg.norm(v)).astype(np.float32)


class LineFactory:
    def __init__(self, project: Project, qc: FakeQc | None, voices: Voices) -> None:
        self.project = project
        self.qc = qc
        self.voices = voices
        self.lines: list[dict[str, Any]] = []

    def add(
        self,
        speaker: str | None,
        dur_s: float,
        text: str | None = "Some line of dialogue.",
        flags: tuple[str, ...] = (),
        voice: str | None = None,
        kind: str = "dialogue",
        amp: float = 0.2,
        fixed_length: bool = False,
    ) -> dict[str, Any]:
        idx = len(self.lines)
        lid = line_id("fmod", "Data/Dialogue.bank", "fsb0", idx)
        u = uid(lid)
        rel = f"{layout.ORIG_AUDIO_DIR}/{u}.wav"
        wav = self.project.path(rel)
        write_voice(wav, dur_s, amp=amp, seed=idx)
        line = {
            "id": lid,
            "uid": u,
            "adapter": "fmod",
            "asset_id": "fmod:Data/Dialogue.bank",
            "entry": {"key": f"dlg_{idx:03d}", "sub": "fsb0", "index": idx},
            "kind": kind,
            "locale": "en-US",
            "audio": {"sr": SR, "ch": 1, "dur_s": dur_s, "codec": "pcm", "wav": rel},
            "speaker_hint": speaker,
            "scene": "s1",
            "order": idx,
            "fixed_length": fixed_length,
            "text": {"key": f"dlg_{idx:03d}", "src": text, "src_lang": "en"} if text else None,
            "flags": list(flags),
        }
        if self.qc is not None:
            self.qc.vectors[FakeQc.key(wav)] = self.voices.emb(voice or speaker or "anon")
        self.lines.append(line)
        return line

    def save(self) -> None:
        write_jsonl(self.project.path(layout.LINES), self.lines, schema="babeldub.lines/1")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    monkeypatch.delenv("BABELDUB_PROJECT", raising=False)
    return home


@pytest.fixture
def project(home: Path) -> Project:
    root = home / "projects" / "demo"
    p = Project.create(
        root,
        {
            "slug": "demo",
            "game_name": "Demo Game",
            "game_dir": str(home / "game"),
            "target_lang": "ru",
            "source_lang": "en",
            "compute": {"device": "cpu"},
        },
    )
    p.mark_stage("extract", "done")
    return p


def _patch_missing(monkeypatch: pytest.MonkeyPatch, value: list[str]) -> None:
    import babeldub.commands.cast as cast_cmd
    import babeldub.qa.qc as qc

    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: list(value))
    monkeypatch.setattr(cast_cmd, "qc_missing", lambda *a, **k: list(value))


@pytest.fixture
def fake_qc(monkeypatch: pytest.MonkeyPatch) -> FakeQc:
    import babeldub.qa.qc as qc

    fake = FakeQc()
    monkeypatch.setattr(qc, "_make_client", lambda log_path: fake)
    _patch_missing(monkeypatch, [])
    return fake


@pytest.fixture
def no_qc(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_missing(monkeypatch, ["env:qc"])


@pytest.fixture
def voices() -> Voices:
    return Voices()


@pytest.fixture
def make_lines(project: Project, voices: Voices):
    """make_lines(qc) -> LineFactory writing synthetic lines (and their fake embeddings)."""
    return lambda qc: LineFactory(project, qc, voices)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project")
    parser.add_argument("--pretty", action="store_true")


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]):
    """Run `babeldub <argv>` through the cast/qa parsers; returns (exit code, JSON payload)."""
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
