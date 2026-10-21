"""Synthetic FMOD data for tests: FSB5 blobs, Studio banks (RIFF 'FEV ') and string banks.

Everything is generated on the fly; no game assets are involved.
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass, field

import numpy as np

from babeldub.adapters.fmod import fsb5 as F


def tone(n: int, sr: int, ch: int = 1, freq: float = 440.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(n) / sr
    mono = amp * np.sin(2 * np.pi * freq * t)
    out = np.stack([mono * (1.0 - 0.2 * c) for c in range(ch)], axis=1)
    return np.round(out * 32767).astype(np.int16)


@dataclass
class SubSpec:
    name: str | None
    rate: int
    pcm: np.ndarray  # int16 (n, ch)
    chunks: list[tuple[int, bytes]] = field(default_factory=list)


def make_fsb(codec: int, subs: list[SubSpec], header: bytes | None = None, names: bool = True) -> bytes:
    if header is None:
        # FMOD 2.03 style tail: u32 1 at 0x1C, flags 0, non-zero hash
        header = (
            F.MAGIC + struct.pack("<6I", 1, 0, 0, 0, 0, 0) + struct.pack("<II", 1, 0) + bytes(range(1, 25))
        )
    writer = F.Fsb5Writer(codec, header, names=names)
    for s in subs:
        data = F.encode_payload(codec, s.pcm)
        writer.add(F.NewSubsound(s.name, s.rate, s.pcm.shape[1], s.pcm.shape[0], data, s.chunks))
    return writer.to_bytes()


def chunk(cid: bytes, payload: bytes) -> bytes:
    out = cid + struct.pack("<I", len(payload)) + payload
    if len(payload) & 1:
        out += b"\0"
    return out


def list_chunk(list_type: bytes, children: bytes) -> bytes:
    return chunk(b"LIST", list_type + children)


def prib(key: str, guid: uuid.UUID | None = None) -> bytes:
    raw = key.encode("utf-8")
    return (guid or uuid.uuid4()).bytes_le + struct.pack("<H", len(raw)) + raw


def stbl(indices: list[int]) -> bytes:
    n = len(indices)
    hashes = sorted(np.random.default_rng(n).integers(1, 2**63, n).tolist())
    body = struct.pack("<IIH", 1, 1, n) + struct.pack(f"<{n}Q", *hashes) + struct.pack("<H", n)
    body += b"".join(i.to_bytes(3, "little") for i in indices)
    return body + struct.pack("<I", 4)  # 13 + 11n bytes: odd for even n -> exercises RIFF padding


def make_bank(
    fsbs: list[bytes],
    *,
    guid: uuid.UUID | None = None,
    prib_keys: list[str] | None = None,
    stbl_indices: list[int] | None = None,
    stdt: bytes = b"",
) -> bytes:
    """RIFF 'FEV ' bank: FMT, LIST PROJ (BNKI, PRIS, SNDH, STDT, STBL, HASH), SND chunks (32-byte aligned)."""
    guid = guid or uuid.UUID("dc8bc70b-effb-4ee2-aaed-ba47fdffd311")
    bnki = chunk(b"BNKI", guid.bytes_le + bytes(16))
    pris = b""
    if prib_keys:
        pris = list_chunk(b"PRIS", b"".join(list_chunk(b"PRIT", chunk(b"PRIB", prib(k))) for k in prib_keys))
    n = len(fsbs)
    sndh_payload = struct.pack("<HH", (n << 1) | 1, 8) + bytes(8 * n) if n else b""
    stbl_payload = stbl(stbl_indices) if stbl_indices is not None else b""
    proj_children = [
        bnki,
        pris,
        chunk(b"SNDH", sndh_payload),
        chunk(b"STDT", stdt),
        chunk(b"STBL", stbl_payload),
        chunk(b"HASH", struct.pack("<HH", 1, 20)),
    ]
    head = bytearray(b"RIFF\0\0\0\0FEV " + chunk(b"FMT ", struct.pack("<II", 146, 146)))
    head += list_chunk(b"PROJ", b"".join(proj_children))
    sndh_pos = head.find(b"SNDH") + 8 + 4
    out = bytearray(head)
    for k, fsb in enumerate(fsbs):
        pad = (-(len(out) + 8)) % 32
        out += b"SND " + struct.pack("<I", pad + len(fsb)) + bytes(pad)
        struct.pack_into("<II", out, sndh_pos + 8 * k, len(out), len(fsb))
        out += fsb
        if (pad + len(fsb)) & 1:
            out += b"\0"
    struct.pack_into("<I", out, 4, len(out) - 8)
    return bytes(out)


# --- STDT string table ------------------------------------------------------------------------
def _common_prefix(items: list[str]) -> str:
    first = items[0]
    n = len(first)
    for s in items[1:]:
        n = min(n, len(s))
        while s[:n] != first[:n]:
            n -= 1
    return first[:n]


def make_stdt(paths: list[str]) -> tuple[bytes, list[str]]:
    """STDT payload for the given paths; returns (payload, guid strings by string index)."""
    guids = [uuid.uuid5(uuid.NAMESPACE_URL, p) for p in paths]

    # node: [frag(str|None), key, children(list) | None, index]
    def children(items: list[tuple[str, int]]) -> list[dict]:
        out: list[dict] = []
        for rest, idx in items:
            if rest == "":
                out.append({"frag": None, "key": 0, "children": [], "index": idx})
        groups: dict[str, list[tuple[str, int]]] = {}
        for rest, idx in items:
            if rest:
                groups.setdefault(rest[0].lower(), []).append((rest, idx))
        for key in sorted(groups):
            grp = groups[key]
            if len(grp) == 1:
                rest, idx = grp[0]
                out.append({"frag": rest, "key": ord(key), "children": [], "index": idx})
            else:
                pre = _common_prefix([r for r, _ in grp])
                kids = children([(r[len(pre) :], i) for r, i in grp])
                out.append({"frag": pre, "key": ord(key), "children": kids, "index": None})
        return out

    root = {
        "frag": None,
        "key": 0,
        "children": children([(p, i) for i, p in enumerate(paths)]),
        "index": None,
    }
    order: list[dict] = [root]
    first_child: dict[int, int] = {}
    i = 0
    while i < len(order):
        node = order[i]
        if node["children"]:
            first_child[i] = len(order)
            order.extend(node["children"])
        i += 1
    blob = bytearray()
    nodes = bytearray()
    for i, node in enumerate(order):
        if node["frag"] is None:
            frag = 0xFFFFFF
        else:
            frag = len(blob)
            blob += node["frag"].encode("utf-8") + b"\0"
        if node["children"]:
            child, nch = first_child[i], len(node["children"])
        else:
            child, nch = (node["index"] if node["index"] is not None else 0), 0
        nodes += struct.pack("<II", frag | (node["key"] << 24), child | (nch << 24))
    payload = struct.pack("<I", 1)
    payload += struct.pack("<HH", (len(order) << 1) | 1, 8) + bytes(nodes)
    payload += struct.pack("<HH", (len(guids) << 1) | 1, 16) + b"".join(g.bytes_le for g in guids)
    payload += struct.pack("<H", (len(blob) << 1) | 1) + bytes(blob)
    return payload, [str(g) for g in guids]


def make_strings_bank(paths: list[str]) -> tuple[bytes, list[str]]:
    payload, guids = make_stdt(paths)
    return make_bank([], guid=uuid.UUID("0e092cf7-f980-092c-2722-0207420bce9e"), stdt=payload), guids
