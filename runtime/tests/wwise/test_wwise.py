"""Wwise .bnk/.pck/.wem parsing, listing and PCM extraction on synthetic containers."""

from __future__ import annotations

import io
import struct
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from babeldub.adapters.base import Asset
from babeldub.adapters.wwise import formats
from babeldub.adapters.wwise.adapter import WwiseAdapter, parse_soundbanks_info
from babeldub.core import tools
from babeldub.core.output import EXIT_ENV, EXIT_USAGE, BabelDubError
from babeldub.recon import walk as walk_mod


def chunk(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack("<I", len(body)) + body


def wem_pcm(samples: np.ndarray, sr: int) -> bytes:
    """Little-endian PCM16 .wem (RIFF WAVE, extensible format tag like Wwise writes)."""
    frames = samples.astype("<i2")
    ch = frames.shape[1]
    mask = 0x4 if ch == 1 else 0x3
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, ch, sr, sr * 2 * ch, 2 * ch, 16, 6, 16, mask)  # 0x18 bytes
    body = b"WAVE" + chunk(b"fmt ", fmt) + chunk(b"data", frames.tobytes())
    return b"RIFF" + struct.pack("<I", len(body)) + body


def wem_vorbis(num_samples: int, sr: int, ch: int) -> bytes:
    """Header-only Wwise Vorbis .wem: fmt size 0x42 with the sample count at +0x18."""
    fmt = bytearray(0x42)
    struct.pack_into("<HHIIHHH", fmt, 0, 0xFFFF, ch, sr, 16000, 0, 0, 0x30)
    struct.pack_into("<I", fmt, 0x18, num_samples)
    body = b"WAVE" + chunk(b"fmt ", bytes(fmt)) + chunk(b"data", b"\x01" * 200)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def make_bank(media: list[tuple[int, bytes]], bank_id: int = 77, lang_id: int = 0) -> bytes:
    data = b""
    didx = b""
    for media_id, blob in media:
        pad = (-len(data)) % 16
        data += b"\0" * pad
        didx += struct.pack("<III", media_id, len(data), len(blob))
        data += blob
    out = chunk(b"BKHD", struct.pack("<IIII", 0x8C, bank_id, lang_id, 0))
    if media:
        out += chunk(b"DIDX", didx) + chunk(b"DATA", data)
    out += chunk(b"HIRC", struct.pack("<I", 0))
    return out


def make_pack(
    languages: dict[int, str], sounds: list[tuple[int, int, bytes]], banks: list[tuple[int, int, bytes]]
) -> bytes:
    """AKPK with UTF-16 language names; entries (id, language id, bytes), block size 16."""
    names = b""
    offsets = []
    count = len(languages)
    base = 4 + count * 8
    for _lid, name in sorted(languages.items()):
        offsets.append(base + len(names))
        names += (name + "\0").encode("utf-16-le")
    lang_blob = struct.pack("<I", count)
    for (lid, _), off in zip(sorted(languages.items()), offsets, strict=True):
        lang_blob += struct.pack("<II", off, lid)
    lang_blob += names
    lang_blob += b"\0" * ((-len(lang_blob)) % 4)
    header_len = 0x1C + len(lang_blob) + 4 + 20 * len(banks) + 4 + 20 * len(sounds)
    payload = b""
    data_start = (header_len + 15) // 16 * 16

    def table(entries: list[tuple[int, int, bytes]]) -> bytes:
        nonlocal payload
        out = struct.pack("<I", len(entries))
        for fid, lid, blob in entries:
            pad = (-(data_start + len(payload))) % 16
            payload += b"\0" * pad
            offset = data_start + len(payload)
            out += struct.pack("<IIIII", fid, 16, len(blob), offset // 16, lid)
            payload += blob
        return out

    bank_blob = table(banks)
    sound_blob = table(sounds)
    header_size = 4 + 16 + len(lang_blob) + len(bank_blob) + len(sound_blob)
    head = b"AKPK" + struct.pack(
        "<IIIIII", header_size, 1, len(lang_blob), len(bank_blob), len(sound_blob), 0
    )
    head += lang_blob + bank_blob + sound_blob
    head += b"\0" * (data_start - len(head))
    return head + payload


def tone(n: int, ch: int = 1) -> np.ndarray:
    t = np.arange(n)
    wave = (8000 * np.sin(2 * np.pi * t / 40)).astype(np.int16)
    return np.repeat(wave[:, None], ch, axis=1)


def test_read_bank_and_wem_info() -> None:
    pcm = wem_pcm(tone(4800), 48000)
    vorb = wem_vorbis(96000, 48000, 2)
    blob = make_bank([(111, pcm), (222, vorb)], bank_id=5, lang_id=3)
    bank = formats.read_bank(io.BytesIO(blob))
    assert bank.bank_id == 5 and bank.language_id == 3 and bank.version == 0x8C
    assert [m.media_id for m in bank.media] == [111, 222]
    assert blob[bank.media[1].offset : bank.media[1].offset + bank.media[1].size] == vorb
    info = formats.wem_info(pcm, len(pcm))
    assert (info.codec, info.sr, info.ch, info.bits, info.dur_s) == ("pcm", 48000, 1, 16, 0.1)
    info = formats.wem_info(vorb, len(vorb))
    assert (info.codec, info.ch, info.dur_s) == ("vorbis", 2, 2.0)
    with pytest.raises(formats.WwiseFormatError):
        formats.read_bank(io.BytesIO(b"NOPE" + b"\0" * 32))
    with pytest.raises(formats.WwiseFormatError):
        formats.read_bank(io.BytesIO(blob[:-200] + b"\xff" * 4))


def test_read_pack() -> None:
    bank = make_bank([(9, wem_pcm(tone(160), 16000))])
    blob = make_pack(
        {0: "SFX", 1: "English(US)"},
        sounds=[(1001, 1, wem_pcm(tone(1600), 16000)), (1002, 0, wem_pcm(tone(800), 16000))],
        banks=[(55, 1, bank)],
    )
    pack = formats.read_pack(io.BytesIO(blob))
    assert pack.languages == {0: "SFX", 1: "English(US)"}
    assert [(e.file_id, e.language_id) for e in pack.sounds] == [(1001, 1), (1002, 0)]
    first = pack.sounds[0]
    assert blob[first.offset : first.offset + 4] == b"RIFF"
    assert pack.banks[0].file_id == 55 and blob[pack.banks[0].offset : pack.banks[0].offset + 4] == b"BKHD"


def make_game(root: Path) -> Path:
    banks = root / "Game" / "Audio" / "GeneratedSoundBanks" / "Windows"
    (banks / "English(US)").mkdir(parents=True)
    (banks / "English(US)" / "VO_Act1.bnk").write_bytes(
        make_bank(
            [(501, wem_pcm(tone(2400), 24000)), (502, wem_vorbis(48000, 48000, 1))], bank_id=10, lang_id=1
        )
    )
    (banks / "Music.bnk").write_bytes(make_bank([(601, wem_vorbis(441000, 44100, 2))], bank_id=11))
    (banks / "Init.bnk").write_bytes(make_bank([], bank_id=1))
    (banks / "SoundbanksInfo.xml").write_text(
        '<?xml version="1.0"?><SoundBanksInfo><SoundBanks><SoundBank Id="10" Language="English(US)">'
        '<IncludedMemoryFiles><File Id="501" Language="English(US)"><ShortName>VO\\Bob\\VO_Bob_Hello_01.wav'
        "</ShortName><Path>SFX\\VO_Bob_Hello_01.wem</Path></File></IncludedMemoryFiles></SoundBank>"
        "</SoundBanks></SoundBanksInfo>",
        encoding="utf-8",
    )
    pck = make_pack(
        {0: "SFX", 1: "Russian"},
        sounds=[(7001, 1, wem_pcm(tone(1600), 16000)), (7002, 0, wem_pcm(tone(800), 16000))],
        banks=[(56, 1, make_bank([(7101, wem_pcm(tone(320), 16000))]))],
    )
    (root / "Game" / "Audio" / "Voices.pck").write_bytes(pck)
    loose = root / "Game" / "Audio" / "Media" / "Japanese"
    loose.mkdir(parents=True)
    (loose / "8001.wem").write_bytes(wem_pcm(tone(1000), 16000))
    return root


def test_discover_and_list_lines(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    game = make_game(tmp_path)
    adapter = WwiseAdapter()
    assets = {a.relpath: a for a in adapter.discover(game)}
    assert set(assets) == {
        "Game/Audio/GeneratedSoundBanks/Windows/English(US)/VO_Act1.bnk",
        "Game/Audio/GeneratedSoundBanks/Windows/Music.bnk",
        "Game/Audio/Voices.pck",
        "Game/Audio/Media/Japanese",
    }  # Init.bnk has no media
    vo = assets["Game/Audio/GeneratedSoundBanks/Windows/English(US)/VO_Act1.bnk"]
    assert vo.role == "voice" and vo.locale == "en-US" and vo.level == "B" and vo.info["media"] == 2
    assert assets["Game/Audio/GeneratedSoundBanks/Windows/Music.bnk"].role == "audio"
    pck = assets["Game/Audio/Voices.pck"]
    assert pck.role == "voice" and pck.locale == "ru" and pck.info["languages"] == ["Russian", "SFX"]
    assert assets["Game/Audio/Media/Japanese"].locale == "ja"

    lines = adapter.list_lines(game, vo)
    assert [ln["id"] for ln in lines] == [
        "wwise:Game/Audio/GeneratedSoundBanks/Windows/English(US)/VO_Act1.bnk#bnk:0000",
        "wwise:Game/Audio/GeneratedSoundBanks/Windows/English(US)/VO_Act1.bnk#bnk:0001",
    ]
    first, second = lines
    assert first["entry"]["key"] == "VO_Bob_Hello_01" and first["entry"]["media_id"] == 501
    assert first["kind"] == "dialogue" and first["locale"] == "en-US"
    assert first["audio"] == {"sr": 24000, "ch": 1, "dur_s": 0.1, "codec": "pcm"}
    assert (
        second["entry"]["key"] == "502"
        and second["audio"]["codec"] == "vorbis"
        and second["audio"]["dur_s"] == 1.0
    )

    music = adapter.list_lines(game, assets["Game/Audio/GeneratedSoundBanks/Windows/Music.bnk"])
    assert music[0]["kind"] == "music" and music[0]["locale"] is None

    plines = adapter.list_lines(game, pck)
    subs = [(ln["entry"]["sub"], ln["entry"]["index"], ln["locale"], ln["kind"]) for ln in plines]
    assert subs == [("snd", 0, "ru", "dialogue"), ("snd", 1, None, "dialogue"), ("bnk0", 0, "ru", "dialogue")]

    wlines = adapter.list_lines(game, assets["Game/Audio/Media/Japanese"])
    assert wlines[0]["entry"]["file"] == "Game/Audio/Media/Japanese/8001.wem" and wlines[0]["locale"] == "ja"


def test_extract_pcm_without_vgmstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    walk_mod.clear_cache()
    monkeypatch.setattr(tools, "find_tool", lambda name: None)
    game = make_game(tmp_path / "g")
    adapter = WwiseAdapter()
    pck = next(a for a in adapter.discover(game) if a.relpath.endswith("Voices.pck"))
    lines = adapter.list_lines(game, pck)
    out = tmp_path / "orig"
    results = adapter.extract_audio(game, pck, lines, out)
    assert [r.get("error") for r in results] == [None, None, None]
    data, sr = sf.read(results[0]["wav"], dtype="int16")
    assert sr == 16000 and np.array_equal(data, tone(1600)[:, 0])
    assert results[2]["dur_s"] == 0.02
    assert not (out / ".wwise-tmp").exists()


def test_extract_vorbis_needs_vgmstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    walk_mod.clear_cache()
    monkeypatch.setattr(tools, "find_tool", lambda name: None)
    game = make_game(tmp_path / "g")
    adapter = WwiseAdapter()
    vo = next(a for a in adapter.discover(game) if a.relpath.endswith("VO_Act1.bnk"))
    lines = adapter.list_lines(game, vo)
    with pytest.raises(BabelDubError) as err:
        adapter.extract_audio(game, vo, lines[1:], tmp_path / "orig")
    assert err.value.code == EXIT_ENV


def test_build_refused_with_hint(tmp_path: Path) -> None:
    asset = Asset(asset_id="wwise:x.bnk", adapter="wwise", relpath="x.bnk", role="voice", level="B")
    with pytest.raises(BabelDubError) as err:
        WwiseAdapter().build(tmp_path, asset, {}, tmp_path / "out", {})
    assert err.value.code == EXIT_USAGE
    assert "skills/babeldub-pack/references/formats/wwise.md" in (err.value.hint or "")
    assert "WwiseConsole" in (err.value.hint or "")


def test_soundbanks_info_json(tmp_path: Path) -> None:
    path = tmp_path / "SoundbanksInfo.json"
    path.write_text(
        '{"SoundBanksInfo": {"SoundBanks": [{"Media": [{"Id": "42", "Language": "German",'
        ' "ShortName": "VO/Anna_01.wav", "Path": "Media/42.wem"}]}],'
        ' "StreamedFiles": [{"Id": 43, "Language": "SFX", "ShortName": "boom.wav"}]}}',
        encoding="utf-8",
    )
    names = parse_soundbanks_info(path)
    assert names[42]["name"] == "VO/Anna_01.wav" and names[42]["language"] == "German"
    assert names[43]["name"] == "boom.wav"
