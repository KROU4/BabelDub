"""Pitch-preserving time-stretch through external tools (contract §7).

Order of preference: Rubber Band CLI (R3 engine, `-3 -D <seconds>`), ffmpeg's `rubberband`
filter (if the ffmpeg build has librubberband), ffmpeg `atempo` (chained factors in
0.5..2.0 so old builds work too). Rubber Band is GPL and is never shipped with BabelDub;
it is used only if the user installed it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from babeldub.audio.io import probe
from babeldub.core.output import EXIT_ENV, EXIT_USAGE, BabelDubError
from babeldub.core.tools import find_tool, run

METHODS = ("rubberband", "ffmpeg-rubberband", "atempo")


def atempo_chain(tempo: float) -> list[float]:
    """Split a tempo factor into atempo stages that each stay within 0.5..2.0."""
    if tempo <= 0:
        raise ValueError("tempo must be positive")
    factors: list[float] = []
    while tempo > 2.0:
        factors.append(2.0)
        tempo /= 2.0
    while tempo < 0.5:
        factors.append(0.5)
        tempo /= 0.5
    factors.append(tempo)
    return factors


@lru_cache(maxsize=4)
def _ffmpeg_has_filter(ffmpeg: str, name: str) -> bool:
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False


def available_methods() -> list[str]:
    """Stretch methods usable on this machine, best first."""
    methods: list[str] = []
    if find_tool("rubberband"):
        methods.append("rubberband")
    ffmpeg = find_tool("ffmpeg")
    if ffmpeg:
        if _ffmpeg_has_filter(str(ffmpeg), "rubberband"):
            methods.append("ffmpeg-rubberband")
        methods.append("atempo")
    return methods


def _rubberband(in_wav: Path, out_wav: Path, ratio: float) -> None:
    tool = find_tool("rubberband")
    if tool is None:
        raise BabelDubError("rubberband not found", code=EXIT_ENV)
    seconds = probe(in_wav)["dur_s"] * ratio
    try:  # Rubber Band 3.x: R3 ("finer") engine, exact output duration
        run([tool, "-q", "-3", "-D", f"{seconds:.6f}", in_wav, out_wav], timeout=300)
    except BabelDubError:  # 2.x has no -3/-D: fall back to the R2 engine and a time ratio
        run([tool, "-q", "-t", f"{ratio:.6f}", in_wav, out_wav], timeout=300)


def _ffmpeg(in_wav: Path, out_wav: Path, audio_filter: str) -> None:
    ffmpeg = find_tool("ffmpeg")
    if ffmpeg is None:
        raise BabelDubError("ffmpeg not found", code=EXIT_ENV)
    run(
        [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", in_wav,
         "-af", audio_filter, "-c:a", "pcm_f32le", out_wav],
        timeout=300,
    )


def time_stretch(
    in_wav: str | os.PathLike[str], out_wav: str | os.PathLike[str], ratio: float, method: str | None = None
) -> str:
    """Write `in_wav` stretched to `ratio` × its duration (ratio < 1 = shorter/faster) without
    changing pitch. Returns the method used: rubberband | ffmpeg-rubberband | atempo | copy.
    `method` forces one tool (raises if it is unavailable)."""
    src, dst = Path(in_wav), Path(out_wav)
    if not (0.25 <= ratio <= 4.0):
        raise BabelDubError(f"stretch ratio {ratio:.3f} is outside 0.25..4.0", code=EXIT_USAGE)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if abs(ratio - 1.0) < 1e-3:
        shutil.copyfile(src, dst)
        return "copy"
    candidates = available_methods()
    if method:
        if method not in METHODS:
            raise BabelDubError(f"unknown stretch method '{method}'", code=EXIT_USAGE, known=list(METHODS))
        if method not in candidates:
            raise BabelDubError(
                f"stretch method '{method}' is not available here",
                code=EXIT_ENV,
                hint="install rubberband / ffmpeg and add them to PATH, see `babeldub doctor`",
            )
        candidates = [method]
    if not candidates:
        raise BabelDubError(
            "no time-stretch tool found (need rubberband or ffmpeg)",
            code=EXIT_ENV,
            hint="install ffmpeg (and optionally Rubber Band) and add it to PATH, then `babeldub doctor`",
        )
    tempo = 1.0 / ratio
    errors: list[str] = []
    for name in candidates:
        try:
            if name == "rubberband":
                _rubberband(src, dst, ratio)
            elif name == "ffmpeg-rubberband":
                _ffmpeg(src, dst, f"rubberband=tempo={tempo:.6f}:pitchq=quality")
            else:
                _ffmpeg(src, dst, ",".join(f"atempo={f:.6f}" for f in atempo_chain(tempo)))
            return name
        except BabelDubError as err:
            errors.append(f"{name}: {err}")
    raise BabelDubError("time-stretch failed with every available tool", detail=errors)
