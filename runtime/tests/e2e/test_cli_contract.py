"""CLI entry point: every command answers --help, a broken command module still yields one JSON."""

from __future__ import annotations

import argparse
import json

import pytest

from babeldub import cli
from babeldub.core.output import EXIT_ERROR, EXIT_OK


def _command_paths(parser: argparse.ArgumentParser, path: list[str]) -> list[list[str]]:
    out = [path]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                out += _command_paths(sub, [*path, name])
    return out


def test_every_command_has_help(capsys: pytest.CaptureFixture[str]) -> None:
    paths = _command_paths(cli.build_parser(), [])
    names = {" ".join(p) for p in paths}
    expected = {
        "doctor", "setup", "models list", "models fetch", "init", "status", "set", "query", "sample", "games",
        "scan", "extract", "link", "cast", "cast apply-labels", "cast select", "translate plan",
        "translate use-official", "batch make", "batch next", "batch submit", "glossary check", "glossary apply",
        "synth", "post", "qa", "decide apply", "report", "build", "verify", "install", "uninstall", "reapply",
    }  # fmt: skip
    assert expected <= names
    for path in paths:
        capsys.readouterr()
        assert cli.main([*path, "--help"]) == EXIT_OK, path
        assert "usage:" in capsys.readouterr().out


def test_broken_command_module_still_prints_one_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "COMMAND_MODULES", [*cli.COMMAND_MODULES, "babeldub.commands.does_not_exist"])
    capsys.readouterr()
    assert cli.main(["--version"]) == EXIT_ERROR
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    out = json.loads(lines[0])
    assert out["ok"] is False and "ModuleNotFoundError" in out["error"] and out["hint"]
