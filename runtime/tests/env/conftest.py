import argparse
import json
from importlib import import_module

import pytest

from babeldub import cli
from babeldub.core.output import BabelDubError, emit_error, set_pretty

MODULES = ["babeldub.commands.env", "babeldub.commands.project", "babeldub.commands.translate"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="babeldub")
    sub = parser.add_subparsers(dest="command")
    sub.required = True
    for name in MODULES:
        import_module(name).register(sub, cli._common)
    return parser


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
