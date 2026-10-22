import argparse
import json
from importlib import import_module

import pytest

from babeldub import cli
from babeldub.core import layout
from babeldub.core.ids import line_id, uid
from babeldub.core.jsonl import write_jsonl
from babeldub.core.output import BabelDubError, emit_error, set_pretty
from babeldub.core.project import Project

MODULES = ["babeldub.commands.env", "babeldub.commands.project", "babeldub.commands.translate",
           "babeldub.commands.analyze"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="babeldub")
    sub = parser.add_subparsers(dest="command")
    sub.required = True
    for name in MODULES:
        import_module(name).register(sub, cli._common)
    return parser


def make_line(index, scene="a1_m01", kind="dialogue", src="Okay, let's see.", official=None, dur=2.0,
              speaker="Zbyszek", fixed=False, flags=None):
    lid = line_id("fmod", "StreamingAssets/Dialogue_en-US.bank", "fsb1", index)
    key = f"dlg_{scene}_{index:03d}"
    text = {"key": key, "src": src, "src_lang": "en", "tgt_official": official, "source": "synthetic"} if src else None
    return {
        "id": lid,
        "uid": uid(lid),
        "adapter": "fmod",
        "asset_id": "fmod:StreamingAssets/Dialogue_en-US.bank",
        "entry": {"key": key, "sub": "fsb1", "index": index},
        "kind": kind,
        "locale": "en-US",
        "audio": {"sr": 48000, "ch": 1, "dur_s": dur, "codec": "vorbis", "wav": f"audio/orig/{uid(lid)}.wav"},
        "speaker_hint": speaker,
        "scene": scene,
        "order": index,
        "fixed_length": fixed,
        "text": text,
        "flags": flags or [],
    }


@pytest.fixture
def line():
    """Factory of synthetic canonical line records (CONTRACT.md §4.3)."""
    return make_line


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(path))
    monkeypatch.delenv("BABELDUB_PROJECT", raising=False)
    monkeypatch.chdir(tmp_path)
    return path


@pytest.fixture
def bd(home, capsys):
    """Run a command like the CLI does; returns (exit code, the single JSON object printed)."""
    parser = build_parser()

    def run(*argv):
        capsys.readouterr()
        args = parser.parse_args([str(a) for a in argv])
        set_pretty(False)
        try:
            code = int(args.func(args))
        except BabelDubError as err:
            code = emit_error(err)
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 1, lines
        return code, json.loads(lines[0])

    return run


@pytest.fixture
def game(tmp_path):
    folder = tmp_path / "Low-Budget Repairs"
    (folder / "StreamingAssets").mkdir(parents=True)
    (folder / "StreamingAssets" / "Dialogue_en-US.bank").write_bytes(b"RIFF")
    return folder


@pytest.fixture
def project_with_lines(bd, game):
    """Initialised ru project with lines.jsonl written and scan/extract/link marked done."""

    def build(lines, lang="ru", mode="auto"):
        code, out = bd("init", game, "--lang", lang, "--mode", mode)
        assert code in (0, 3), out
        project = Project.load(out["project"])
        write_jsonl(project.path(layout.LINES), lines, schema="babeldub.lines/1")
        for stage in ("scan", "extract", "link"):
            project.mark_stage(stage, "done")
        return project

    return build
