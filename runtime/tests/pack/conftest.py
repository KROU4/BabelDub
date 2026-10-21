"""Synthetic game + project + fake adapters for the pack stage tests (no real game assets)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from babeldub import cli
from babeldub.adapters import base
from babeldub.adapters.base import Adapter, Asset
from babeldub.commands import pack as pack_cmd
from babeldub.core import layout
from babeldub.core.ids import line_id, uid
from babeldub.core.jsonl import append_jsonl, write_json, write_jsonl
from babeldub.core.output import BabelDubError, emit_error
from babeldub.core.project import Project
from babeldub.pack import guards, planner

FAKE_MODULE = "babeldub_tests_fake_adapters"
BANK_REL = "Data/Dialogue.fbank"
CATALOG_REL = "Data/catalog.json"
VOICE_DIR = "Voice/en"


def sha1_of(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


# ---- fake adapters ----------------------------------------------------------------------
class FakeBankAdapter(Adapter):
    """A JSON 'bank' with numbered entries; optionally patches a shared catalog file."""

    name = "fakebank"
    level = "A"
    calls: list[str] = []
    verify_options: list[dict[str, Any]] = []

    def build(
        self,
        game_dir: Path,
        asset: Asset,
        replacements: dict[str, Path],
        out_path: Path,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        FakeBankAdapter.calls.append(asset.asset_id)
        bank = json.loads((game_dir / asset.relpath).read_text(encoding="utf-8"))
        for lid, wav in replacements.items():
            index = int(lid.rsplit(":", 1)[1])
            bank["entries"][index]["data"] = "dub:" + hashlib.sha1(wav.read_bytes()).hexdigest()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(bank), encoding="utf-8")
        codec = "pcm16" if options.get("codec") in (None, "auto") else options["codec"]
        result: dict[str, Any] = {
            "out": str(out_path),
            "replaced": len(replacements),
            "kept": len(bank["entries"]) - len(replacements),
            "codec": codec,
        }
        catalog_rel = asset.info.get("catalog")
        if catalog_rel:
            build_root = out_path
            for _ in PurePosixPath(asset.relpath).parts:
                build_root = build_root.parent
            catalog = json.loads((game_dir / catalog_rel).read_text(encoding="utf-8"))
            catalog["crc"][asset.relpath] = 0
            target = build_root / catalog_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(catalog), encoding="utf-8")
            result["files"] = [asset.relpath, catalog_rel]
        return result

    def verify(self, game_dir: Path, asset: Asset, built: Path, options: dict[str, Any]) -> dict[str, Any]:
        FakeBankAdapter.verify_options.append(options)
        orig = json.loads((game_dir / asset.relpath).read_text(encoding="utf-8"))
        new = json.loads(built.read_text(encoding="utf-8"))
        ok = len(orig["entries"]) == len(new["entries"])
        return {"ok": ok, "checks": ["parse", "count"], "problems": [] if ok else ["entry count differs"]}


class FakeDirAdapter(Adapter):
    """Loose files in a folder: each line is one file; a line may add a new file."""

    name = "fakedir"
    level = "A"
    calls: list[str] = []

    def build(
        self,
        game_dir: Path,
        asset: Asset,
        replacements: dict[str, Path],
        out_path: Path,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        FakeDirAdapter.calls.append(asset.asset_id)
        out_path.mkdir(parents=True, exist_ok=True)
        files = []
        for lid, wav in sorted(replacements.items()):
            name = lid.split("#", 1)[1].rsplit(":", 1)[0]
            (out_path / name).write_bytes(wav.read_bytes())
            files.append(f"{asset.relpath}/{name}")
        existing = len(list((game_dir / asset.relpath).iterdir()))
        return {
            "out": str(out_path),
            "files": files,
            "replaced": len(files),
            "kept": max(existing - len(files), 0),
            "codec": "pcm16",
        }

    def verify(self, game_dir: Path, asset: Asset, built: Path, options: dict[str, Any]) -> dict[str, Any]:
        missing = [f["relpath"] for f in options.get("files", []) if not Path(f["path"]).is_file()]
        return {"ok": built.is_dir() and not missing, "checks": ["files"], "problems": missing}


@pytest.fixture(autouse=True)
def fake_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType(FAKE_MODULE)
    module.FakeBankAdapter = FakeBankAdapter  # type: ignore[attr-defined]
    module.FakeDirAdapter = FakeDirAdapter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, FAKE_MODULE, module)
    monkeypatch.setitem(base.REGISTRY, "fakebank", f"{FAKE_MODULE}:FakeBankAdapter")
    monkeypatch.setitem(base.REGISTRY, "fakedir", f"{FAKE_MODULE}:FakeDirAdapter")
    FakeBankAdapter.calls = []
    FakeBankAdapter.verify_options = []
    FakeDirAdapter.calls = []


@pytest.fixture(autouse=True)
def no_running_game(monkeypatch: pytest.MonkeyPatch) -> None:
    """Process scanning is tested separately; command tests assume the game is closed."""
    monkeypatch.setattr(
        guards, "check_running", lambda game_dir: {"processes": [], "method": "mock", "error": None}
    )


# ---- running commands -------------------------------------------------------------------
def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    """Run one pack command like cli.main does and return (exit code, the single JSON object)."""
    parser = argparse.ArgumentParser(prog="babeldub")
    sub = parser.add_subparsers(dest="command")
    sub.required = True
    pack_cmd.register(sub, cli._common)
    args = parser.parse_args(list(argv))
    try:
        code = int(args.func(args))
    except BabelDubError as err:
        code = emit_error(err)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got {lines}"
    return code, json.loads(lines[0])


# ---- synthetic game + project -----------------------------------------------------------
def _write_wav(path: Path, freq: float, dur_s: float = 0.25, sr: int = 24000) -> None:
    t = np.arange(int(sr * dur_s), dtype=np.float32) / sr
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32), sr, subtype="PCM_16")


@dataclass
class Env:
    root: Path
    game: Path
    project: Project
    capsys: pytest.CaptureFixture[str]
    bank_lines: list[str]
    dir_lines: list[str]

    def run(self, *argv: str) -> tuple[int, dict[str, Any]]:
        return run(self.capsys, *argv, "--project", str(self.root))

    def game_sha(self, rel: str) -> str | None:
        path = self.game / rel
        return sha1_of(path) if path.is_file() else None

    def manifest(self) -> dict[str, Any]:
        return json.loads((self.root / layout.BUILD_MANIFEST).read_text(encoding="utf-8"))

    def install_state(self) -> dict[str, Any]:
        return json.loads((self.root / layout.INSTALL_LOG).read_text(encoding="utf-8"))

    def progress(self) -> dict[str, Any]:
        return Project.load(str(self.root)).progress()

    def decide(self, ids: list[str], decision: str = "accept", attempt: int = 1) -> None:
        append_jsonl(
            self.root / layout.DECISIONS,
            [
                {"id": i, "decision": decision, "attempt": attempt, "by": "agent", "note": "", "ts": "t"}
                for i in ids
            ],
        )

    def redub(self, lid: str, freq: float) -> None:
        """New processed audio for a line (as if it was regenerated and accepted again)."""
        _write_wav(self.root / layout.POST_DIR / f"{uid(lid)}.wav", freq)

    def ready(self) -> None:
        """build + verify, asserting both succeed."""
        code, out = self.run("build")
        assert code == 0, out
        code, out = self.run("verify")
        assert code == 0, out


def make_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    catalog: bool = False,
) -> Env:
    home = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    monkeypatch.delenv("BABELDUB_PROJECT", raising=False)
    game = tmp_path / "Game Folder"
    bank = {"format": "fakebank", "entries": [{"index": i, "data": f"orig{i}"} for i in range(4)]}
    (game / "Data").mkdir(parents=True)
    (game / BANK_REL).write_text(json.dumps(bank), encoding="utf-8")
    (game / CATALOG_REL).write_text(json.dumps({"crc": {BANK_REL: 123456}}), encoding="utf-8")
    (game / VOICE_DIR).mkdir(parents=True)
    (game / VOICE_DIR / "a.wav").write_bytes(b"RIFF-original-a")
    (game / VOICE_DIR / "b.wav").write_bytes(b"RIFF-original-b")
    (game / "Game.exe").write_bytes(b"MZ not a real program")

    root = home / "projects" / "test"
    project = Project.create(
        root, {"slug": "test", "game_name": "Test", "game_dir": str(game), "target_lang": "ru"}
    )
    bank_asset = Asset(
        asset_id=f"fakebank:{BANK_REL}",
        adapter="fakebank",
        relpath=BANK_REL,
        role="voice",
        size=(game / BANK_REL).stat().st_size,
        level="A",
        info={"catalog": CATALOG_REL} if catalog else {},
    )
    dir_asset = Asset(
        asset_id=f"fakedir:{VOICE_DIR}", adapter="fakedir", relpath=VOICE_DIR, role="voice", level="A"
    )
    write_jsonl(root / layout.ASSETS, [bank_asset.to_row(), dir_asset.to_row()], schema="babeldub.assets/1")

    bank_lines = [line_id("fakebank", BANK_REL, "main", i) for i in range(3)]
    dir_lines = [line_id("fakedir", VOICE_DIR, name, 0) for name in ("a.wav", "b.wav", "c.wav")]
    rows = []
    for lid in bank_lines:
        rows.append(
            {
                "id": lid,
                "uid": uid(lid),
                "adapter": "fakebank",
                "asset_id": bank_asset.asset_id,
                "kind": "dialogue",
            }
        )
    for lid in dir_lines:
        rows.append(
            {
                "id": lid,
                "uid": uid(lid),
                "adapter": "fakedir",
                "asset_id": dir_asset.asset_id,
                "kind": "dialogue",
            }
        )
    write_jsonl(root / layout.LINES, rows, schema="babeldub.lines/1")

    post, synth = [], []
    for n, lid in enumerate(bank_lines + dir_lines):
        wav = root / layout.POST_DIR / f"{uid(lid)}.wav"
        _write_wav(wav, 200.0 + 50 * n)
        post.append({"id": lid, "attempt": 1, "wav": f"{layout.POST_DIR}/{uid(lid)}.wav"})
        synth.append(
            {
                "id": lid,
                "attempt": 1,
                "engine": "qwen3tts",
                "model": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                "status": "ok",
            }
        )
    write_jsonl(root / layout.POST, post, schema="babeldub.post/1")
    write_jsonl(root / layout.SYNTH_RESULTS, synth, schema="babeldub.synth/1")
    write_jsonl(root / layout.DECISIONS, [], schema="babeldub.decisions/1")

    catalog_yaml = tmp_path / "models.yaml"
    catalog_yaml.write_text(
        "models:\n"
        "  - name: Qwen3-TTS-12Hz-1.7B-Base\n"
        "    repo: Qwen/Qwen3-TTS-12Hz-1.7B-Base\n"
        "    engine: qwen3tts\n"
        "    license: Apache-2.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(planner, "models_catalog_path", lambda: catalog_yaml)
    write_json(
        root / layout.RECON, {"engine": "unity", "middleware": ["fmod"], "warnings": ["no anti-cheat found"]}
    )
    return Env(
        root=root, game=game, project=project, capsys=capsys, bank_lines=bank_lines, dir_lines=dir_lines
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Env:
    e = make_env(tmp_path, monkeypatch, capsys)
    e.decide(e.bank_lines + e.dir_lines)
    return e
