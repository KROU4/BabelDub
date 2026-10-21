"""Read-only parsers for Wwise containers (no decoding).

.bnk  - chunks: 4-byte tag + u32 size. BKHD (u32 version, u32 bank id, u32 language id, ...),
        DIDX (12-byte entries: u32 media id, u32 offset into DATA payload, u32 size),
        DATA (embedded .wem media), HIRC (u32 count + objects), STID, INIT, ...
.pck  - "AKPK", u32 header size (bytes after offset 8), u32 version/flag (1), then section sizes:
        languages, banks, sounds[, externals]. Language map: u32 count, {u32 offset, u32 id}*,
        strings (UTF-16LE or UTF-8). Each file table: u32 count, entries of
        {u32 id (u64 for 64-bit ids), u32 block size, u32 size, u32 offset in blocks, u32 language id}.
.wem  - RIFF/RIFX "WAVE" with a 'fmt ' chunk; the format tag selects the codec (0xFFFF Vorbis,
        0x0002 ADPCM, 0x3040 Opus, 0x0001/0xFFFE PCM, ...).

Console builds may be big-endian; endianness is detected from header plausibility.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import BinaryIO

CODECS: dict[int, str] = {
    0x0001: "pcm",
    0xFFFE: "pcm",
    0x0002: "adpcm",
    0x0069: "ima",
    0x8311: "ptadpcm",
    0xFFFF: "vorbis",
    0x3039: "opusnx",
    0x3040: "opus",
    0x3041: "opus",
    0x0165: "xma",
    0x0166: "xma2",
    0xFFF0: "dsp",
    0xAAC0: "aac",
    0x00FF: "aac",
    0xFFFB: "hevag",
    0xFFFC: "atrac9",
}
PCM_TAGS = (0x0001, 0xFFFE)


class WwiseFormatError(ValueError):
    """The bytes do not look like the expected Wwise structure."""


@dataclass
class MediaEntry:
    media_id: int
    offset: int  # absolute offset in the file
    size: int


@dataclass
class BankInfo:
    version: int
    bank_id: int
    language_id: int | None
    endian: str  # "<" or ">"
    media: list[MediaEntry] = field(default_factory=list)
    data_offset: int | None = None
    data_size: int = 0
    hirc_objects: int | None = None
    chunks: list[str] = field(default_factory=list)


@dataclass
class PackEntry:
    file_id: int
    offset: int  # absolute
    size: int
    language_id: int
    table: str  # "bank" | "sound" | "external"


@dataclass
class PackInfo:
    version: int
    endian: str
    languages: dict[int, str] = field(default_factory=dict)
    banks: list[PackEntry] = field(default_factory=list)
    sounds: list[PackEntry] = field(default_factory=list)
    externals: list[PackEntry] = field(default_factory=list)


@dataclass
class WemInfo:
    codec: str
    format_tag: int
    sr: int
    ch: int
    dur_s: float | None
    bits: int = 0
    big_endian: bool = False
    data_offset: int | None = None  # relative to the start of the .wem
    data_size: int | None = None


def _read_exact(fh: BinaryIO, size: int) -> bytes:
    data = fh.read(size)
    if len(data) != size:
        raise WwiseFormatError("unexpected end of file")
    return data


def read_bank(fh: BinaryIO, base: int = 0, size: int | None = None) -> BankInfo:
    """Parse a soundbank that starts at absolute offset `base` (a .bnk file or a bank inside a .pck)."""
    fh.seek(base)
    head = fh.read(16)
    if len(head) < 16 or head[:4] != b"BKHD":
        raise WwiseFormatError("not a Wwise soundbank (missing BKHD)")
    endian = "<"
    (version,) = struct.unpack_from("<I", head, 8)
    if version > 0x1000:
        (be_version,) = struct.unpack_from(">I", head, 8)
        if be_version <= 0x1000:
            endian, version = ">", be_version
    end = base + size if size is not None else None
    if end is None:
        fh.seek(0, 2)
        end = fh.tell()
    pos = base
    bank_id = 0
    language_id: int | None = None
    info = BankInfo(version=version, bank_id=0, language_id=None, endian=endian)
    didx: bytes = b""
    while pos + 8 <= end:
        fh.seek(pos)
        hdr = fh.read(8)
        if len(hdr) < 8:
            break
        tag = hdr[:4]
        (chunk_size,) = struct.unpack_from(endian + "I", hdr, 4)
        payload = pos + 8
        if payload + chunk_size > end or not all(32 <= b < 127 for b in tag):
            raise WwiseFormatError(f"corrupt chunk at {pos:#x}")
        name = tag.decode("ascii")
        info.chunks.append(name)
        if name == "BKHD":
            body = _read_exact(fh, min(chunk_size, 12))
            if len(body) >= 8:
                bank_id = struct.unpack_from(endian + "I", body, 4)[0]
            if len(body) >= 12:
                language_id = struct.unpack_from(endian + "I", body, 8)[0]
        elif name == "DIDX":
            didx = _read_exact(fh, chunk_size)
        elif name == "DATA":
            info.data_offset = payload
            info.data_size = chunk_size
        elif name == "HIRC" and chunk_size >= 4:
            info.hirc_objects = struct.unpack(endian + "I", _read_exact(fh, 4))[0]
        pos = payload + chunk_size
    info.bank_id = bank_id
    info.language_id = language_id
    if didx:
        if info.data_offset is None:
            raise WwiseFormatError("DIDX without DATA chunk")
        for i in range(len(didx) // 12):
            media_id, offset, msize = struct.unpack_from(endian + "III", didx, i * 12)
            if offset + msize > info.data_size:
                raise WwiseFormatError(f"media {media_id} points outside DATA")
            info.media.append(MediaEntry(media_id=media_id, offset=info.data_offset + offset, size=msize))
    return info


def _read_string(blob: bytes, offset: int) -> str:
    if offset >= len(blob):
        return ""
    if offset + 1 < len(blob) and blob[offset + 1] == 0:  # UTF-16LE
        end = offset
        while end + 1 < len(blob) and blob[end : end + 2] != b"\x00\x00":
            end += 2
        return blob[offset:end].decode("utf-16-le", errors="replace")
    end = blob.find(b"\x00", offset)
    return blob[offset : end if end >= 0 else len(blob)].decode("utf-8", errors="replace")


def _read_table(blob: bytes, endian: str, table: str) -> list[PackEntry]:
    if len(blob) < 4:
        return []
    (count,) = struct.unpack_from(endian + "I", blob, 0)
    if count == 0:
        return []
    entry_size = (len(blob) - 4) // count
    if entry_size not in (20, 24) or 4 + count * entry_size > len(blob):
        raise WwiseFormatError(f"unexpected {table} table layout ({len(blob)} bytes, {count} entries)")
    out: list[PackEntry] = []
    for i in range(count):
        off = 4 + i * entry_size
        if entry_size == 24:
            file_id, block, size, start, lang = struct.unpack_from(endian + "QIIII", blob, off)
        else:
            file_id, block, size, start, lang = struct.unpack_from(endian + "IIIII", blob, off)
        out.append(
            PackEntry(file_id=file_id, offset=start * max(block, 1), size=size, language_id=lang, table=table)
        )
    return out


def read_pack(fh: BinaryIO) -> PackInfo:
    """Parse the header of an AKPK file package."""
    fh.seek(0)
    head = fh.read(32)
    if len(head) < 24 or head[:4] != b"AKPK":
        raise WwiseFormatError("not a Wwise file package (missing AKPK)")
    endian = "<"
    (version,) = struct.unpack_from("<I", head, 8)
    if version > 0x1000:
        endian = ">"
        (version,) = struct.unpack_from(">I", head, 8)
    header_size, _ver, lang_size, bank_size, sound_size = struct.unpack_from(endian + "5I", head, 4)
    ext_size = 0
    if 4 + 12 + lang_size + bank_size + sound_size == header_size:
        tables_at = 0x18
    else:
        (ext_size,) = struct.unpack_from(endian + "I", head, 0x18)
        if 4 + 16 + lang_size + bank_size + sound_size + ext_size != header_size:
            raise WwiseFormatError("AKPK section sizes do not add up to the header size")
        tables_at = 0x1C
    fh.seek(tables_at)
    lang_blob = _read_exact(fh, lang_size)
    bank_blob = _read_exact(fh, bank_size)
    sound_blob = _read_exact(fh, sound_size)
    ext_blob = _read_exact(fh, ext_size) if ext_size else b""
    info = PackInfo(version=version, endian=endian)
    if len(lang_blob) >= 4:
        (count,) = struct.unpack_from(endian + "I", lang_blob, 0)
        if 4 + count * 8 > len(lang_blob):
            raise WwiseFormatError("corrupt language map")
        for i in range(count):
            str_off, lang_id = struct.unpack_from(endian + "II", lang_blob, 4 + i * 8)
            info.languages[lang_id] = _read_string(lang_blob, str_off)
    info.banks = _read_table(bank_blob, endian, "bank")
    info.sounds = _read_table(sound_blob, endian, "sound")
    info.externals = _read_table(ext_blob, endian, "external")
    return info


def wem_info(head: bytes, total_size: int) -> WemInfo:
    """Codec, rate, channels and (when knowable without decoding) duration of a .wem."""
    if len(head) < 12 or head[:4] not in (b"RIFF", b"RIFX") or head[8:12] not in (b"WAVE", b"XWMA"):
        raise WwiseFormatError("not a RIFF/RIFX WAVE stream")
    endian = ">" if head[:4] == b"RIFX" else "<"
    pos = 12
    fmt: bytes | None = None
    vorb: bytes | None = None
    data_offset: int | None = None
    data_size: int | None = None
    while pos + 8 <= len(head):
        tag = head[pos : pos + 4]
        (size,) = struct.unpack_from(endian + "I", head, pos + 4)
        body = head[pos + 8 : pos + 8 + size]
        if tag == b"fmt ":
            fmt = body
        elif tag == b"vorb":
            vorb = body
        elif tag == b"data":
            data_offset, data_size = pos + 8, size
            break
        pos += 8 + size + (size & 1)
    if fmt is None or len(fmt) < 16:
        raise WwiseFormatError("missing 'fmt ' chunk")
    tag, channels, rate, avg_bytes, block_align, bits = struct.unpack_from(endian + "HHIIHH", fmt, 0)
    if data_size is None:
        data_size = max(total_size - pos - 8, 0)
    if data_offset is not None:
        data_size = min(data_size, max(total_size - data_offset, 0))
    num_samples: int | None = None
    if vorb is not None and len(vorb) >= 4:
        num_samples = struct.unpack_from(endian + "I", vorb, 0)[0]
    elif tag in (0xFFFF, 0x3040, 0x3041) and len(fmt) >= 0x1C:
        num_samples = struct.unpack_from(endian + "I", fmt, 0x18)[0]
    dur: float | None = None
    if rate > 0:
        if num_samples:
            dur = num_samples / rate
        elif tag in PCM_TAGS and block_align > 0:
            dur = data_size / block_align / rate
        elif avg_bytes > 0:
            dur = data_size / avg_bytes
    return WemInfo(
        codec=CODECS.get(tag, f"0x{tag:04x}"),
        format_tag=tag,
        sr=rate,
        ch=channels,
        dur_s=round(dur, 3) if dur is not None else None,
        bits=bits,
        big_endian=endian == ">",
        data_offset=data_offset,
        data_size=data_size,
    )


def pcm_samples(wem: bytes) -> tuple[bytes, int, int, int]:
    """Raw interleaved PCM of a PCM .wem: (frames bytes, sample rate, channels, bits)."""
    info = wem_info(wem, len(wem))
    if info.format_tag not in PCM_TAGS or info.data_offset is None or info.big_endian:
        raise WwiseFormatError("not a little-endian PCM .wem")
    data = wem[info.data_offset : info.data_offset + (info.data_size or 0)]
    return data, info.sr, info.ch, info.bits
