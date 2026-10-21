from __future__ import annotations

import struct
import uuid
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fmod_synth import SubSpec, make_bank, make_fsb, make_strings_bank, tone

from babeldub.adapters.base import Asset, get_adapter
from babeldub.adapters.fmod import fsb5 as F
from babeldub.adapters.fmod import gameruntime, rebuild, vgm
from babeldub.adapters.fmod.bank import BytesPayload, open_container, write_container
from babeldub.core.output import EXIT_ENV, EXIT_USAGE, BabelDubError

SA = "Game_Data/StreamingAssets"
DLG = f"{SA}/Dialogue_en-US.bank"
VOICE = [
    SubSpec("dlg_a1_m01_d00_Hero_01", 48000, tone(4000, 48000, 1, 300.0)),
    SubSpec("dlg_a1_m01_d00_Hero_02", 48000, tone(3000, 48000, 2, 500.0)),
    SubSpec("dlg_a1_m02_cs00_i00_Intro", 24000, tone(2400, 24000, 1, 700.0)),
    SubSpec("dlg_a1_m01_d00_Hero_01", 48000, tone(900, 48000, 1, 900.0)),
]
EVENTS = [
    "bank:/Master",
    "bank:/Dialogue",
    "event:/Dialogue/Act1/Hero/dlg_a1_m01_d00_Hero_01",
    "event:/Dialogue/Act1/Hero/dlg_a1_m01_d00_Hero_02",
    "event:/Cutscenes/a1_m02_cs00_Intro/dlg_a1_m02_cs00_i00_Intro",
    "event:/Sound Effects/Impacts/boom",
]


def _dialogue_bank(codec: int = F.CODEC_PCM16) -> bytes:
    sfx = make_fsb(F.CODEC_PCM16, [SubSpec("boom", 44100, tone(2000, 44100, 2, 100.0))])
    voice = make_fsb(codec, VOICE)
    keys = [
        e.split(":/", 1)[1] for e in EVENTS if e.startswith("event:/Dialogue") or e.startswith("event:/Cut")
    ]
    return make_bank([sfx, voice], prib_keys=keys, stbl_indices=[3, 1, 0, 2])


@pytest.fixture()
def game(tmp_path: Path) -> Path:
    root = tmp_path / "Game"
    sa = root / SA
    sa.mkdir(parents=True)
    (sa / "Dialogue_en-US.bank").write_bytes(_dialogue_bank())
    (sa / "Dialogue_pl-PL.bank").write_bytes(_dialogue_bank())
    strings, _ = make_strings_bank(EVENTS)
    (sa / "Master.strings.bank").write_bytes(strings)
    master = make_fsb(F.CODEC_PCM16, [SubSpec("ui_click", 48000, tone(500, 48000, 2))])
    (sa / "Master.bank").write_bytes(make_bank([master], guid=uuid.uuid4()))
    (root / "readme.txt").write_text("not audio", encoding="utf-8")
    return root


def _asset(game: Path, rel: str = DLG) -> Asset:
    return next(a for a in get_adapter("fmod").discover(game) if a.relpath == rel)


def _wav(path: Path, data: np.ndarray, sr: int) -> Path:
    sf.write(path, data, sr, subtype="PCM_16")
    return path


def test_discover(game: Path) -> None:
    assets = {a.relpath: a for a in get_adapter("fmod").discover(game)}
    assert set(assets) == {DLG, f"{SA}/Dialogue_pl-PL.bank", f"{SA}/Master.strings.bank", f"{SA}/Master.bank"}
    dlg = assets[DLG]
    assert (dlg.role, dlg.locale, dlg.level) == ("voice", "en-US", "A")
    assert dlg.asset_id == f"fmod:{DLG}" and dlg.sha1 and dlg.size
    assert dlg.info["audio_table"] == "fsb1"
    assert dlg.info["strings_banks"] == [f"{SA}/Master.strings.bank"]
    assert [f["n"] for f in dlg.info["fsbs"]] == [1, 4]
    assert dlg.info["localized_group"] == ["Dialogue_en-US", "Dialogue_pl-PL"]
    assert assets[f"{SA}/Master.strings.bank"].role == "other"
    assert assets[f"{SA}/Master.bank"].role == "audio"


def test_list_lines(game: Path) -> None:
    lines = get_adapter("fmod").list_lines(game, _asset(game))
    assert len(lines) == 5
    by_id = {ln["id"]: ln for ln in lines}
    boom = by_id[f"fmod:{DLG}#fsb0:0000"]
    assert boom["kind"] == "sfx" and boom["entry"]["subsong"] == 1
    hero = by_id[f"fmod:{DLG}#fsb1:0000"]
    assert hero["entry"]["event_path"] == "event:/Dialogue/Act1/Hero/dlg_a1_m01_d00_Hero_01"
    assert hero["entry"]["table_key"] == "Dialogue/Act1/Hero/dlg_a1_m01_d00_Hero_01"
    assert hero["entry"]["audio_table"] is True and hero["entry"]["subsong"] == 2
    assert (hero["kind"], hero["speaker_hint"], hero["scene"], hero["locale"]) == (
        "dialogue",
        "Hero",
        "a1_m01",
        "en-US",
    )
    assert hero["audio"] == {"sr": 48000, "ch": 1, "dur_s": 0.083, "codec": "pcm16", "samples": 4000}
    intro = by_id[f"fmod:{DLG}#fsb1:0002"]
    assert intro["kind"] == "cutscene" and intro["fixed_length"] is True and intro["speaker_hint"] is None
    assert "duplicate" in by_id[f"fmod:{DLG}#fsb1:0003"]["flags"]
    assert [ln["order"] for ln in lines] == list(range(5))
    assert all(len(ln["uid"]) == 12 and ln["asset_id"] == f"fmod:{DLG}" for ln in lines)


def test_extract_audio_native(game: Path, tmp_path: Path) -> None:
    ad = get_adapter("fmod")
    asset = _asset(game)
    lines = ad.list_lines(game, asset)
    rows = ad.extract_audio(game, asset, lines, tmp_path / "orig")
    assert len(rows) == 5
    for row, spec in zip(rows[1:], VOICE, strict=True):
        data, sr = sf.read(row["wav"], dtype="int16", always_2d=True)
        assert sr == spec.rate and np.array_equal(data, spec.pcm)
        assert Path(row["wav"]).name.endswith(".wav") and row["ch"] == spec.pcm.shape[1]


def _replacements(tmp_path: Path) -> dict[str, Path]:
    rng = np.random.default_rng(1)
    st = np.round(rng.normal(0, 2000, (3000, 2))).astype(np.int16)
    return {
        f"fmod:{DLG}#fsb1:0000": _wav(tmp_path / "hero01_ru.wav", st, 22050),  # stereo 22.05k -> mono 48k
        f"fmod:{DLG}#fsb1:0002": _wav(
            tmp_path / "intro_ru.wav", tone(9600, 48000, 1, 440.0), 48000
        ),  # -> 24k
    }


def test_build_fadpcm_and_verify(game: Path, tmp_path: Path) -> None:
    ad = get_adapter("fmod")
    asset = _asset(game)
    reps = _replacements(tmp_path)
    out = tmp_path / "build" / DLG
    summary = ad.build(game, asset, reps, out, {"codec": "fadpcm"})
    assert (summary["replaced"], summary["kept"], summary["codec"]) == (2, 3, "fadpcm")
    assert summary["size"] == out.stat().st_size and summary["replaced_ids"] == sorted(reps)
    report = summary["fsbs"][0]
    assert (report["sub"], report["codec_in"], report["replaced"], report["reencoded"]) == (
        "fsb1",
        "pcm16",
        2,
        2,
    )
    assert report["resampled"] == 2 and report["remixed"] == 1
    orig = open_container(game / DLG)
    new = open_container(out)
    o0, n0 = orig.fsbs[0], new.fsbs[0]
    assert orig.read(o0.offset, o0.size) == new.read(n0.offset, n0.size)  # untouched FSB copied
    blob = new.read(new.fsbs[1].offset, new.fsbs[1].size)
    fsb = F.parse_fsb5(blob)
    assert fsb.codec == F.CODEC_FADPCM
    assert [s.name for s in fsb.subsounds] == [s.name for s in VOICE]
    hero, intro = fsb.subsounds[0], fsb.subsounds[2]
    assert (hero.rate, hero.channels, hero.nsamples) == (48000, 1, 6531)  # ceil(3000 * 48000 / 22050)
    assert (intro.rate, intro.channels, intro.nsamples) == (24000, 1, 4800)
    kept = fsb.subsounds[1]
    data = blob[fsb.data_start + kept.data_offset : fsb.data_start + kept.data_offset + kept.data_size]
    dec = F.decode_native(fsb, kept, data)
    ref = VOICE[1].pcm.astype(np.float64)
    assert 10 * np.log10(np.sum(ref**2) / np.sum((ref - dec) ** 2)) > 25
    opts = {"replacements": {k: str(v) for k, v in reps.items()}, "vgmstream": False}
    res = ad.verify(game, asset, out, opts)
    assert res["ok"], res["problems"]
    for name in (
        "riff_size",
        "chunk_walk_to_eof",
        "metadata_identical",
        "audio_table",
        "fsb1_replaced_audio",
    ):
        assert name in res["checks"]
    assert "fsb0_unchanged" in res["checks"]


def test_one_dub_for_takes_of_different_formats(game: Path, tmp_path: Path) -> None:
    """A follower (alt take) gets its leader's wav: converted to the take's own rate and channels."""
    ad = get_adapter("fmod")
    asset = _asset(game)
    wav = _replacements(tmp_path)[f"fmod:{DLG}#fsb1:0000"]  # stereo 22.05 kHz
    reps = {f"fmod:{DLG}#fsb1:0000": wav, f"fmod:{DLG}#fsb1:0001": wav}  # mono 48k and stereo 48k takes
    out = tmp_path / "build" / DLG
    summary = ad.build(game, asset, reps, out, {"codec": "pcm16"})
    assert summary["replaced"] == 2
    built = open_container(out)
    fsb = F.parse_fsb5(built.read(built.fsbs[1].offset, built.fsbs[1].size))
    assert [(s.rate, s.channels) for s in fsb.subsounds[:2]] == [(48000, 1), (48000, 2)]
    res = ad.verify(game, asset, out, {"replacements": {k: str(v) for k, v in reps.items()}, "vgmstream": False})
    assert res["ok"], res["problems"]
    assert "fsb1_replaced_audio" in res["checks"]


def test_parallel_fadpcm_build_is_identical(
    game: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ad = get_adapter("fmod")
    asset = _asset(game)
    reps = _replacements(tmp_path)
    serial = ad.build(game, asset, reps, tmp_path / "serial.bank", {"codec": "fadpcm", "jobs": 1})
    monkeypatch.setattr(rebuild, "_PARALLEL_MIN_FRAMES", 0)
    monkeypatch.setattr(rebuild, "_ENCODE_BATCH_FRAMES", 4)  # several batches in flight
    parallel = ad.build(game, asset, reps, tmp_path / "parallel.bank", {"codec": "fadpcm", "jobs": 2})
    assert parallel["sha1"] == serial["sha1"]


def test_build_auto_keeps_pcm16_and_copies_kept_bytes(game: Path, tmp_path: Path) -> None:
    ad = get_adapter("fmod")
    asset = _asset(game)
    reps = _replacements(tmp_path)
    out = tmp_path / "auto.bank"
    summary = ad.build(game, asset, reps, out, {"codec": "auto", "sample_rate": 32000, "fsb_guid": "zero"})
    assert summary["codec"] == "pcm16" and summary["fsbs"][0]["copied"] == 2
    orig_fsb = open_container(game / DLG)
    new = open_container(out)
    ob = orig_fsb.read(orig_fsb.fsbs[1].offset, orig_fsb.fsbs[1].size)
    nb = new.read(new.fsbs[1].offset, new.fsbs[1].size)
    fo, fn = F.parse_fsb5(ob), F.parse_fsb5(nb)
    assert nb[0x24:0x3C] == bytes(0x18)  # zeroed GUID
    for i in (1, 3):
        so, sn = fo.subsounds[i], fn.subsounds[i]
        assert (
            ob[fo.data_start + so.data_offset :][: so.data_size]
            == nb[fn.data_start + sn.data_offset :][: sn.data_size]
        )
    assert fn.subsounds[0].rate == 32000 and fn.subsounds[2].rate == 32000
    res = ad.verify(
        game,
        asset,
        out,
        {"replacements": {k: str(v) for k, v in reps.items()}, "sample_rate": 32000, "vgmstream": False},
    )
    assert res["ok"], res["problems"]


def test_build_guards(game: Path, tmp_path: Path) -> None:
    ad = get_adapter("fmod")
    asset = _asset(game)
    reps = _replacements(tmp_path)
    with pytest.raises(BabelDubError) as exc:
        ad.build(game, asset, reps, game / "out.bank", {})
    assert exc.value.code == EXIT_USAGE
    with pytest.raises(BabelDubError):
        ad.build(
            game, asset, {f"fmod:{SA}/Other.bank#fsb1:0000": tmp_path / "x.wav"}, tmp_path / "o.bank", {}
        )
    with pytest.raises(BabelDubError):
        ad.build(game, asset, {f"fmod:{DLG}#fsb1:0042": next(iter(reps.values()))}, tmp_path / "o.bank", {})
    with pytest.raises(BabelDubError):
        ad.build(game, asset, reps, tmp_path / "o.bank", {"codec": "vorbis"})
    copy = ad.build(game, asset, {}, tmp_path / "copy.bank", {})
    assert copy["codec"] == "copy" and (tmp_path / "copy.bank").read_bytes() == (game / DLG).read_bytes()


def test_verify_detects_damage(game: Path, tmp_path: Path) -> None:
    ad = get_adapter("fmod")
    asset = _asset(game)
    # 1. metadata byte changed (inside the BNKI chunk)
    bad = bytearray((game / DLG).read_bytes())
    at = bad.find(b"BNKI") + 8 + 20
    bad[at] ^= 0xFF
    p1 = tmp_path / "meta.bank"
    p1.write_bytes(bytes(bad))
    res = ad.verify(game, asset, p1, {"vgmstream": False})
    assert not res["ok"] and any(p.startswith("metadata_identical") for p in res["problems"])
    # 2. subsound order changed in the audio-table FSB
    swapped = make_fsb(F.CODEC_PCM16, [VOICE[1], VOICE[0], VOICE[2], VOICE[3]])
    p2 = tmp_path / "order.bank"
    write_container(open_container(game / DLG), {1: BytesPayload(swapped)}, p2)
    res = ad.verify(game, asset, p2, {"vgmstream": False})
    assert any(p.startswith("fsb1_names_order") for p in res["problems"])
    # 3. same names and formats, but a kept subsound carries other audio
    alt = list(VOICE)
    alt[1] = SubSpec(VOICE[1].name, 48000, tone(3000, 48000, 2, 1234.0))
    p3 = tmp_path / "content.bank"
    write_container(open_container(game / DLG), {1: BytesPayload(make_fsb(F.CODEC_PCM16, alt))}, p3)
    res = ad.verify(game, asset, p3, {"vgmstream": False})
    assert any(p.startswith("kept_audio_matches_original") for p in res["problems"])
    # 4. missing file
    assert not ad.verify(game, asset, tmp_path / "nope.bank", {})["ok"]


def test_vorbis_bank_needs_vgmstream(game: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Vorbis payloads are opaque to BabelDub: decoding kept subsounds needs vgmstream-cli
    writer = F.Fsb5Writer(F.CODEC_VORBIS, None)
    writer.add(F.NewSubsound("dlg_a1_m01_d00_Hero_01", 48000, 1, 1000, b"\x01" * 100))
    writer.add(F.NewSubsound("dlg_a1_m01_d00_Hero_02", 48000, 1, 1000, b"\x02" * 100))
    (game / DLG).write_bytes(make_bank([writer.to_bytes()]))

    def missing(name: str, why: str) -> Path:
        raise BabelDubError(f"{name} not found ({why})", code=EXIT_ENV)

    monkeypatch.setattr(vgm, "require_tool", missing)
    ad = get_adapter("fmod")
    asset = _asset(game)
    lines = ad.list_lines(game, asset)
    assert lines[0]["audio"]["codec"] == "vorbis"
    with pytest.raises(BabelDubError) as exc:
        ad.extract_audio(game, asset, lines, tmp_path / "x")
    assert exc.value.code == EXIT_ENV
    wav = _wav(tmp_path / "r.wav", tone(100, 48000), 48000)
    with pytest.raises(BabelDubError) as exc:
        ad.build(game, asset, {lines[0]["id"]: wav}, tmp_path / "v.bank", {})
    assert exc.value.code == EXIT_ENV
    # when every subsound is replaced nothing has to be decoded
    summary = ad.build(game, asset, {ln["id"]: wav for ln in lines}, tmp_path / "v.bank", {})
    assert (summary["codec"], summary["replaced"]) == ("fadpcm", 2)


def test_find_fmod_library(tmp_path: Path) -> None:
    def pe(path: Path, machine: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        head = bytearray(0x200)
        head[:2] = b"MZ"
        struct.pack_into("<I", head, 0x3C, 0x80)
        head[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", head, 0x84, machine)
        path.write_bytes(bytes(head))

    pe(tmp_path / "Data/Plugins/x86/fmodstudio.dll", 0x14C)
    pe(tmp_path / "Data/Plugins/x86_64/fmodstudioL.dll", 0x8664)
    assert gameruntime.find_fmod_library(tmp_path) is None
    pe(tmp_path / "Data/Plugins/x86_64/fmodstudio.dll", 0x8664)
    found = gameruntime.find_fmod_library(tmp_path)
    assert found is not None and found.parent.name == "x86_64"


def test_game_runtime_without_library_is_reported(game: Path, tmp_path: Path) -> None:
    ad = get_adapter("fmod")
    asset = _asset(game)
    out = tmp_path / "b.bank"
    ad.build(game, asset, _replacements(tmp_path), out, {})
    res = ad.verify(game, asset, out, {"game_runtime": True, "vgmstream": False})
    assert any(p.startswith("game_runtime") for p in res["problems"])


def test_game_runtime_child_reports_unusable_library(tmp_path: Path) -> None:
    # the child process must answer with one JSON object even when the library cannot be loaded
    fake = tmp_path / "fmodstudio.dll"
    fake.write_bytes(b"not a library")
    spec = {"dll": str(fake), "preload": [], "bank": str(tmp_path / "x.bank"), "probes": [], "count_keys": []}
    res = gameruntime.run_check(spec, tmp_path, timeout=120)
    assert res["ok"] is False and res["problems"]
