"""`babeldub` command line entry point.

Commands live in babeldub.commands.<group>; each module exposes register(subparsers) and
binds handlers with set_defaults(func=handler). A handler receives the parsed args and
returns an exit code (see babeldub.core.output).
"""

from __future__ import annotations

import argparse
import sys
import traceback
from importlib import import_module

from babeldub import __version__
from babeldub.core.output import EXIT_ERROR, EXIT_USAGE, BabelDubError, emit, emit_error, set_pretty

COMMAND_MODULES = [
    "babeldub.commands.env",  # doctor, setup, models
    "babeldub.commands.project",  # init, status, set, query, sample, games
    "babeldub.commands.recon",  # scan, extract, link
    "babeldub.commands.cast",  # cast
    "babeldub.commands.translate",  # glossary, batch, translate
    "babeldub.commands.voice",  # synth, post
    "babeldub.commands.qa",  # qa, decide, report
    "babeldub.commands.pack",  # build, verify, install, uninstall, reapply
]


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", help="project folder or slug (default: active project)")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output for humans")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="babeldub",
        description="BabelDub toolbox: game recon, voice extraction, AI dubbing pipeline and native repacking.",
    )
    parser.add_argument("--version", action="version", version=f"babeldub {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True
    for module_name in COMMAND_MODULES:
        module = import_module(module_name)
        module.register(sub, _common)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        parser = build_parser()
    except Exception as exc:  # a broken command module must not break the one-JSON contract
        return emit(
            {
                "error": f"babeldub failed to start: {type(exc).__name__}: {exc}",
                "hint": "reinstall the CLI (skill babeldub-doctor, scripts/bootstrap.py --force)",
            },
            EXIT_ERROR,
        )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse prints its own usage text
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    set_pretty(getattr(args, "pretty", False))
    try:
        return int(args.func(args))
    except BabelDubError as err:
        return emit_error(err)
    except KeyboardInterrupt:
        return emit({"error": "interrupted"}, EXIT_ERROR)
    except Exception as exc:  # unexpected: keep the contract (one JSON object) and show where
        tb = traceback.extract_tb(exc.__traceback__)[-3:]
        where = [f"{f.filename.rsplit('babeldub', 1)[-1]}:{f.lineno} {f.name}" for f in tb]
        return emit({"error": f"{type(exc).__name__}: {exc}", "where": where}, EXIT_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
