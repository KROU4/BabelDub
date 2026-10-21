"""WAV/FLAC reading and writing through soundfile (contract §7)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

_PCM_SUBTYPES = {"PCM_16", "PCM_24", "PCM_32", "PCM_U8", "PCM_S8"}


def read_audio(path: str | os.PathLike[str]) -> tuple[np.ndarray, int]:
    """Read any soundfile-supported file as float32 [frames, channels] and its sample rate."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return np.ascontiguousarray(data, dtype=np.float32), int(sr)


def write_wav(path: str | os.PathLike[str], data: np.ndarray, sr: int, subtype: str = "PCM_16") -> Path:
    """Write float audio ([frames] or [frames, channels]) atomically; PCM output is clipped to [-1, 1]."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim == 2 and arr.shape[1] == 1:
        arr = arr[:, 0]
    if subtype.upper() in _PCM_SUBTYPES:
        arr = np.clip(arr, -1.0, 1.0)
    fd, tmp = tempfile.mkstemp(prefix=f".{out.stem}.", suffix=".tmp.wav", dir=out.parent)
    os.close(fd)
    try:
        sf.write(tmp, arr, int(sr), subtype=subtype, format="WAV")
        os.replace(tmp, out)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return out


def probe(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Header info without decoding: {sr, ch, dur_s, frames, subtype, format}."""
    info = sf.info(str(path))
    frames = int(info.frames)
    sr = int(info.samplerate)
    return {
        "sr": sr,
        "ch": int(info.channels),
        "dur_s": round(frames / sr, 4) if sr else 0.0,
        "frames": frames,
        "subtype": info.subtype,
        "format": info.format,
    }
