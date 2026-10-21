from __future__ import annotations

from pathlib import Path

from fmod_synth import make_stdt, make_strings_bank

from babeldub.adapters.fmod.strings import event_paths, parse_stdt, read_strings_bank

PATHS = [
    "bank:/Master",
    "bank:/Master.strings",
    "bank:/Dialogue",
    "bus:/",
    "bus:/VO/Dialogue",
    "event:/Dialogue/Act1/Zbyszek/dlg_a1_m01_d03_Zbyszek_01",
    "event:/Dialogue/Act1/Zbyszek/dlg_a1_m01_d03_Zbyszek_02",
    "event:/Dialogue/Act1/Maciek/dlg_a1_m01_d04_Maciek_01",
    "event:/Cutscenes/a2_m21_cs00_Intro/dlg_a2_m21_cs00_i00_Intro",
    "event:/Sound Effects/Impacts/boom",
    "vca:/VCA Dialogue",
]


def test_stdt_round_trip_with_prefix_paths() -> None:
    payload, guids = make_stdt(PATHS)
    entries = parse_stdt(payload)
    assert [e.path for e in entries] == PATHS  # sorted by string index
    assert [e.guid for e in entries] == guids
    # 'bank:/Master' is a prefix of 'bank:/Master.strings': needs a terminator node
    assert {e.path for e in entries} >= {"bank:/Master", "bank:/Master.strings", "bus:/"}


def test_read_strings_bank(tmp_path: Path) -> None:
    data, guids = make_strings_bank(PATHS)
    path = tmp_path / "Master.strings.bank"
    path.write_bytes(data)
    entries = read_strings_bank(path)
    assert {e.path: e.guid for e in entries} == dict(zip(PATHS, guids, strict=True))
    assert len(event_paths(entries)) == 5


def test_empty_stdt() -> None:
    assert parse_stdt(b"") == []
