"""FMOD Studio string tables (STDT chunk of Master.strings.bank / *.strings.bank).

STDT maps GUIDs to paths (event:/, bus:/, bank:/, vca:/, snapshot:/, parameter:/) with a compressed
radix trie (layout reverse-engineered on FMOD 2.03 banks, format version 146):
  u32 version (1)
  array of nodes, 8 bytes each (node 0 = root):
      u24 fragment offset in the blob (0xFFFFFF = none), u8 first char lower-cased (0 = terminator)
      u24 first child index - or, for a leaf / terminator, the string index; u8 child count
  array of GUIDs (16 bytes, little-endian UUID layout), indexed by string index
  byte array: NUL-terminated fragments (original case)
  (leaf lookup tables follow; not needed here)
Arrays are serialized as u16 (count << 1 | 1) [+ u16 element size for struct arrays]; a u32
(count << 1) form is accepted for counts that do not fit 15 bits.
A path is the concatenation of fragments along the root -> terminator walk.
"""

from __future__ import annotations

import re
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path

from babeldub.adapters.fmod.bank import BankError, open_container

_NONE = 0xFFFFFF


@dataclass
class StringEntry:
    path: str
    index: int
    guid: str | None


def _array_count(buf: bytes, pos: int) -> tuple[int, int]:
    (short,) = struct.unpack_from("<H", buf, pos)
    if short & 1:
        return short >> 1, pos + 2
    (long_,) = struct.unpack_from("<I", buf, pos)
    return long_ >> 1, pos + 4


def _layout(payload: bytes) -> tuple[int, int, int, int, int, int]:
    """(nodes_pos, n_nodes, guids_pos, n_guids, blob_pos, blob_len) of an STDT payload."""
    n_nodes, pos = _array_count(payload, 4)
    (elem,) = struct.unpack_from("<H", payload, pos)
    if elem != 8:
        raise BankError(f"STDT: unexpected node size {elem}")
    nodes_pos = pos + 2
    pos = nodes_pos + 8 * n_nodes
    n_guids, pos = _array_count(payload, pos)
    (gelem,) = struct.unpack_from("<H", payload, pos)
    if gelem != 16:
        raise BankError(f"STDT: unexpected GUID size {gelem}")
    guids_pos = pos + 2
    pos = guids_pos + 16 * n_guids
    blob_len, blob_pos = _array_count(payload, pos)
    if blob_pos + blob_len > len(payload):
        raise BankError("STDT: string blob overruns the chunk")
    return nodes_pos, n_nodes, guids_pos, n_guids, blob_pos, blob_len


def _fallback_blob(payload: bytes, guids_end: int) -> tuple[int, int]:
    """Locate the blob by its first fragment ('<prefix>:/') when the array header is not understood."""
    m = re.compile(rb"[a-z]\x00?[a-z]{1,12}:/\x00").search(payload, guids_end)
    if not m:
        raise BankError("STDT: string blob not found")
    return m.start(), len(payload) - m.start()


def parse_stdt(payload: bytes) -> list[StringEntry]:
    if len(payload) < 12:
        return []
    (version,) = struct.unpack_from("<I", payload, 0)
    if version != 1:
        raise BankError(f"STDT: unsupported version {version}")
    nodes_pos, n_nodes, guids_pos, n_guids, blob_pos, blob_len = _layout(payload)

    def node(i: int) -> tuple[int, int, int, int]:
        a, b = struct.unpack_from("<II", payload, nodes_pos + 8 * i)
        return a & 0xFFFFFF, a >> 24, b & 0xFFFFFF, b >> 24

    def consistent(blob: bytes) -> bool:
        # every fragment starts with its node's (lower-cased) search key
        for i in range(1, min(n_nodes, 64)):
            f, key, _, _ = node(i)
            if not key or f == _NONE:
                continue
            if f >= len(blob):
                return False
            ch = blob[f] + 32 if 65 <= blob[f] <= 90 else blob[f]
            if ch != key:
                return False
        return True

    blob = payload[blob_pos : blob_pos + blob_len]
    if not consistent(blob):
        blob_pos, blob_len = _fallback_blob(payload, guids_pos + 16 * n_guids)
        blob = payload[blob_pos : blob_pos + blob_len]
        if not consistent(blob):
            raise BankError("STDT: string blob does not match the trie")

    def frag(off: int) -> str:
        if off == _NONE or off >= len(blob):
            return ""
        end = blob.find(b"\0", off)
        return blob[off : end if end >= 0 else len(blob)].decode("utf-8", "replace")

    out: list[StringEntry] = []
    stack: list[tuple[int, str]] = [(0, "")]
    seen = 0
    while stack:
        i, prefix = stack.pop()
        seen += 1
        if seen > 4 * n_nodes + 16 or i >= n_nodes:
            raise BankError("STDT: malformed trie")
        f, key, child, nch = node(i)
        text = prefix + (frag(f) if i != 0 else "")
        if i != 0 and (key == 0 or nch == 0):
            guid = None
            if 0 <= child < n_guids:
                raw = payload[guids_pos + 16 * child : guids_pos + 16 * child + 16]
                guid = str(uuid.UUID(bytes_le=raw))
            out.append(StringEntry(text, child, guid))
            continue
        for c in range(child + nch - 1, child - 1, -1):
            stack.append((c, text))
    out.sort(key=lambda e: e.index)
    return out


def read_strings_bank(path: Path) -> list[StringEntry]:
    """All string table entries of a bank (empty when the bank has no STDT data)."""
    cont = open_container(path)
    entries: list[StringEntry] = []
    for chunk in cont.find(b"STDT"):
        if chunk.size:
            try:
                entries.extend(parse_stdt(cont.payload(chunk)))
            except struct.error as exc:
                raise BankError(f"STDT: truncated ({exc})") from exc
    return entries


def event_paths(entries: list[StringEntry]) -> list[str]:
    return [e.path for e in entries if e.path.startswith("event:/")]
