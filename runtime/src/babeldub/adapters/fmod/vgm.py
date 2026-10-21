"""vgmstream-cli wrapper for codecs we do not decode ourselves (FMOD Vorbis, MPEG, Opus, ...).

vgmstream numbers subsongs of a .bank globally across all FSB5 blocks in SNDH order, 1-based:
subsong = (subsounds in the FSBs before this one) + index + 1 (checked on Low-Budget Repairs:
subsong 16 of Dialogue_en-US.bank is FSB #1 index 0). For a raw .fsb it is index + 1.
"""

from __future__ import annotations

import os
from pathlib import Path

from babeldub.core.output import EXIT_ERROR, BabelDubError
from babeldub.core.tools import require_tool, run

_RUN_GAP = 8  # decode neighbours instead of starting another process when the gap is small
_TIMEOUT_PER_SUBSONG = 30.0


def tool() -> Path:
    return require_tool("vgmstream-cli", "decoding FMOD Vorbis/MPEG/Opus subsounds")


def _runs(subsongs: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for s in sorted(set(subsongs)):
        if runs and s - runs[-1][1] <= _RUN_GAP:
            runs[-1] = (runs[-1][0], s)
        else:
            runs.append((s, s))
    return runs


def decode_subsongs(container: Path, subsongs: list[int], out_dir: Path) -> dict[int, Path]:
    """Decode subsongs (1-based, global) to 16-bit WAV files in out_dir; returns subsong -> wav."""
    exe = tool()
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[int, Path] = {}
    for first, last in _runs(subsongs):
        if first == last:
            target = out_dir / f"{first:06d}.wav"
            cmd: list[str | os.PathLike[str]] = [exe, "-i", "-s", str(first), "-o", target, container]
        else:
            pattern = out_dir / "?06s.wav"
            cmd = [exe, "-i", "-s", str(first), "-S", str(last), "-o", pattern, container]
        run(cmd, timeout=60 + _TIMEOUT_PER_SUBSONG * (last - first + 1))
        for s in range(first, last + 1):
            wav = out_dir / f"{s:06d}.wav"
            if wav.exists():
                result[s] = wav
    missing = [s for s in subsongs if s not in result]
    if missing:
        raise BabelDubError(
            f"vgmstream-cli did not produce {len(missing)} subsong(s)",
            code=EXIT_ERROR,
            missing=missing[:10],
            hint="check that vgmstream-cli can open this bank: vgmstream-cli -m <bank>",
        )
    return result
