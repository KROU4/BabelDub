"""Rebuild FSB5 blocks with replacement audio and write the patched bank.

One codec per FSB: an FSB that receives replacements is rewritten entirely in the target codec
(PCM16 or FADPCM) with the same number, order and names of subsounds - the audio table (STBL)
addresses subsounds by index. Kept subsounds are copied byte-for-byte when the codec does not
change, otherwise decoded (own decoder or vgmstream-cli) and re-encoded at their original rate
and channel count. FSBs without replacements are copied byte-for-byte.
"""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from babeldub.adapters.fmod import fadpcm, pcm, vgm
from babeldub.adapters.fmod import fsb5 as F
from babeldub.adapters.fmod.bank import BankError, FsbPayload, write_container
from babeldub.adapters.fmod.bankinfo import BankInfo
from babeldub.core.output import EXIT_USAGE, BabelDubError

_ENCODE_BATCH_FRAMES = 16384  # FADPCM frames gathered before one vectorized encode call
_PARALLEL_MIN_FRAMES = 4 * _ENCODE_BATCH_FRAMES  # below this, threads have nothing to share
CODEC_CHOICES = ("auto", "pcm16", "fadpcm")


def resolve_codec(choice: str, source_codec: int) -> int:
    """auto: keep a writable source codec (PCM16/FADPCM), otherwise FADPCM (Vorbis has no open encoder)."""
    choice = (choice or "auto").lower()
    if choice not in CODEC_CHOICES:
        raise BabelDubError(f"unknown codec '{choice}'", code=EXIT_USAGE, hint="use auto, pcm16 or fadpcm")
    if choice == "auto":
        return source_codec if source_codec in F.WRITABLE else F.CODEC_FADPCM
    return F.CODEC_BY_NAME[choice]


@dataclass
class FsbReport:
    sub: str
    codec_in: str
    codec_out: str
    n: int
    replaced: int = 0
    copied: int = 0
    reencoded: int = 0
    resampled: int = 0
    remixed: int = 0
    length_fixed: int = 0
    loops_dropped: int = 0

    def row(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in self.__dict__.items()
            if v or k in ("sub", "codec_in", "codec_out", "n", "replaced")
        }


@dataclass
class _Pending:
    sub: F.Subsound
    pcm: np.ndarray
    rate: int
    chunks: list[tuple[int, bytes]] = field(default_factory=list)


class _Encoder:
    """Encodes batches and hands them to the writer in submission order.

    FADPCM batches run on a thread pool: numpy releases the GIL inside the vectorized encoder, so
    threads scale almost like processes without re-importing the caller's __main__ (Windows spawn).
    """

    def __init__(self, codec: int, jobs: int) -> None:
        self.codec = codec
        self.pool = ThreadPoolExecutor(max_workers=jobs) if codec == F.CODEC_FADPCM and jobs > 1 else None
        self.max_inflight = 2 * jobs if self.pool else 0
        self._inflight: deque[tuple[list[_Pending], Future[list[bytes]] | list[bytes]]] = deque()

    def _encode(self, batch: list[_Pending]) -> list[bytes]:
        if self.codec == F.CODEC_FADPCM:
            return fadpcm.encode_many([p.pcm for p in batch])
        return [F.encode_payload(self.codec, p.pcm) for p in batch]

    def submit(self, batch: list[_Pending]) -> None:
        if self.pool is not None:
            self._inflight.append((batch, self.pool.submit(self._encode, batch)))
        else:
            self._inflight.append((batch, self._encode(batch)))

    def drain(self, writer: F.Fsb5Writer, keep: int) -> None:
        while len(self._inflight) > keep:
            batch, result = self._inflight.popleft()
            payloads = result.result() if isinstance(result, Future) else result
            for p, data in zip(batch, payloads, strict=True):
                writer.add(F.NewSubsound(p.sub.name, p.rate, p.pcm.shape[1], p.pcm.shape[0], data, p.chunks))

    def close(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(cancel_futures=True)


def default_jobs() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _header_tail(src: F.Fsb5, guid: str) -> bytes:
    header = bytearray(src.header)
    if guid == "zero":
        header[0x24:] = bytes(len(header) - 0x24)  # null GUID: FMOD skips its header cache
    elif guid != "keep":
        raise BabelDubError(f"unknown fsb_guid '{guid}'", code=EXIT_USAGE, hint="use keep or zero")
    return bytes(header)


def _loop_chunk(sub: F.Subsound, new_len: int) -> tuple[int, bytes] | None:
    """Keep a whole-sound loop on a replaced subsound; partial loops cannot be mapped."""
    raw = sub.chunk(F.CHUNK_LOOP)
    if raw is None or len(raw) < 8:
        return None
    start, end = struct.unpack_from("<II", raw, 0)
    if start == 0 and end >= sub.nsamples - 1 and new_len > 0:
        return (F.CHUNK_LOOP, struct.pack("<II", 0, new_len - 1) + raw[8:])
    return None


def _decode_kept_with_vgmstream(info: BankInfo, k: int, indices: list[int], tmp: Path) -> dict[int, Path]:
    if not indices:
        return {}
    subsongs = [info.subsong(k, i) for i in indices]
    files = vgm.decode_subsongs(info.path, subsongs, tmp / f"vgm_fsb{k}")
    return {i: files[s] for i, s in zip(indices, subsongs, strict=True)}


def rebuild_fsb(
    info: BankInfo,
    k: int,
    replacements: dict[int, Path],
    target_codec: int,
    *,
    sample_rate: int | None,
    fsb_guid: str,
    tmp: Path,
    jobs: int = 1,
) -> tuple[F.Fsb5Writer, FsbReport]:
    src = info.fsbs[k]
    report = FsbReport(f"fsb{k}", src.codec_name, F.codec_name(target_codec), len(src.subsounds))
    copy_raw = target_codec == src.codec and not src.big_endian_pcm
    native = src.codec in F.NATIVE_DECODE
    need_decode = [] if copy_raw else [s.index for s in src.subsounds if s.index not in replacements]
    vgm_files = {} if native else _decode_kept_with_vgmstream(info, k, need_decode, tmp)
    spool = tempfile.TemporaryFile(dir=tmp)
    writer = F.Fsb5Writer(target_codec, _header_tail(src, fsb_guid), names=src.has_names, spool=spool)
    pending: list[_Pending] = []
    pending_frames = 0
    to_encode = [s for s in src.subsounds if s.index in replacements or not copy_raw]
    work_frames = sum((s.nsamples // 256 + 1) * s.channels for s in to_encode)
    encoder = _Encoder(target_codec, jobs if work_frames >= _PARALLEL_MIN_FRAMES else 1)

    def flush() -> None:
        nonlocal pending_frames
        if pending:
            encoder.submit(list(pending))
            pending.clear()
            pending_frames = 0
        encoder.drain(writer, keep=encoder.max_inflight)

    def queue(item: _Pending) -> None:
        nonlocal pending_frames
        pending.append(item)
        pending_frames += (item.pcm.shape[0] // 256 + 1) * item.pcm.shape[1]
        if pending_frames >= _ENCODE_BATCH_FRAMES:
            flush()

    def add_replaced(sub: F.Subsound) -> None:
        rate = sample_rate or sub.rate
        data, sr_in, _ = pcm.load_for_subsound(replacements[sub.index], sub.channels, rate)
        channels_in = sf.info(str(replacements[sub.index])).channels
        report.replaced += 1
        report.resampled += int(sr_in != rate)
        report.remixed += int(channels_in != sub.channels)
        chunks: list[tuple[int, bytes]] = []
        loop = _loop_chunk(sub, data.shape[0])
        if loop:
            chunks.append(loop)
        elif sub.chunk(F.CHUNK_LOOP) is not None:
            report.loops_dropped += 1
        if sub.chunk(F.CHUNK_PEAKVOLUME) is not None:
            chunks.append(F.peak_chunk(data))
        queue(_Pending(sub, data, rate, chunks))

    def add_kept(sub: F.Subsound) -> None:
        if copy_raw:
            flush()
            encoder.drain(writer, keep=0)
            raw = info.read_data(k, sub)
            writer.add(F.NewSubsound(sub.name, sub.rate, sub.channels, sub.nsamples, raw, list(sub.chunks)))
            report.copied += 1
            return
        if native:
            decoded = F.decode_native(src, sub, info.read_data(k, sub))
        else:
            decoded, _ = sf.read(str(vgm_files[sub.index]), dtype="int16", always_2d=True)
            decoded = pcm.to_channels(decoded, sub.channels)
            if decoded.shape[0] != sub.nsamples:
                report.length_fixed += 1
                fixed = np.zeros((sub.nsamples, sub.channels), dtype=np.int16)
                n = min(sub.nsamples, decoded.shape[0])
                fixed[:n] = decoded[:n]
                decoded = fixed
        chunks = [c for c in sub.chunks if c[0] in F.PORTABLE_CHUNKS]
        if sub.chunk(F.CHUNK_PEAKVOLUME) is not None:
            chunks.append(F.peak_chunk(decoded))
        report.reencoded += 1
        queue(_Pending(sub, np.ascontiguousarray(decoded), sub.rate, chunks))

    try:
        for sub in src.subsounds:
            if sub.index in replacements:
                add_replaced(sub)
            else:
                add_kept(sub)
        flush()
        encoder.drain(writer, keep=0)
    except BaseException:
        spool.close()
        raise
    finally:
        encoder.close()
    return writer, report


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build_bank(
    info: BankInfo,
    replacements: dict[tuple[int, int], Path],
    out_path: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Write the rebuilt container; replacements map (fsb index, subsound index) -> wav."""
    by_fsb: dict[int, dict[int, Path]] = {}
    for (k, i), wav in replacements.items():
        if k >= len(info.fsbs) or i >= len(info.fsbs[k].subsounds):
            raise BabelDubError(f"no subsound fsb{k}:{i} in {info.path.name}", code=EXIT_USAGE)
        if not Path(wav).is_file():
            raise BabelDubError(f"replacement audio not found: {wav}", code=EXIT_USAGE)
        by_fsb.setdefault(k, {})[i] = Path(wav)
    sample_rate = options.get("sample_rate")
    sample_rate = int(sample_rate) if sample_rate else None
    fsb_guid = str(options.get("fsb_guid", "keep"))
    jobs = int(options.get("jobs") or default_jobs())
    reports: list[FsbReport] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_root = options.get("tmp_dir")
    with tempfile.TemporaryDirectory(prefix=".babeldub-fmod-", dir=tmp_root or out_path.parent) as tmp_name:
        tmp = Path(tmp_name)
        payloads: dict[int, FsbPayload] = {}
        writers: list[F.Fsb5Writer] = []
        try:
            for k in sorted(by_fsb):
                target = resolve_codec(str(options.get("codec", "auto")), info.fsbs[k].codec)
                writer, report = rebuild_fsb(
                    info, k, by_fsb[k], target, sample_rate=sample_rate, fsb_guid=fsb_guid, tmp=tmp, jobs=jobs
                )
                payloads[k] = writer
                writers.append(writer)
                reports.append(report)
            size = write_container(info.container, payloads, out_path)
        except (F.Fsb5Error, BankError) as exc:
            raise BabelDubError(f"{info.path.name}: {exc}", code=EXIT_USAGE) from exc
        finally:
            for w in writers:
                w.spool.close()
    total = sum(len(f.subsounds) for f in info.fsbs)
    codecs = sorted({r.codec_out for r in reports})
    return {
        "out": str(out_path),
        "replaced": len(replacements),
        "kept": total - len(replacements),
        "codec": codecs[0] if len(codecs) == 1 else ("+".join(codecs) or "copy"),
        "size": size,
        "sha1": _sha1(out_path),
        "fsbs": [r.row() for r in reports],
    }
