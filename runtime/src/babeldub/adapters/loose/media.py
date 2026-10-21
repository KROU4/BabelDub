"""Probe, decode and encode ordinary audio files (used by the loose and Wwise adapters).

soundfile (libsndfile) handles WAV/FLAC/OGG Vorbis/Opus/MP3 in-process; ffprobe/ffmpeg cover
AAC/M4A and anything libsndfile rejects; vgmstream-cli decodes game formats (.wem etc.).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from babeldub.core.output import EXIT_ENV, BabelDubError
from babeldub.core.tools import find_tool, require_tool, run

AUDIO_EXTS = (".ogg", ".wav", ".mp3", ".flac", ".opus", ".m4a", ".aac")

# libsndfile subtype -> short codec name used in lines.jsonl
_SUBTYPE_CODEC = {
    "VORBIS": "vorbis",
    "OPUS": "opus",
    "MPEG_LAYER_III": "mp3",
    "MPEG_LAYER_II": "mp2",
    "PCM_16": "pcm16",
    "PCM_24": "pcm24",
    "PCM_32": "pcm32",
    "PCM_U8": "pcm8",
    "PCM_S8": "pcm8",
    "FLOAT": "float32",
    "DOUBLE": "float64",
    "ULAW": "ulaw",
    "ALAW": "alaw",
    "IMA_ADPCM": "ima_adpcm",
    "MS_ADPCM": "ms_adpcm",
}


def probe(path: Path) -> dict[str, Any]:
    """{sr, ch, dur_s, codec, subtype?, format?} without decoding the whole file."""
    try:
        info = sf.info(str(path))
        codec = _SUBTYPE_CODEC.get(info.subtype, info.subtype.lower())
        if info.format == "FLAC":
            codec = "flac"
        return {
            "sr": int(info.samplerate),
            "ch": int(info.channels),
            "dur_s": round(float(info.frames) / info.samplerate, 3) if info.samplerate else None,
            "codec": codec,
            "subtype": info.subtype,
            "format": info.format,
        }
    except (RuntimeError, sf.LibsndfileError):
        pass
    return ffprobe(path)


def ffprobe(path: Path) -> dict[str, Any]:
    tool = find_tool("ffprobe")
    if tool is None:
        raise BabelDubError(
            f"cannot read {path.name}: libsndfile does not support it and ffprobe is missing",
            code=EXIT_ENV,
            hint="install ffmpeg (ffprobe) and add it to PATH, or run `babeldub setup --tools`",
        )
    proc = run(
        [
            tool,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,duration:format=duration",
            "-of",
            "json",
            path,
        ],
        timeout=60,
    )
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise BabelDubError(f"{path.name}: no audio stream")
    st = streams[0]
    dur = st.get("duration") or (data.get("format") or {}).get("duration")
    return {
        "sr": int(st.get("sample_rate") or 0) or None,
        "ch": int(st.get("channels") or 0) or None,
        "dur_s": round(float(dur), 3) if dur not in (None, "N/A") else None,
        "codec": st.get("codec_name"),
    }


def decode_to_wav(src: Path, dst: Path) -> dict[str, Any]:
    """Decode any supported file to 16-bit WAV at its own rate and channel count."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part.wav")
    try:
        data, sr = sf.read(str(src), dtype="float32", always_2d=True)
        sf.write(str(tmp), data, sr, subtype="PCM_16")
    except (RuntimeError, sf.LibsndfileError):
        ffmpeg = require_tool("ffmpeg", f"decoding {src.suffix} audio")
        run([ffmpeg, "-v", "error", "-y", "-i", src, "-vn", "-c:a", "pcm_s16le", tmp], timeout=300)
    tmp.replace(dst)
    info = sf.info(str(dst))
    return {
        "sr": int(info.samplerate),
        "ch": int(info.channels),
        "dur_s": round(info.frames / info.samplerate, 3),
    }


def vgmstream_decode(src: Path, dst: Path, subsong: int | None = None) -> dict[str, Any]:
    """Decode a game audio file with vgmstream-cli to WAV (raises EXIT_ENV when the tool is missing)."""
    tool = require_tool("vgmstream-cli", "decoding game audio formats")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part.wav")
    cmd: list[Any] = [tool, "-o", tmp]
    if subsong is not None:
        cmd += ["-s", str(subsong)]
    cmd.append(src)
    run(cmd, timeout=300)
    if not tmp.exists():
        raise BabelDubError(f"vgmstream-cli produced no output for {src.name}")
    tmp.replace(dst)
    info = sf.info(str(dst))
    return {
        "sr": int(info.samplerate),
        "ch": int(info.channels),
        "dur_s": round(info.frames / info.samplerate, 3),
    }


def resample(data: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Band-limited FFT resampling of [frames, channels] float data (no external dependencies)."""
    if sr_in == sr_out or data.shape[0] == 0:
        return data
    n_in = data.shape[0]
    n_out = max(1, int(round(n_in * sr_out / sr_in)))
    spec = np.fft.rfft(data, axis=0)
    bins = n_out // 2 + 1
    if spec.shape[0] >= bins:
        spec = spec[:bins]
    else:
        pad = np.zeros((bins - spec.shape[0], spec.shape[1]), dtype=spec.dtype)
        spec = np.concatenate([spec, pad], axis=0)
    out = np.fft.irfft(spec, n=n_out, axis=0) * (n_out / n_in)
    return out.astype(np.float32)


def match_channels(data: np.ndarray, channels: int) -> np.ndarray:
    if data.shape[1] == channels:
        return data
    mono = data.mean(axis=1, keepdims=True)
    return np.repeat(mono, channels, axis=1) if channels > 1 else mono


def encode_like(src_wav: Path, dst: Path, sr: int, ch: int, fmt: str, subtype: str | None = None) -> str:
    """Encode `src_wav` to `dst` with the given rate/channels in the container/codec of `fmt`.

    fmt is the original file extension (".ogg", ".mp3", ".opus", ".m4a", ".aac", ".wav", ".flac").
    Returns the codec used. ffmpeg is preferred for lossy formats; without it libsndfile writes
    OGG Vorbis, Opus and MP3; AAC needs ffmpeg.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part" + dst.suffix)
    ext = fmt.lower()
    if ext in (".wav", ".flac"):
        data, sr_in = sf.read(str(src_wav), dtype="float32", always_2d=True)
        data = match_channels(resample(data, sr_in, sr), ch)
        if ext == ".wav":
            sub = subtype if subtype and subtype in sf.available_subtypes("WAV") else "PCM_16"
            sf.write(str(tmp), np.clip(data, -1.0, 1.0), sr, subtype=sub, format="WAV")
            codec = _SUBTYPE_CODEC.get(sub, sub.lower())
        else:
            sub = subtype if subtype in ("PCM_16", "PCM_24", "PCM_S8") else "PCM_16"
            sf.write(str(tmp), np.clip(data, -1.0, 1.0), sr, subtype=sub, format="FLAC")
            codec = "flac"
        tmp.replace(dst)
        return codec
    ffmpeg = find_tool("ffmpeg")
    codecs = {".ogg": "libvorbis", ".mp3": "libmp3lame", ".opus": "libopus", ".m4a": "aac", ".aac": "aac"}
    if ext not in codecs:
        raise BabelDubError(f"unsupported target format {fmt}")
    if ffmpeg is not None:
        out_sr = sr
        if ext == ".opus" and sr not in (8000, 12000, 16000, 24000, 48000):
            out_sr = 48000  # Opus only runs at these rates; players resample transparently
        cmd: list[Any] = [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            src_wav,
            "-vn",
            "-ar",
            str(out_sr),
            "-ac",
            str(ch),
            "-c:a",
            codecs[ext],
        ]
        if ext == ".ogg":
            cmd += ["-q:a", "6"]
        elif ext == ".mp3":
            cmd += ["-q:a", "2"]
        elif ext == ".opus":
            cmd += ["-b:a", "96k"]
        else:
            cmd += ["-b:a", "160k"]
        if ext == ".m4a":
            cmd += ["-f", "ipod"]
        elif ext == ".aac":
            cmd += ["-f", "adts"]
        cmd.append(tmp)
        run(cmd, timeout=300)
        tmp.replace(dst)
        return {".ogg": "vorbis", ".mp3": "mp3", ".opus": "opus", ".m4a": "aac", ".aac": "aac"}[ext]
    if ext in (".m4a", ".aac"):
        raise BabelDubError(
            f"encoding {ext} needs ffmpeg",
            code=EXIT_ENV,
            hint="install ffmpeg or run `babeldub setup --tools`",
        )
    data, sr_in = sf.read(str(src_wav), dtype="float32", always_2d=True)
    out_sr = sr if ext != ".opus" else 48000
    data = np.clip(match_channels(resample(data, sr_in, out_sr), ch), -1.0, 1.0)
    fmt_map = {".ogg": ("OGG", "VORBIS"), ".opus": ("OGG", "OPUS"), ".mp3": ("MP3", "MPEG_LAYER_III")}
    container, sub = fmt_map[ext]
    sf.write(str(tmp), data, out_sr, format=container, subtype=sub)
    tmp.replace(dst)
    return {".ogg": "vorbis", ".mp3": "mp3", ".opus": "opus"}[ext]


def is_decodable(path: Path) -> tuple[bool, str | None, dict[str, Any] | None]:
    """(ok, problem, probe) - decodes the first second to make sure the file is really playable."""
    try:
        info = probe(path)
    except BabelDubError as err:
        return False, str(err), None
    if not info.get("dur_s"):
        return False, "zero duration", info
    try:
        with sf.SoundFile(str(path)) as fh:
            block = fh.read(frames=min(fh.frames, fh.samplerate), dtype="float32")
            if block.size == 0:
                return False, "no samples decoded", info
    except (RuntimeError, sf.LibsndfileError):
        ffmpeg = find_tool("ffmpeg")
        if ffmpeg is None:
            return True, None, info  # probed by ffprobe; full decode check needs ffmpeg
        proc = subprocess.run(
            [str(ffmpeg), "-v", "error", "-i", str(path), "-t", "1", "-f", "null", "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if proc.returncode != 0:
            return False, (proc.stderr or "ffmpeg decode failed").strip().splitlines()[-1], info
    return True, None, info
