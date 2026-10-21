"""FSB5 sound banks: parsing, native PCM/FADPCM decoding and writing (PCM16, FADPCM).

Layout (vgmstream src/meta/fsb5.c, confirmed byte-for-byte on FMOD 2.03 output):
  header (version 1: 0x3C bytes, version 0: 0x40 bytes)
    'FSB5', u32 version, u32 numSubsounds, u32 sampleHeadersSize, u32 nameTableSize,
    u32 dataSize, u32 codec, then an opaque tail: u32 (=1 in FMOD 2.03), u32 flags
    (bit 0 = PCM16 big-endian), 16-byte hash/GUID used for header caching, 8 unknown bytes.
  sample headers: u64 per subsound
    bit 0 extra chunks follow, bits 1-4 rate index, bits 5-6 channel code (1/2/6/8),
    bits 7-33 data offset / 32 (relative to the data section), bits 34-63 samples per channel;
    each extra chunk: u32 (bit 0 next, bits 1-24 size, bits 25-31 type) + payload.
  name table (optional): u32 offset per subsound, NUL-terminated UTF-8 strings, zero padding so
    that the data section starts 32-byte aligned relative to the FSB start.
  data: one codec per FSB; every subsound starts 32-byte aligned.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import BinaryIO

import numpy as np

from babeldub.adapters.fmod import fadpcm

MAGIC = b"FSB5"

CODEC_PCM8 = 1
CODEC_PCM16 = 2
CODEC_PCM24 = 3
CODEC_PCM32 = 4
CODEC_PCMFLOAT = 5
CODEC_VORBIS = 15
CODEC_FADPCM = 16

CODEC_NAMES: dict[int, str] = {
    0: "none",
    1: "pcm8",
    2: "pcm16",
    3: "pcm24",
    4: "pcm32",
    5: "pcmfloat",
    6: "gcadpcm",
    7: "imaadpcm",
    8: "vag",
    9: "hevag",
    10: "xma",
    11: "mpeg",
    12: "celt",
    13: "at9",
    14: "xwma",
    15: "vorbis",
    16: "fadpcm",
    17: "opus",
}
CODEC_BY_NAME = {v: k for k, v in CODEC_NAMES.items()}

NATIVE_DECODE = {CODEC_PCM8, CODEC_PCM16, CODEC_PCM24, CODEC_PCM32, CODEC_PCMFLOAT, CODEC_FADPCM}
WRITABLE = {CODEC_PCM16, CODEC_FADPCM}

RATE_TABLE = (4000, 8000, 11000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 96000)
CHANNEL_TABLE = (1, 2, 6, 8)

CHUNK_CHANNELS = 1
CHUNK_FREQUENCY = 2
CHUNK_LOOP = 3
CHUNK_COMMENT = 4
CHUNK_VORBISDATA = 11
CHUNK_PEAKVOLUME = 13
# chunks that stay valid when the codec changes (rate/channels chunks are regenerated)
PORTABLE_CHUNKS = {CHUNK_LOOP, CHUNK_COMMENT}

_MAX_DATA_OFFSET = ((1 << 27) - 1) << 5
_MAX_SAMPLES = (1 << 30) - 1
_MAX_CHUNK = (1 << 24) - 1


class Fsb5Error(ValueError):
    """Malformed or unsupported FSB5 data."""


def codec_name(codec: int) -> str:
    return CODEC_NAMES.get(codec, f"codec{codec}")


def align32(n: int) -> int:
    return (n + 31) & ~31


@dataclass
class Subsound:
    index: int
    name: str | None
    rate: int
    channels: int
    nsamples: int
    data_offset: int  # relative to the data section
    data_size: int  # bytes up to the next subsound (includes alignment padding)
    chunks: list[tuple[int, bytes]] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.nsamples / self.rate if self.rate else 0.0

    def chunk(self, ctype: int) -> bytes | None:
        for t, payload in self.chunks:
            if t == ctype:
                return payload
        return None


@dataclass
class Fsb5:
    version: int
    codec: int
    header: bytes  # raw header (0x3C or 0x40 bytes)
    sample_headers_size: int
    name_table_size: int
    data_size: int
    subsounds: list[Subsound]

    @property
    def header_size(self) -> int:
        return len(self.header)

    @property
    def data_start(self) -> int:
        return self.header_size + self.sample_headers_size + self.name_table_size

    @property
    def total_size(self) -> int:
        return self.data_start + self.data_size

    @property
    def has_names(self) -> bool:
        return self.name_table_size > 0

    @property
    def flags(self) -> int:
        return struct.unpack_from("<I", self.header, 0x20)[0]

    @property
    def big_endian_pcm(self) -> bool:
        return self.codec == CODEC_PCM16 and bool(self.flags & 1)

    @property
    def codec_name(self) -> str:
        return codec_name(self.codec)


def header_size_for(version: int) -> int:
    return 0x3C if version == 1 else 0x40


def parse_fsb5(buf: bytes | bytearray | memoryview, offset: int = 0) -> Fsb5:
    """Parse header, sample headers and name table of the FSB5 at `offset` (data is not copied)."""
    mv = memoryview(buf)
    if len(mv) < offset + 0x3C or bytes(mv[offset : offset + 4]) != MAGIC:
        raise Fsb5Error("no FSB5 magic (encrypted or not an FSB5)")
    version, count, shs, nts, dsize, codec = struct.unpack_from("<6I", mv, offset + 4)
    if version not in (0, 1):
        raise Fsb5Error(f"unsupported FSB5 version {version}")
    hlen = header_size_for(version)
    end_meta = offset + hlen + shs + nts
    if end_meta > len(mv):
        raise Fsb5Error("truncated FSB5 header")
    header = bytes(mv[offset : offset + hlen])
    pos = offset + hlen
    raw_subs: list[tuple[int, int, int, int, list[tuple[int, bytes]]]] = []
    for _ in range(count):
        (mode,) = struct.unpack_from("<Q", mv, pos)
        pos += 8
        more = mode & 1
        rate_idx = (mode >> 1) & 0xF
        rate = RATE_TABLE[rate_idx] if rate_idx < len(RATE_TABLE) else 0
        channels = CHANNEL_TABLE[(mode >> 5) & 0x3]
        data_off = ((mode >> 7) & 0x07FFFFFF) << 5
        nsamples = (mode >> 34) & 0x3FFFFFFF
        chunks: list[tuple[int, bytes]] = []
        while more:
            (word,) = struct.unpack_from("<I", mv, pos)
            pos += 4
            more = word & 1
            size = (word >> 1) & 0xFFFFFF
            ctype = (word >> 25) & 0x7F
            payload = bytes(mv[pos : pos + size])
            pos += size
            if ctype == CHUNK_CHANNELS and payload:
                channels = payload[0]
            elif ctype == CHUNK_FREQUENCY and len(payload) >= 4:
                (rate,) = struct.unpack_from("<I", payload, 0)
            chunks.append((ctype, payload))
        raw_subs.append((rate, channels, nsamples, data_off, chunks))
    if pos != offset + hlen + shs:
        raise Fsb5Error(f"sample headers end at {pos - offset}, header says {hlen + shs}")
    names: list[str | None] = [None] * count
    if nts:
        nt = offset + hlen + shs
        for i in range(count):
            (noff,) = struct.unpack_from("<I", mv, nt + 4 * i)
            start = nt + noff
            end = start
            limit = nt + nts
            while end < limit and mv[end] != 0:
                end += 1
            names[i] = bytes(mv[start:end]).decode("utf-8", "replace")
    subsounds: list[Subsound] = []
    for i, (rate, channels, nsamples, data_off, chunks) in enumerate(raw_subs):
        next_off = raw_subs[i + 1][3] if i + 1 < count else dsize
        subsounds.append(
            Subsound(
                index=i,
                name=names[i],
                rate=rate,
                channels=channels,
                nsamples=nsamples,
                data_offset=data_off,
                data_size=max(0, next_off - data_off),
                chunks=chunks,
            )
        )
    return Fsb5(version, codec, header, shs, nts, dsize, subsounds)


def read_fsb5(fh: BinaryIO, offset: int) -> Fsb5:
    """Parse an FSB5 header from an open file without reading its sample data."""
    fh.seek(offset)
    head = fh.read(0x40)
    if len(head) < 0x3C or head[:4] != MAGIC:
        raise Fsb5Error("no FSB5 magic (encrypted or not an FSB5)")
    version, _count, shs, nts = struct.unpack_from("<4I", head, 4)
    meta_len = header_size_for(version) + shs + nts
    fh.seek(offset)
    meta = fh.read(meta_len)
    if len(meta) < meta_len:
        raise Fsb5Error("truncated FSB5 header")
    return parse_fsb5(meta, 0)


def payload_size(codec: int, channels: int, nsamples: int) -> int | None:
    """Exact codec payload size in bytes (without padding), when it is determined by the sample count."""
    widths = {CODEC_PCM8: 1, CODEC_PCM16: 2, CODEC_PCM24: 3, CODEC_PCM32: 4, CODEC_PCMFLOAT: 4}
    if codec in widths:
        return nsamples * channels * widths[codec]
    if codec == CODEC_FADPCM:
        return fadpcm.encoded_size(nsamples, channels)
    return None


def subsound_data(read: Callable[[int, int], bytes], fsb_offset: int, fsb: Fsb5, sub: Subsound) -> bytes:
    """Raw (padded) data of a subsound; `read(pos, size)` reads absolute bytes of the container."""
    return read(fsb_offset + fsb.data_start + sub.data_offset, sub.data_size)


def decode_native(fsb: Fsb5, sub: Subsound, data: bytes) -> np.ndarray:
    """Decode a subsound of a natively supported codec to int16 (nsamples, channels)."""
    ch = sub.channels
    n = sub.nsamples
    codec = fsb.codec
    if codec == CODEC_FADPCM:
        out = fadpcm.decode(data, ch, n)
        if out.shape[0] < n:
            raise Fsb5Error(f"subsound {sub.index}: FADPCM data shorter than {n} samples")
        return out
    need = payload_size(codec, ch, n)
    if need is None:
        raise Fsb5Error(f"codec {codec_name(codec)} is not decoded natively")
    if len(data) < need:
        raise Fsb5Error(f"subsound {sub.index}: data shorter than {n} samples")
    if codec == CODEC_PCM16:
        dtype = ">i2" if fsb.big_endian_pcm else "<i2"
        return np.frombuffer(data, dtype=dtype, count=n * ch).astype(np.int16).reshape(n, ch)
    if codec == CODEC_PCM8:
        return (np.frombuffer(data, dtype=np.int8, count=n * ch).astype(np.int16) << 8).reshape(n, ch)
    if codec == CODEC_PCM24:
        b = np.frombuffer(data, dtype=np.uint8, count=n * ch * 3).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - (1 << 24), v)
        return (v >> 8).astype(np.int16).reshape(n, ch)
    if codec == CODEC_PCM32:
        v = np.frombuffer(data, dtype="<i4", count=n * ch)
        return (v >> 16).astype(np.int16).reshape(n, ch)
    v = np.frombuffer(data, dtype="<f4", count=n * ch)  # PCMFLOAT
    return np.clip(np.round(v * 32768.0), -32768, 32767).astype(np.int16).reshape(n, ch)


def encode_payload(codec: int, pcm: np.ndarray) -> bytes:
    """Encode int16 (nsamples, channels) into a writable codec."""
    if codec == CODEC_PCM16:
        return np.ascontiguousarray(pcm, dtype="<i2").tobytes()
    if codec == CODEC_FADPCM:
        return fadpcm.encode(pcm)
    raise Fsb5Error(f"writing codec {codec_name(codec)} is not supported")


def peak_chunk(pcm: np.ndarray) -> tuple[int, bytes]:
    """PEAKVOLUME chunk (linear peak as float32) for int16 samples."""
    peak = float(np.abs(pcm.astype(np.int32)).max()) / 32768.0 if pcm.size else 0.0
    return (CHUNK_PEAKVOLUME, struct.pack("<f", peak))


def _mode_word(
    rate: int, channels: int, data_offset: int, nsamples: int, extra: bool
) -> tuple[int, list[tuple[int, bytes]]]:
    """Sample-header u64 plus the rate/channel chunks it needs."""
    chunks: list[tuple[int, bytes]] = []
    if rate in RATE_TABLE:
        rate_idx = RATE_TABLE.index(rate)
    else:
        rate_idx = RATE_TABLE.index(48000)
        chunks.append((CHUNK_FREQUENCY, struct.pack("<I", rate)))
    if channels in CHANNEL_TABLE:
        ch_code = CHANNEL_TABLE.index(channels)
    else:
        if not 1 <= channels <= 255:
            raise Fsb5Error(f"unsupported channel count {channels}")
        ch_code = 0
        chunks.append((CHUNK_CHANNELS, bytes([channels])))
    if data_offset % 32 or data_offset > _MAX_DATA_OFFSET:
        raise Fsb5Error("FSB5 data section exceeds the 4 GB format limit")
    if nsamples > _MAX_SAMPLES:
        raise Fsb5Error("subsound too long for FSB5")
    mode = (nsamples << 34) | ((data_offset >> 5) << 7) | (ch_code << 5) | (rate_idx << 1)
    if extra or chunks:
        mode |= 1
    return mode, chunks


@dataclass
class NewSubsound:
    name: str | None
    rate: int
    channels: int
    nsamples: int
    data: bytes
    chunks: list[tuple[int, bytes]] = field(default_factory=list)  # without CHANNELS/FREQUENCY


class Fsb5Writer:
    """Builds an FSB5; sample data is spooled to `spool` (a temp file for big banks) as it is added."""

    def __init__(
        self,
        codec: int,
        header_template: bytes | None = None,
        *,
        names: bool = True,
        spool: BinaryIO | None = None,
        little_endian_pcm: bool = True,
    ) -> None:
        if header_template is None:
            header_template = MAGIC + struct.pack("<I", 1) + bytes(0x3C - 8)
        if header_template[:4] != MAGIC:
            raise Fsb5Error("header template is not an FSB5 header")
        self.codec = codec
        self.version = struct.unpack_from("<I", header_template, 4)[0]
        self.header_template = bytes(header_template[: header_size_for(self.version)])
        self.names = names
        self.little_endian_pcm = little_endian_pcm
        self.spool: BinaryIO = spool if spool is not None else io.BytesIO()
        self._sample_headers = bytearray()
        self._names: list[str] = []
        self._data_size = 0
        self.count = 0

    def add(self, sub: NewSubsound) -> None:
        offset = self._data_size
        other = [c for c in sub.chunks if c[0] not in (CHUNK_CHANNELS, CHUNK_FREQUENCY)]
        mode, needed = _mode_word(sub.rate, sub.channels, offset, sub.nsamples, bool(other))
        chunks = needed + other
        self._sample_headers += struct.pack("<Q", mode)
        for k, (ctype, payload) in enumerate(chunks):
            if len(payload) > _MAX_CHUNK:
                raise Fsb5Error("FSB5 extra chunk too large")
            more = 1 if k + 1 < len(chunks) else 0
            self._sample_headers += struct.pack("<I", more | (len(payload) << 1) | (ctype << 25)) + payload
        self.spool.write(sub.data)
        pad = align32(len(sub.data)) - len(sub.data)
        if pad:
            self.spool.write(b"\0" * pad)
        self._data_size += len(sub.data) + pad
        self._names.append(sub.name or "")
        self.count += 1

    def _name_table(self) -> bytes:
        if not self.names:
            return b""
        offsets = bytearray()
        strings = bytearray()
        base = 4 * len(self._names)
        for name in self._names:
            offsets += struct.pack("<I", base + len(strings))
            strings += name.encode("utf-8") + b"\0"
        table = offsets + strings
        hlen = len(self.header_template)
        pad = align32(hlen + len(self._sample_headers) + len(table)) - (
            hlen + len(self._sample_headers) + len(table)
        )
        return bytes(table) + b"\0" * pad

    def head(self) -> bytes:
        names = self._name_table()
        if self._data_size > 0xFFFFFFFF:
            raise Fsb5Error("FSB5 data section exceeds the 4 GB format limit")
        header = bytearray(self.header_template)
        struct.pack_into(
            "<4s6I",
            header,
            0,
            MAGIC,
            self.version,
            self.count,
            len(self._sample_headers),
            len(names),
            self._data_size,
            self.codec,
        )
        if self.codec == CODEC_PCM16 and self.little_endian_pcm:  # bit 0 of flags = big-endian PCM16
            flags = struct.unpack_from("<I", header, 0x20)[0]
            struct.pack_into("<I", header, 0x20, flags & ~1)
        return bytes(header) + bytes(self._sample_headers) + names

    @property
    def size(self) -> int:
        return len(self.head()) + self._data_size

    def write_to(self, fh: BinaryIO) -> None:
        fh.write(self.head())
        self.spool.flush()
        self.spool.seek(0)
        remaining = self._data_size
        while remaining:
            block = self.spool.read(min(remaining, 8 << 20))
            if not block:
                raise Fsb5Error("FSB5 spool ended early")
            fh.write(block)
            remaining -= len(block)

    def to_bytes(self) -> bytes:
        out = io.BytesIO()
        self.write_to(out)
        return out.getvalue()


def rebuild_verbatim(buf: bytes | bytearray | memoryview, offset: int = 0) -> bytes:
    """Re-serialize an FSB5 from its own parts (round-trip self-check of parser and writer)."""
    fsb = parse_fsb5(buf, offset)
    writer = Fsb5Writer(fsb.codec, fsb.header, names=fsb.has_names, little_endian_pcm=not fsb.big_endian_pcm)
    mv = memoryview(buf)
    for sub in fsb.subsounds:
        start = offset + fsb.data_start + sub.data_offset
        writer.add(
            NewSubsound(
                sub.name,
                sub.rate,
                sub.channels,
                sub.nsamples,
                bytes(mv[start : start + sub.data_size]),
                [c for c in sub.chunks if c[0] not in (CHUNK_CHANNELS, CHUNK_FREQUENCY)],
            )
        )
    return writer.to_bytes()
