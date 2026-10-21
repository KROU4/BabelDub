from __future__ import annotations

import struct
import uuid
from pathlib import Path

import pytest
from fmod_synth import SubSpec, make_bank, make_fsb, tone

from babeldub.adapters.fmod import fsb5 as F
from babeldub.adapters.fmod.bank import BankError, BytesPayload, open_container, write_container
from babeldub.adapters.fmod.meta import audio_table, audio_table_fsb, parse_prib, programmer_keys


def _fsbs() -> list[bytes]:
    sfx = make_fsb(F.CODEC_PCM16, [SubSpec("boom", 48000, tone(1000, 48000, 2))])
    voice = make_fsb(
        F.CODEC_FADPCM,
        [SubSpec(f"dlg_line_{i}", 48000, tone(700 + 300 * i, 48000, 1, 200.0 + 50 * i)) for i in range(3)],
    )
    return [sfx, voice]


def _bank(tmp_path: Path, name: str = "Dialogue_en-US.bank") -> Path:
    keys = [f"Dialogue/Act1/Hero/dlg_line_{i}" for i in range(3)]
    path = tmp_path / name
    path.write_bytes(make_bank(_fsbs(), prib_keys=keys, stbl_indices=[2, 0, 1]))
    return path


def test_open_bank_index(tmp_path: Path) -> None:
    path = _bank(tmp_path)
    cont = open_container(path)
    assert cont.kind == "bank" and cont.fmt_version == 146 and not cont.encrypted
    assert cont.bank_guid == "dc8bc70b-effb-4ee2-aaed-ba47fdffd311"
    assert cont.sndh_header == 4 and len(cont.fsbs) == 2
    data = path.read_bytes()
    for loc, fsb in zip(cont.fsbs, _fsbs(), strict=True):
        assert loc.offset % 32 == 0
        assert data[loc.offset : loc.offset + loc.size] == fsb
        assert loc.chunk is not None and loc.chunk.cid == b"SND "
    assert programmer_keys(cont) == [f"Dialogue/Act1/Hero/dlg_line_{i}" for i in range(3)]
    table = audio_table(cont)
    assert table is not None and table.count == 3 and table.is_permutation
    assert audio_table_fsb(cont, [1, 3]) == 1
    # the odd-sized STBL chunk is padded and the walk still reaches the following chunks
    assert cont.find(b"HASH")


def test_unchanged_rewrite_is_identical(tmp_path: Path) -> None:
    path = _bank(tmp_path)
    out = tmp_path / "out" / "same.bank"
    write_container(open_container(path), {}, out)
    assert out.read_bytes() == path.read_bytes()


def test_replace_fsb_patches_sndh_and_riff(tmp_path: Path) -> None:
    path = _bank(tmp_path)
    orig = path.read_bytes()
    cont = open_container(path)
    bigger = make_fsb(F.CODEC_PCM16, [SubSpec("boom", 48000, tone(5000, 48000, 2))])
    out = tmp_path / "patched.bank"
    size = write_container(cont, {0: BytesPayload(bigger)}, out)
    data = out.read_bytes()
    assert size == len(data)
    assert struct.unpack_from("<I", data, 4)[0] == len(data) - 8
    new = open_container(out)
    assert data[new.fsbs[0].offset : new.fsbs[0].offset + new.fsbs[0].size] == bigger
    old1 = cont.fsbs[1]
    assert (
        data[new.fsbs[1].offset : new.fsbs[1].offset + new.fsbs[1].size]
        == orig[old1.offset : old1.offset + old1.size]
    )
    assert all(loc.offset % 32 == 0 for loc in new.fsbs)
    # metadata before the first SND chunk only differs in RIFF size and SNDH entries
    first = cont.chunks[2].pos
    sndh = cont.sndh
    assert sndh is not None
    entries = slice(sndh.data_pos + 4, sndh.data_pos + 4 + 16)
    a, b = bytearray(orig[:first]), bytearray(data[:first])
    for buf in (a, b):
        buf[4:8] = bytes(4)
        buf[entries] = bytes(16)
    assert a == b


def test_raw_fsb_container(tmp_path: Path) -> None:
    fsbs = _fsbs()
    path = tmp_path / "sounds.fsb"
    path.write_bytes(fsbs[0] + fsbs[1])
    cont = open_container(path)
    assert cont.kind == "fsb" and [loc.offset for loc in cont.fsbs] == [0, len(fsbs[0])]
    out = tmp_path / "out.fsb"
    write_container(cont, {1: BytesPayload(fsbs[0])}, out)
    assert out.read_bytes() == fsbs[0] + fsbs[0]


def test_not_a_bank(tmp_path: Path) -> None:
    path = tmp_path / "x.bank"
    path.write_bytes(b"BKHD" + bytes(64))
    with pytest.raises(BankError):
        open_container(path)


def test_encrypted_bank_is_flagged(tmp_path: Path) -> None:
    path = tmp_path / "enc.bank"
    data = bytearray(make_bank(_fsbs()))
    path.write_bytes(bytes(data))
    at = open_container(path).fsbs[0].offset
    data[at : at + 4] = b"\x13\x37\x13\x37"  # scrambled magic, as with an FMOD encryption key
    path.write_bytes(bytes(data))
    cont = open_container(path)
    assert cont.encrypted
    with pytest.raises(BankError):
        write_container(cont, {}, tmp_path / "enc_out.bank")


def test_parse_prib() -> None:
    guid = uuid.uuid4()
    raw = guid.bytes_le + struct.pack("<H", 5) + b"a/b/c"
    assert parse_prib(raw) == (str(guid), "a/b/c")
    assert parse_prib(b"short") == (None, "")
