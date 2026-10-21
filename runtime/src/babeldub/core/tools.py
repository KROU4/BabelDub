"""Locating and running external tools (ffmpeg, vgmstream-cli, rubberband)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from babeldub.core.output import EXIT_ENV, BabelDubError
from babeldub.core.paths import IS_WINDOWS, tools_dir

_EXE = ".exe" if IS_WINDOWS else ""


def find_tool(name: str) -> Path | None:
    """Look in BABELDUB_HOME/tools/** first (tools installed by `babeldub setup`), then PATH."""
    root = tools_dir()
    if root.exists():
        for candidate in sorted(root.rglob(name + _EXE)):
            if candidate.is_file():
                return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def require_tool(name: str, why: str) -> Path:
    path = find_tool(name)
    if path is None:
        raise BabelDubError(
            f"{name} not found ({why})",
            code=EXIT_ENV,
            hint="run `babeldub doctor`, then `babeldub setup --tools` or install it and add to PATH",
        )
    return path


def run(cmd: list[str | os.PathLike[str]], timeout: float | None = 600, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a tool, capture text output, raise BabelDubError with the tail of stderr on failure."""
    proc = subprocess.run(
        [str(c) for c in cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise BabelDubError(f"{Path(str(cmd[0])).name} failed with code {proc.returncode}", detail=tail)
    return proc
