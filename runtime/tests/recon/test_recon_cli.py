"""scan -> extract -> link end to end on a synthetic Ren'Py-like game with loose voice files."""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from babeldub.adapters import base
from babeldub.adapters.base import Adapter, Asset
from babeldub.commands import recon as recon_cmd
from babeldub.core import layout
from babeldub.core.jsonl import read_json, read_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_OK, EXIT_USAGE, BabelDubError, emit_error
from babeldub.core.project import Project


class ScriptTextAdapter(Adapter):
    """Test-only text adapter: game/strings.txt with `key|lang|text` rows."""

    name = "scripttext"
    level = "A"

    def discover(self, game_dir: Path) -> list[Asset]:
        path = game_dir / "game" / "strings.txt"
        if not path.exists():
            return []
        return [
            Asset(
                asset_id="scripttext:game/strings.txt",
                adapter=self.name,
                relpath="game/strings.txt",
                role="text",
                size=path.stat().st_size,
                level="A",
            )
        ]

    def read_texts(self, game_dir: Path, asset: Asset) -> list[dict[str, Any]]:
        rows = []
        for raw in (game_dir / asset.relpath).read_text(encoding="utf-8").splitlines():
            key, lang, value = raw.split("|", 2)
            rows.append(
                {
                    "key": key,
                    "table": "script",
                    "locale": lang,
                    "lang": lang,
                    "text": value,
                    "source": "scripttext",
                }
            )
        return rows


class BrokenAdapter(Adapter):
    name = "broken"

    def discover(self, game_dir: Path) -> list[Asset]:
        raise RuntimeError("parser exploded")


def _tone(path: Path, seconds: float, sr: int = 16000, channels: int = 1, **kw: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * sr)) / sr
    data = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    sf.write(str(path), np.repeat(data[:, None], channels, axis=1), sr, **kw)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    home = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    monkeypatch.delenv("BABELDUB_PROJECT", raising=False)
    fake = types.ModuleType("babeldub_test_fake_adapters")
    fake.ScriptTextAdapter = ScriptTextAdapter  # type: ignore[attr-defined]
    fake.BrokenAdapter = BrokenAdapter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "babeldub_test_fake_adapters", fake)
    monkeypatch.setattr(
        base,
        "REGISTRY",
        {
            "unity-localization": "babeldub.adapters.unity.adapter:UnityLocalizationAdapter",
            "wwise": "babeldub.adapters.wwise.adapter:WwiseAdapter",
            "loose": "babeldub.adapters.loose.adapter:LooseAudioAdapter",
            "scripttext": "babeldub_test_fake_adapters:ScriptTextAdapter",
        },
    )
    game = tmp_path / "Demo Game"
    (game / "renpy").mkdir(parents=True)
    (game / "renpy" / "__init__.py").write_text("# renpy\n", encoding="utf-8")
    (game / "game").mkdir()
    (game / "game" / "script.rpyc").write_bytes(b"RENPY RPC2")
    (game / "game" / "archive.rpa").write_bytes(b"RPA-3.0 0 0\n")
    _tone(game / "game" / "voice" / "eileen" / "e001.ogg", 1.0, format="OGG", subtype="VORBIS")
    _tone(game / "game" / "voice" / "eileen" / "e002_v2.ogg", 0.5, format="OGG", subtype="VORBIS")
    _tone(game / "game" / "voice" / "lucy" / "l001.wav", 0.75, sr=22050, channels=2)
    _tone(game / "game" / "music" / "theme.ogg", 2.0, format="OGG", subtype="VORBIS")
    (game / "game" / "strings.txt").write_text(
        "e001|en|{i}Hello{/i} there.\ne001|ru|Привет.\ne002|en|Second line.\nl001|en|Lucy speaks.\n"
        "l001|ru|Люси говорит.\nmenu|en|Start\n",
        encoding="utf-8",
    )
    project = Project.create(
        home / "projects" / "demo",
        {
            "slug": "demo",
            "game_name": "Demo Game",
            "game_dir": str(game),
            "target_lang": "ru",
            "source_lang": None,
        },
    )
    return {"home": home, "game": game, "project": project}


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    parser = argparse.ArgumentParser(prog="babeldub")
    sub = parser.add_subparsers(dest="command")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project")
        p.add_argument("--pretty", action="store_true")

    recon_cmd.register(sub, common)
    args = parser.parse_args(argv)
    try:
        code = args.func(args)
    except BabelDubError as err:
        code = emit_error(err)
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1, out  # exactly one JSON object on stdout
    return code, json.loads(out[0])


def test_scan_extract_link(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    project: Project = env["project"]
    code, out = run(["extract", "--project", "demo"], capsys)
    assert code == EXIT_USAGE and "scan" in out["error"]

    code, out = run(["scan", "--project", "demo"], capsys)
    assert code == EXIT_OK, out
    assert out["engine"]["id"] == "renpy"
    assert out["level"] == "A"
    assert any(v.startswith("loose:game/voice") for v in out["voice_assets"])
    assert out["counts"] == {"voice": 1, "text": 1, "audio": 1, "other": 0}
    assets = read_jsonl(project.path(layout.ASSETS))
    voice = next(a for a in assets if a["role"] == "voice")
    assert voice["sha1"] and voice["info"]["sha1_of"] == "listing"
    text_asset = next(a for a in assets if a["role"] == "text")
    assert len(text_asset["sha1"]) == 40
    recon = read_json(project.path(layout.RECON))
    assert recon["engine"]["id"] == "renpy" and recon["adapters"]["loose"]["assets"] == 2
    report = project.path(layout.INVENTORY_REPORT).read_text(encoding="utf-8")
    assert "# Recon: Demo Game" in report and "Voice assets" in report and "game/voice" in report
    assert project.progress()["scan"]["status"] == "done"

    code, out = run(["extract", "--project", "demo", "--limit", "1"], capsys)
    assert code == EXIT_OK, out
    assert out["lines"] == 1 and out["decoded"] == 1 and out["skipped"]["limit"] == 2

    code, out = run(["extract", "--project", "demo"], capsys)
    assert code == EXIT_OK, out
    assert out["lines"] == 3 and out["reused"] == 1 and out["decoded"] == 2
    assert out["by_kind"] == {"dialogue": 3}
    assert out["texts"] == 6 and out["locales"] == ["en", "ru"]
    lines = read_jsonl(project.path(layout.LINES))
    assert [ln["entry"]["key"] for ln in lines] == ["e001", "e002_v2", "l001"]
    assert {ln["speaker_hint"] for ln in lines} == {"eileen", "lucy"}
    for ln in lines:
        wav = project.path(ln["audio"]["wav"])
        assert wav.exists() and sf.info(str(wav)).frames > 0
    lucy = lines[2]
    assert lucy["audio"]["sr"] == 22050 and lucy["audio"]["ch"] == 2 and lucy["audio"]["codec"] == "pcm16"

    code, out = run(["extract", "--project", "demo"], capsys)
    assert out["decoded"] == 0 and out["reused"] == 3  # idempotent: nothing decoded twice

    code, out = run(["link", "--project", "demo"], capsys)
    assert code == EXIT_OK, out
    assert out["linked"] == 3 and out["unlinked"] == 0 and out["with_official_target"] == 2
    lines = {ln["entry"]["key"]: ln for ln in read_jsonl(project.path(layout.LINES))}
    assert (
        lines["e001"]["text"]["src"] == "Hello there." and lines["e001"]["text"]["tgt_official"] == "Привет."
    )
    assert lines["e002_v2"]["text"]["key"] == "e002" and lines["e002_v2"]["text"]["match"] == "normalized"
    assert Project.load("demo").data["source_lang"] == "en"
    assert project.progress()["link"]["status"] == "done"

    # extract after link keeps the linked text
    run(["extract", "--project", "demo"], capsys)
    lines = {ln["entry"]["key"]: ln for ln in read_jsonl(project.path(layout.LINES))}
    assert lines["e001"]["text"]["tgt_official"] == "Привет."


def test_failing_adapter_is_a_warning(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = dict(base.REGISTRY)
    registry["broken"] = "babeldub_test_fake_adapters:BrokenAdapter"
    registry["missing"] = "babeldub_no_such_module:Nope"
    monkeypatch.setattr(base, "REGISTRY", registry)
    code, out = run(["scan", "--project", "demo"], capsys)
    assert code == EXIT_ATTENTION
    joined = " ".join(out["warnings"])
    assert "broken" in joined and "parser exploded" in joined and "missing" in joined
    assert out["voice_assets"]  # the other adapters still produced the inventory


def test_link_reports_unlinked(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    project: Project = env["project"]
    (env["game"] / "game" / "strings.txt").write_text("other|en|Nothing to see\n", encoding="utf-8")
    run(["scan", "--project", "demo"], capsys)
    run(["extract", "--project", "demo"], capsys)
    code, out = run(["link", "--project", "demo"], capsys)
    assert code == EXIT_ATTENTION and out["unlinked"] == 3 and out["linked"] == 0
    assert all("no_text" in ln["flags"] for ln in read_jsonl(project.path(layout.LINES)))
    assert project.progress()["link"]["status"] == "attention"


def test_extract_dry_run_writes_nothing(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    project: Project = env["project"]
    run(["scan", "--project", "demo"], capsys)
    code, out = run(["extract", "--project", "demo", "--dry-run", "--kinds", "all"], capsys)
    assert out["dry_run"] and out["would_decode"] == 3
    assert not project.path(layout.LINES).exists()
    assert not project.path(layout.ORIG_AUDIO_DIR).exists() or not any(
        project.path(layout.ORIG_AUDIO_DIR).iterdir()
    )
