"""Loose audio adapter: discovery, line listing, decoding and the re-encode round trip."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from babeldub.adapters.loose import adapter as loose_mod
from babeldub.adapters.loose import media
from babeldub.adapters.loose.adapter import LooseAudioAdapter
from babeldub.core.output import EXIT_USAGE, BabelDubError
from babeldub.recon import walk as walk_mod


def tone(seconds: float, sr: int, ch: int, freq: float = 330.0) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    mono = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.repeat(mono[:, None], ch, axis=1)


def write(path: Path, seconds: float, sr: int, ch: int, **kw: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), tone(seconds, sr, ch), sr, **kw)  # type: ignore[arg-type]
    return path


def make_game(root: Path) -> Path:
    voice = root / "game" / "voice"
    write(voice / "eileen" / "e001.ogg", 1.0, 22050, 1, format="OGG", subtype="VORBIS")
    write(voice / "eileen" / "e002.ogg", 0.6, 22050, 1, format="OGG", subtype="VORBIS")
    write(voice / "lucy" / "chapter1" / "l001.wav", 0.8, 44100, 2, subtype="PCM_16")
    write(voice / "en" / "narrator" / "n001.flac", 0.5, 32000, 1, subtype="PCM_24")
    write(voice / "cutscenes" / "intro_01.wav", 0.4, 16000, 1, subtype="PCM_16")
    write(root / "game" / "music" / "theme.ogg", 1.5, 44100, 2, format="OGG", subtype="VORBIS")
    write(root / "game" / "sfx" / "click.wav", 0.1, 44100, 1)
    write(root / "_Redist" / "test.wav", 0.1, 8000, 1)
    return root


def test_discover_groups_voice_and_audio(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    game = make_game(tmp_path)
    assets = {a.relpath: a for a in LooseAudioAdapter().discover(game)}
    assert set(assets) == {"game/voice", "game/music", "game/sfx"}  # _Redist is ignored
    voice = assets["game/voice"]
    assert voice.role == "voice" and voice.level == "A" and voice.info["files"] == 5
    assert voice.info["recursive"] is True and voice.asset_id == "loose:game/voice"
    assert assets["game/music"].role == "audio" and assets["game/music"].info["kind"] == "music"
    assert assets["game/sfx"].info["kind"] == "sfx"


def test_many_audio_folders_merge_per_top_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    walk_mod.clear_cache()
    game = make_game(tmp_path)
    monkeypatch.setattr(loose_mod, "MAX_AUDIO_GROUPS", 1)
    assets = {a.relpath: a for a in LooseAudioAdapter().discover(game)}
    assert set(assets) == {"game/voice", "game"}
    merged = assets["game"]
    assert merged.info["recursive"] and merged.info["exclude"] == ["game/voice"]
    keys = [ln["entry"]["key"] for ln in LooseAudioAdapter().list_lines(game, merged)]
    assert keys == ["theme", "click"]  # voice files stay in their own asset


def test_list_lines(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    game = make_game(tmp_path)
    adapter = LooseAudioAdapter()
    voice = next(a for a in adapter.discover(game) if a.role == "voice")
    lines = adapter.list_lines(game, voice)
    assert [ln["entry"]["file"] for ln in lines] == [
        "game/voice/cutscenes/intro_01.wav",
        "game/voice/eileen/e001.ogg",
        "game/voice/eileen/e002.ogg",
        "game/voice/en/narrator/n001.flac",
        "game/voice/lucy/chapter1/l001.wav",
    ]
    assert [ln["id"] for ln in lines][1] == "loose:game/voice#file:0001"
    by_key = {ln["entry"]["key"]: ln for ln in lines}
    assert by_key["e001"]["speaker_hint"] == "eileen" and by_key["e001"]["kind"] == "dialogue"
    assert by_key["e001"]["audio"]["codec"] == "vorbis" and by_key["e001"]["audio"]["sr"] == 22050
    assert by_key["n001"]["speaker_hint"] == "narrator" and by_key["n001"]["locale"] == "en"
    assert by_key["n001"]["audio"]["codec"] == "flac"
    assert by_key["l001"]["speaker_hint"] == "lucy" and by_key["l001"]["scene"] == "chapter1"
    assert by_key["l001"]["audio"] == {"sr": 44100, "ch": 2, "dur_s": 0.8, "codec": "pcm16"}
    assert by_key["intro_01"]["kind"] == "cutscene" and by_key["intro_01"]["fixed_length"] is True
    assert by_key["intro_01"]["speaker_hint"] is None


def test_extract_audio(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    game = make_game(tmp_path / "g")
    adapter = LooseAudioAdapter()
    voice = next(a for a in adapter.discover(game) if a.role == "voice")
    lines = adapter.list_lines(game, voice)
    results = adapter.extract_audio(game, voice, lines, tmp_path / "orig")
    assert all("error" not in r for r in results)
    for res, line in zip(results, lines, strict=True):
        info = sf.info(res["wav"])
        assert Path(res["wav"]).name == f"{line['uid']}.wav"
        assert info.samplerate == line["audio"]["sr"] and info.channels == line["audio"]["ch"]
        assert info.subtype == "PCM_16"


@pytest.mark.parametrize("with_ffmpeg", [True, False])
def test_build_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_ffmpeg: bool) -> None:
    walk_mod.clear_cache()
    if not with_ffmpeg:
        monkeypatch.setattr(media, "find_tool", lambda name: None)  # libsndfile fallback for ogg
    game = make_game(tmp_path / "g")
    adapter = LooseAudioAdapter()
    voice = next(a for a in adapter.discover(game) if a.role == "voice")
    lines = {ln["entry"]["key"]: ln for ln in adapter.list_lines(game, voice)}
    synth = tmp_path / "synth"
    repl = {
        lines["e001"]["id"]: write(synth / "a.wav", 1.2, 24000, 2),  # stereo 24 kHz -> mono 22.05 kHz ogg
        lines["l001"]["id"]: write(synth / "b.wav", 0.9, 24000, 1),  # mono -> stereo 44.1 kHz wav
        lines["n001"]["id"]: write(synth / "c.wav", 0.4, 48000, 1),  # -> 32 kHz 24-bit flac
    }
    out = tmp_path / "build" / "game" / "voice"
    summary = adapter.build(game, voice, repl, out, {})
    assert summary["replaced"] == 3 and summary["kept"] == 0
    assert sorted(summary["files"]) == [
        "game/voice/eileen/e001.ogg",
        "game/voice/en/narrator/n001.flac",
        "game/voice/lucy/chapter1/l001.wav",
    ]
    assert set(summary["codec"].split(",")) == {"vorbis", "pcm16", "flac"}
    ogg = sf.info(str(out / "eileen" / "e001.ogg"))
    assert (ogg.format, ogg.subtype, ogg.samplerate, ogg.channels) == ("OGG", "VORBIS", 22050, 1)
    assert abs(ogg.frames / ogg.samplerate - 1.2) < 0.05
    wav = sf.info(str(out / "lucy" / "chapter1" / "l001.wav"))
    assert (wav.subtype, wav.samplerate, wav.channels) == ("PCM_16", 44100, 2)
    flac = sf.info(str(out / "en" / "narrator" / "n001.flac"))
    assert (flac.format, flac.subtype, flac.samplerate, flac.channels) == ("FLAC", "PCM_24", 32000, 1)
    assert not (out / "eileen" / "e002.ogg").exists()  # untouched lines are not copied
    report = adapter.verify(game, voice, out, {})
    assert report["ok"], report
    assert report["files"] == 3


def test_verify_finds_problems(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    game = make_game(tmp_path / "g")
    adapter = LooseAudioAdapter()
    voice = next(a for a in adapter.discover(game) if a.role == "voice")
    out = tmp_path / "out"
    write(out / "eileen" / "e001.ogg", 0.5, 44100, 2, format="OGG", subtype="VORBIS")  # wrong rate/channels
    (out / "eileen" / "e002.ogg").write_bytes(b"OggS broken")
    write(out / "eileen" / "e999.ogg", 0.5, 22050, 1, format="OGG", subtype="VORBIS")  # no original
    report = adapter.verify(game, voice, out, {})
    assert not report["ok"]
    text = " | ".join(report["problems"])
    assert "channels 2 != original 1" in text and "sample rate 44100 != original 22050" in text
    assert "e002.ogg" in text and "e999.ogg: no original" in text


def test_build_rejects_unknown_ids(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    game = make_game(tmp_path / "g")
    adapter = LooseAudioAdapter()
    voice = next(a for a in adapter.discover(game) if a.role == "voice")
    with pytest.raises(BabelDubError) as err:
        adapter.build(game, voice, {"loose:game/voice#file:0099": tmp_path / "x.wav"}, tmp_path / "o", {})
    assert err.value.code == EXIT_USAGE


def test_resample_and_channels() -> None:
    data = tone(1.0, 48000, 1)
    out = media.resample(data, 48000, 16000)
    assert out.shape == (16000, 1)
    assert abs(float(np.abs(out).max()) - 0.3) < 0.02
    assert media.match_channels(out, 2).shape == (16000, 2)
    assert media.match_channels(np.ones((10, 2), dtype=np.float32), 1).shape == (10, 1)
