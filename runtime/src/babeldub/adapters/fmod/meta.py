"""Small FMOD Studio metadata chunks used by the adapter.

PRIB (inside LIST PRIS/PRIT, one per programmer instrument): GUID(16), u16 length, UTF-8 string.
  In localized-audio-table games the string is the audio-table key, usually the event path
  without the 'event:/' prefix ("Dialogue/Act2/Maciek/dlg_..."); it may be empty.
STBL (audio table): u32 version, u32 ?, u16 count, count x u64 key hashes (sorted),
  u16 count, count x u24 subsound index (a permutation of 0..count-1), u32 trailing value.
  The key hash function is unknown, so the subsound order of the audio-table FSB must never change.
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass, field

from babeldub.adapters.fmod.bank import Container


@dataclass
class AudioTable:
    count: int
    indices: list[int] = field(default_factory=list)  # subsound index per (sorted) key hash

    @property
    def is_permutation(self) -> bool:
        return sorted(self.indices) == list(range(self.count))


def parse_prib(payload: bytes) -> tuple[str | None, str]:
    """(instrument GUID, key string) of a PRIB chunk."""
    if len(payload) < 18:
        return None, ""
    guid = str(uuid.UUID(bytes_le=payload[:16]))
    (length,) = struct.unpack_from("<H", payload, 16)
    key = payload[18 : 18 + length].split(b"\0", 1)[0].decode("utf-8", "replace")
    return guid, key


def programmer_keys(cont: Container) -> list[str]:
    """Non-empty programmer-instrument keys of a bank, in file order."""
    keys: list[str] = []
    for chunk in cont.find(b"PRIB"):
        _, key = parse_prib(cont.payload(chunk))
        if key:
            keys.append(key)
    return keys


def parse_stbl(payload: bytes) -> AudioTable | None:
    if len(payload) < 10:
        return None
    count = struct.unpack_from("<H", payload, 8)[0]
    pos = 10 + 8 * count
    if pos + 2 > len(payload):
        return None
    count2 = struct.unpack_from("<H", payload, pos)[0]
    pos += 2
    if count2 != count or pos + 3 * count > len(payload):
        return None
    idx = [int.from_bytes(payload[pos + 3 * i : pos + 3 * i + 3], "little") for i in range(count)]
    return AudioTable(count, idx)


def audio_table(cont: Container) -> AudioTable | None:
    for chunk in cont.find(b"STBL"):
        if chunk.size:
            table = parse_stbl(cont.payload(chunk))
            if table and table.count:
                return table
    return None


def audio_table_fsb(cont: Container, counts: list[int]) -> int | None:
    """Index of the FSB that backs the audio table (its subsound count equals the table size)."""
    table = audio_table(cont)
    if table is None or not table.is_permutation:
        return None
    matches = [k for k, n in enumerate(counts) if n == table.count]
    return matches[-1] if matches else None
