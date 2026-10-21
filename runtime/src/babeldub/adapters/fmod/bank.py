"""FMOD Studio .bank containers (RIFF 'FEV ') and raw .fsb files: chunk index, FSB locations, rewrite.

.bank layout (vgmstream src/meta/fsb5_fev.c; checked on FMOD 2.03 banks):
  RIFF size 'FEV '
    'FMT '  u32 bank format version, u32 compatible version
    'LIST' 'PROJ'  metadata (BNKI bank GUID, events, buses, PRIS/PRIT/PRIB programmer instruments,
                   SNDH, STDT string table, STBL audio table, HASH ...)
    'SND '  zero padding so that the FSB5 starts on a 32-byte boundary *of the file*, then the FSB5
  SNDH payload: u16 (count << 1 | 1), u16 entry size (8), then count x (u32 absolute FSB5 offset,
  u32 FSB5 size). Chunks with an odd size are followed by one pad byte.
Rewriting keeps every byte before the first 'SND ' chunk except the RIFF size and the SNDH entries,
re-emits SND chunks with fresh alignment padding and copies any later chunk verbatim.
"""

from __future__ import annotations

import os
import struct
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol

from babeldub.adapters.fmod.fsb5 import MAGIC as FSB_MAGIC
from babeldub.adapters.fmod.fsb5 import Fsb5Error, read_fsb5


class BankError(ValueError):
    """Malformed or unsupported FMOD bank."""


@dataclass
class Chunk:
    cid: bytes
    pos: int  # absolute offset of the 8-byte chunk header
    size: int
    list_type: bytes | None = None
    children: list[Chunk] = field(default_factory=list)

    @property
    def data_pos(self) -> int:
        return self.pos + 8

    @property
    def end(self) -> int:
        return self.pos + 8 + self.size + (self.size & 1)

    @property
    def name(self) -> str:
        return self.cid.decode("latin1")


@dataclass
class FsbLocation:
    index: int
    offset: int  # absolute offset of the FSB5 magic (or of the encrypted blob)
    size: int
    chunk: Chunk | None = None  # enclosing 'SND ' chunk (banks only)


class FsbPayload(Protocol):
    @property
    def size(self) -> int: ...

    def write_to(self, fh: BinaryIO) -> None: ...


@dataclass
class BytesPayload:
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    def write_to(self, fh: BinaryIO) -> None:
        fh.write(self.data)


def _is_chunk_id(raw: bytes) -> bool:
    return len(raw) == 4 and all(32 <= b < 127 for b in raw)


def _walk(buf: bytes, start: int, end: int, base: int) -> list[Chunk]:
    """Parse chunks in buf[start:end]; `base` is the absolute file offset of buf[0]."""
    out: list[Chunk] = []
    pos = start
    while pos + 8 <= end:
        cid = buf[pos : pos + 4]
        if not _is_chunk_id(cid):
            raise BankError(f"bad chunk id at {base + pos}")
        (size,) = struct.unpack_from("<I", buf, pos + 4)
        if pos + 8 + size > end:
            raise BankError(f"chunk {cid!r} at {base + pos} overruns its parent")
        chunk = Chunk(cid, base + pos, size)
        if cid == b"LIST" and size >= 4:
            chunk.list_type = buf[pos + 8 : pos + 12]
            chunk.children = _walk(buf, pos + 12, pos + 8 + size, base)
        out.append(chunk)
        nxt = pos + 8 + size
        if size & 1:
            # RIFF pads odd chunks; tolerate writers that do not
            if nxt + 1 <= end and (nxt + 1 == end or _is_chunk_id(buf[nxt + 1 : nxt + 5])):
                nxt += 1
        pos = nxt
    return out


@dataclass
class Container:
    path: Path
    kind: str  # "bank" | "fsb"
    file_size: int
    chunks: list[Chunk]
    fsbs: list[FsbLocation]
    sndh: Chunk | None = None
    sndh_header: int = 0  # bytes before the (offset, size) entries
    sndh_stride: int = 8
    fmt_version: int | None = None
    bank_guid: str | None = None
    encrypted: bool = False
    meta: dict[int, bytes] = field(default_factory=dict)  # pos -> payload of non-SND chunks

    def find(self, cid: bytes, list_path: tuple[bytes, ...] | None = None) -> list[Chunk]:
        """All chunks with this id (depth-first). list_path restricts to LIST types along the way."""
        found: list[Chunk] = []

        def rec(items: list[Chunk], trail: tuple[bytes, ...]) -> None:
            for ch in items:
                if ch.cid == cid and (list_path is None or trail[-len(list_path) :] == list_path):
                    found.append(ch)
                if ch.children:
                    rec(ch.children, trail + ((ch.list_type or b""),))

        rec(self.chunks, ())
        return found

    def payload(self, chunk: Chunk) -> bytes:
        """Payload bytes of a metadata chunk (held in memory) or read from disk."""
        for top in self.chunks:
            if top.pos <= chunk.pos < top.end and top.pos in self.meta:
                data = self.meta[top.pos]
                rel = chunk.data_pos - top.data_pos
                return data[rel : rel + chunk.size]
        return self.read(chunk.data_pos, chunk.size)

    def read(self, pos: int, size: int) -> bytes:
        with self.path.open("rb") as fh:
            fh.seek(pos)
            return fh.read(size)


def _parse_sndh(
    payload: bytes, file_size: int, peek: Callable[[int], bytes]
) -> tuple[int, int, list[tuple[int, int]]]:
    """Return (header bytes, stride, [(offset, size)]); prefers the layout whose entries hit FSB5 magic."""
    layouts: list[tuple[int, int]] = []
    if len(payload) >= 4:
        cnt_raw, elem = struct.unpack_from("<HH", payload, 0)
        if cnt_raw & 1 and elem >= 8 and 4 + (cnt_raw >> 1) * elem == len(payload):
            layouts.append((4, elem))
    if len(payload) % 8 == 0:
        layouts.append((0, 8))
    if len(payload) >= 4 and (len(payload) - 4) % 8 == 0:
        layouts.append((4, 8))
    best: tuple[int, int, list[tuple[int, int]]] | None = None
    for head, stride in layouts:
        n = (len(payload) - head) // stride
        entries = [struct.unpack_from("<II", payload, head + k * stride) for k in range(n)]
        if not entries or any(off + size > file_size or size < 0x3C for off, size in entries):
            continue
        hits = sum(1 for off, _ in entries if peek(off) == FSB_MAGIC)
        if hits == len(entries):
            return head, stride, entries
        if best is None:
            best = (head, stride, entries)
    if best is None:
        if not payload:
            return 0, 8, []
        raise BankError("SNDH chunk has an unknown layout")
    return best


def open_container(path: Path) -> Container:
    """Index an FMOD .bank (RIFF FEV) or raw .fsb without reading sample data."""
    path = Path(path)
    file_size = path.stat().st_size
    with path.open("rb") as fh:
        head = fh.read(12)
        if head[:4] == FSB_MAGIC:
            return _open_raw_fsb(path, fh, file_size)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"FEV ":
            raise BankError("not an FMOD Studio bank (RIFF 'FEV ') or FSB5 file")
        (riff_size,) = struct.unpack_from("<I", head, 4)
        end = min(file_size, 8 + riff_size)
        top: list[Chunk] = []
        meta: dict[int, bytes] = {}
        pos = 12
        while pos + 8 <= end:
            fh.seek(pos)
            hdr = fh.read(8)
            cid = hdr[:4]
            (size,) = struct.unpack_from("<I", hdr, 4)
            if not _is_chunk_id(cid) or pos + 8 + size > end:
                raise BankError(f"bad top-level chunk at {pos}")
            chunk = Chunk(cid, pos, size)
            if cid != b"SND ":
                data = fh.read(size)
                meta[pos] = data
                if cid == b"LIST" and size >= 4:
                    chunk.list_type = data[:4]
                    chunk.children = _walk(data, 4, size, pos + 8)
            top.append(chunk)
            pos = chunk.end if chunk.end <= end else pos + 8 + size

        def peek(off: int) -> bytes:
            fh.seek(off)
            return fh.read(4)

        cont = Container(path=path, kind="bank", file_size=file_size, chunks=top, fsbs=[], meta=meta)
        fmt = [c for c in top if c.cid == b"FMT "]
        if fmt and fmt[0].size >= 4:
            cont.fmt_version = struct.unpack_from("<I", meta[fmt[0].pos], 0)[0]
        bnki = cont.find(b"BNKI")
        if bnki and bnki[0].size >= 16:
            cont.bank_guid = str(uuid.UUID(bytes_le=cont.payload(bnki[0])[:16]))
        snd_chunks = [c for c in top if c.cid == b"SND "]
        sndh = cont.find(b"SNDH")
        if sndh:
            cont.sndh = sndh[0]
            head_len, stride, entries = _parse_sndh(cont.payload(sndh[0]), file_size, peek)
            cont.sndh_header, cont.sndh_stride = head_len, stride
            for k, (off, size) in enumerate(entries):
                owner = next(
                    (c for c in snd_chunks if c.data_pos <= off and off + size <= c.data_pos + c.size), None
                )
                cont.fsbs.append(FsbLocation(k, off, size, owner))
        else:
            for k, chunk in enumerate(snd_chunks):
                fh.seek(chunk.data_pos)
                blob = fh.read(min(chunk.size, 64))
                at = blob.find(FSB_MAGIC)
                if at < 0:
                    cont.encrypted = True
                    at = (-chunk.data_pos) % 32
                cont.fsbs.append(FsbLocation(k, chunk.data_pos + at, chunk.size - at, chunk))
        for loc in cont.fsbs:
            if peek(loc.offset) != FSB_MAGIC:
                cont.encrypted = True
        return cont


def _open_raw_fsb(path: Path, fh: BinaryIO, file_size: int) -> Container:
    fsbs: list[FsbLocation] = []
    pos = 0
    while pos + 0x3C <= file_size:
        fh.seek(pos)
        if fh.read(4) != FSB_MAGIC:
            break
        try:
            info = read_fsb5(fh, pos)
        except Fsb5Error as exc:
            raise BankError(f"bad FSB5 at {pos}: {exc}") from exc
        fsbs.append(FsbLocation(len(fsbs), pos, info.total_size))
        pos += info.total_size
    return Container(path=path, kind="fsb", file_size=file_size, chunks=[], fsbs=fsbs)


def _copy_range(src: BinaryIO, dst: BinaryIO, pos: int, size: int) -> None:
    src.seek(pos)
    remaining = size
    while remaining:
        block = src.read(min(remaining, 8 << 20))
        if not block:
            raise BankError("source file ended early")
        dst.write(block)
        remaining -= len(block)


def write_container(cont: Container, new_fsbs: dict[int, FsbPayload], out_path: Path) -> int:
    """Write a copy of `cont` with FSB #k replaced by new_fsbs[k]; returns the output size."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".part")
    try:
        with cont.path.open("rb") as src, tmp.open("wb") as dst:
            if cont.kind == "fsb":
                size = _write_raw_fsb(cont, new_fsbs, src, dst)
            else:
                size = _write_bank(cont, new_fsbs, src, dst)
        os.replace(tmp, out_path)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    return size


def _write_raw_fsb(cont: Container, new_fsbs: dict[int, FsbPayload], src: BinaryIO, dst: BinaryIO) -> int:
    written = 0
    pos = 0
    for loc in cont.fsbs:
        if loc.index in new_fsbs:
            new_fsbs[loc.index].write_to(dst)
            written += new_fsbs[loc.index].size
        else:
            _copy_range(src, dst, loc.offset, loc.size)
            written += loc.size
        pos = loc.offset + loc.size
    if pos < cont.file_size:
        _copy_range(src, dst, pos, cont.file_size - pos)
        written += cont.file_size - pos
    return written


def _write_bank(cont: Container, new_fsbs: dict[int, FsbPayload], src: BinaryIO, dst: BinaryIO) -> int:
    if cont.encrypted:
        raise BankError("bank sample data is encrypted")
    snd_chunks = [c for c in cont.chunks if c.cid == b"SND "]
    if not snd_chunks:
        if new_fsbs:
            raise BankError("bank has no SND chunks")
        _copy_range(src, dst, 0, cont.file_size)
        return cont.file_size
    first = snd_chunks[0].pos
    if cont.sndh is not None and cont.sndh.pos >= first:
        raise BankError("SNDH located after sample data is not supported")
    by_chunk = {loc.chunk.pos: loc for loc in cont.fsbs if loc.chunk is not None}
    if len(by_chunk) != len(cont.fsbs):
        raise BankError("SNDH entries do not map one-to-one onto SND chunks")
    # plan the layout after the metadata prefix
    plan: list[tuple[Chunk, FsbLocation | None, int, int, int]] = []  # chunk, fsb, pad, fsb_size, trailing
    new_offsets: dict[int, tuple[int, int]] = {}
    pos = first
    tail = [c for c in cont.chunks if c.pos >= first]
    for chunk in tail:
        loc = by_chunk.get(chunk.pos) if chunk.cid == b"SND " else None
        if loc is None:
            plan.append((chunk, None, 0, 0, 0))
            pos += chunk.end - chunk.pos
            continue
        fsb_size = new_fsbs[loc.index].size if loc.index in new_fsbs else loc.size
        trailing = chunk.data_pos + chunk.size - (loc.offset + loc.size)
        pad = (-(pos + 8)) % 32
        new_offsets[loc.index] = (pos + 8 + pad, fsb_size)
        body = pad + fsb_size + trailing
        plan.append((chunk, loc, pad, fsb_size, trailing))
        pos += 8 + body + (body & 1)
    total = pos
    if total - 8 > 0xFFFFFFFF:
        raise BankError("rebuilt bank exceeds the 4 GB RIFF limit")
    src.seek(0)
    prefix = bytearray(src.read(first))
    struct.pack_into("<I", prefix, 4, total - 8)
    if cont.sndh is not None:
        for k, (off, size) in new_offsets.items():
            at = cont.sndh.data_pos + cont.sndh_header + k * cont.sndh_stride
            struct.pack_into("<II", prefix, at, off, size)
    dst.write(prefix)
    for chunk, loc, pad, fsb_size, trailing in plan:
        if loc is None:
            _copy_range(src, dst, chunk.pos, chunk.end - chunk.pos)
            continue
        body = pad + fsb_size + trailing
        dst.write(chunk.cid + struct.pack("<I", body) + b"\0" * pad)
        if loc.index in new_fsbs:
            new_fsbs[loc.index].write_to(dst)
        else:
            _copy_range(src, dst, loc.offset, loc.size)
        if trailing:
            _copy_range(src, dst, loc.offset + loc.size, trailing)
        if body & 1:
            dst.write(b"\0")
    return total
