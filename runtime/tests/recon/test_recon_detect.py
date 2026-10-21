"""Engine/middleware detection on synthetic game folders (no real game assets)."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import soundfile as sf

from babeldub.recon import walk as walk_mod
from babeldub.recon.detect import detect, godot_pck_version, renpy_version
from babeldub.recon.kinds import guess_kind, is_voice_path
from babeldub.recon.langs import lang_of, locale_from_token, locale_in_path


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def fake_pe(file_version: str, product_version: str | None = None) -> bytes:
    """Bytes that carry a VS_FIXEDFILEINFO signature followed by UTF-16 StringFileInfo pairs."""
    fixed = struct.pack("<IIII", 0xFEEF04BD, 0x00010000, (2 << 16) | 2, (20 << 16) | 1)
    strings = b""
    pairs = [("FileVersion", file_version)]
    if product_version:
        pairs.append(("ProductVersion", product_version))
    for key, value in pairs:
        strings += (key + "\0").encode("utf-16-le") + b"\0\0" + (value + "\0").encode("utf-16-le")
    return b"MZ" + b"\0" * 200 + fixed + b"\0" * 40 + strings + b"\0" * 64


def gg_header(version: str) -> bytes:
    """SerializedFile v22 header: version string at 0x30."""
    head = bytearray(0x30)
    struct.pack_into(">I", head, 0x08, 22)
    return bytes(head) + version.encode("ascii") + b"\0" + b"\0" * 64


def make_unity(root: Path) -> Path:
    data = root / "Foo_Data"
    _touch(data / "globalgamemanagers", gg_header("2021.3.45f1"))
    _touch(root / "UnityPlayer.dll", b"MZ")
    _touch(root / "GameAssembly.dll", b"MZ")
    _touch(
        data / "il2cpp_data" / "Metadata" / "global-metadata.dat",
        struct.pack("<Ii", 0xFAB11BAF, 29) + b"\0" * 32,
    )
    _touch(data / "Plugins" / "x86_64" / "fmodstudio.dll", fake_pe("2.2.20 (build 1234)"))
    _touch(data / "StreamingAssets" / "Dialogue_en-US.bank", b"RIFF\0\0\0\0FEV ")
    _touch(data / "StreamingAssets" / "Master.strings.bank", b"RIFF\0\0\0\0FEV ")
    aa = data / "StreamingAssets" / "aa"
    _touch(
        aa / "settings.json", b'{"m_AddressablesVersion": "1.21.19", "m_buildTarget": "StandaloneWindows64"}'
    )
    _touch(
        aa / "StandaloneWindows64" / "localization-string-tables-english_assets_all.bundle", b"not a bundle"
    )
    _touch(aa / "StandaloneWindows64" / "localization-assets-shared_assets_all.bundle", b"not a bundle")
    return root


def make_renpy(root: Path) -> Path:
    _touch(root / "renpy" / "__init__.py", b"# renpy\n")
    _touch(root / "renpy" / "vc_version.py", b"branch = 'fix'\nversion = '8.1.3.23091805'\n")
    _touch(root / "game" / "script.rpyc", b"RENPY RPC2")
    _touch(root / "game" / "archive.rpa", b"RPA-3.0 0000000000000000 42424242\n")
    voice = root / "game" / "voice" / "eileen"
    voice.mkdir(parents=True)
    sf.write(
        str(voice / "e001.ogg"), np.zeros((800, 1), dtype=np.float32), 8000, format="OGG", subtype="VORBIS"
    )
    return root


def make_unreal(root: Path) -> Path:
    _touch(root / "MyGame" / "Content" / "Paks" / "pakchunk0-Windows.pak", b"\0" * 64)
    _touch(root / "MyGame" / "Content" / "Paks" / "pakchunk0-Windows.utoc", b"\0" * 16)
    _touch(root / "MyGame" / "Content" / "Paks" / "pakchunk0-Windows.ucas", b"\0" * 16)
    _touch(root / "MyGame" / "Binaries" / "Win64" / "MyGame-Win64-Shipping.exe", b"MZ")
    _touch(root / "Engine" / "Binaries" / "ThirdParty" / "readme.txt", b"x")
    _touch(root / "MyGame.exe", b"MZ")
    return root


def godot_exe(version: tuple[int, int, int]) -> bytes:
    pck = b"GDPC" + struct.pack("<4I", 2, *version) + b"\0" * 64
    return b"MZ" + b"\0" * 100 + pck + struct.pack("<Q", len(pck)) + b"GDPC"


def test_detect_unity_il2cpp_with_fmod(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    result = detect(make_unity(tmp_path))
    engine = result["engine"]
    assert engine["id"] == "unity"
    assert engine["version"] == "2021.3.45f1"
    assert engine["backend"] == "il2cpp"
    assert engine["metadata_version"] == 29
    assert engine["addressables"]["version"] == "1.21.19"
    fmod = next(m for m in result["middleware"] if m["id"] == "fmod")
    assert fmod["version"] == "2.2.20 (build 1234)"
    assert [t["id"] for t in result["text_systems"]] == ["unity-localization"]
    assert "skills/babeldub-recon/references/engines/unity.md" in result["refs"]["recon"]
    assert "skills/babeldub-recon/references/audio/fmod.md" in result["refs"]["recon"]
    assert "skills/babeldub-recon/references/text/unity-localization.md" in result["refs"]["recon"]
    assert "skills/babeldub-pack/references/formats/fmod.md" in result["refs"]["pack"]
    assert result["anticheat"] == []
    assert result["warnings"] == []


def test_detect_unity_mono_backend(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    data = tmp_path / "Bar_Data"
    _touch(data / "globalgamemanagers", gg_header("2019.4.40f1"))
    _touch(data / "Managed" / "Assembly-CSharp.dll", b"MZ")
    _touch(tmp_path / "UnityPlayer.dll", b"MZ")
    result = detect(tmp_path)
    assert result["engine"]["backend"] == "mono"
    assert "skills/babeldub-recon/references/engines/unity.md" in result["refs"]["recon"]


def test_detect_renpy(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    root = make_renpy(tmp_path)
    result = detect(root)
    assert result["engine"]["id"] == "renpy"
    assert result["engine"]["version"] == "8.1.3.23091805"
    assert renpy_version(root) == "8.1.3.23091805"
    assert result["middleware"] == []


def test_detect_unreal_iostore(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    result = detect(make_unreal(tmp_path))
    engine = result["engine"]
    assert engine["id"] == "unreal"
    assert engine["iostore"] is True
    assert engine["project"] == "MyGame"


def test_detect_godot_embedded_pck(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    exe = _touch(tmp_path / "game.exe", godot_exe((4, 2, 1)))
    assert godot_pck_version(exe, embedded=True) == "4.2.1"
    result = detect(tmp_path)
    assert result["engine"]["id"] == "godot"
    assert result["engine"]["version"] == "4.2.1"


def test_detect_godot_and_wwise_pck_are_told_apart(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    _touch(tmp_path / "data.pck", b"GDPC" + struct.pack("<4I", 1, 3, 5, 2) + b"\0" * 32)
    _touch(tmp_path / "audio" / "voices.pck", b"AKPK" + b"\0" * 32)
    result = detect(tmp_path)
    assert result["engine"]["id"] == "godot"
    assert result["engine"]["version"] == "3.5.2"
    assert "wwise" in [m["id"] for m in result["middleware"]]


def test_anticheat_and_unknown_engine_warn(tmp_path: Path) -> None:
    walk_mod.clear_cache()
    _touch(tmp_path / "EasyAntiCheat" / "Settings.json", b"{}")
    _touch(tmp_path / "game.exe", b"MZ")
    result = detect(tmp_path)
    assert result["engine"]["id"] == "unknown"
    assert [a["id"] for a in result["anticheat"]] == ["easyanticheat"]
    assert any("anti-cheat" in w for w in result["warnings"])
    assert any("engine not recognized" in w for w in result["warnings"])


def test_walk_limits(tmp_path: Path) -> None:
    for i in range(10):
        _touch(tmp_path / f"f{i}.bin")
    _touch(tmp_path / "a" / "b" / "c" / "deep.bin")
    index = walk_mod.build_index(tmp_path, max_entries=5)
    assert index.truncated and len(index.files) == 5
    shallow = walk_mod.build_index(tmp_path, max_depth=2)
    assert not any(f.rel.endswith("deep.bin") for f in shallow.files)
    assert shallow.max_depth_hit
    full = walk_mod.build_index(tmp_path)
    assert full.by_ext[".bin"][0] == 11
    assert [f.rel for f in full.files_matching_path("a/b/c/*.bin")] == ["a/b/c/deep.bin"]


def test_locale_helpers() -> None:
    assert locale_from_token("English(US)") == "en-US"
    assert locale_from_token("en_us") == "en-US"
    assert locale_from_token("es_419") == "es-419"
    assert locale_from_token("русский") == "ru"
    assert locale_from_token("Chinese(PRC)") == "zh-CN"
    assert locale_from_token("rus") == "ru"
    assert locale_from_token("se") is None  # RPG Maker sound effects folder, not Northern Sami
    assert locale_from_token("vo") is None
    assert locale_from_token("voice") is None
    assert lang_of("en-US") == "en" and lang_of("zh-Hans") == "zh" and lang_of(None) is None
    assert locale_in_path("Audio/Windows/English(US)/VO_Act1.bnk") == "en-US"
    assert locale_in_path("StreamingAssets/Dialogue_pl-PL.bank") == "pl-PL"
    assert locale_in_path("game/voice/eileen/e001.ogg") is None
    assert locale_in_path("sound/vo_english.pck") == "en"


def test_kind_guesses() -> None:
    assert guess_kind("game/voice/eileen/e001.ogg") == "dialogue"
    assert guess_kind("Audio/VO/Cutscenes/intro.wem") == "cutscene"
    assert guess_kind("DialogueBank.bnk") == "dialogue"
    assert guess_kind("www/audio/se/Cursor1.ogg") == "sfx"
    assert guess_kind("audio/bgm/Theme1.ogg") == "music"
    assert guess_kind("Barks/Guard_Grunt_01.wav") == "bark"
    assert guess_kind("misc/thing.ogg") is None
    assert is_voice_path("Sound/Speech/x.wav") and not is_voice_path("Sound/Music/x.wav")


def test_signature_refs_point_to_existing_skill_references() -> None:
    """recon_ref/pack_ref in data/engines.yaml must name real files of the shipped skills."""
    from babeldub.recon.detect import load_signatures

    skills = Path(__file__).resolve().parents[3] / "skills"
    missing = []
    for section in ("engines", "middleware", "text_systems"):
        for ident, spec in load_signatures()[section].items():
            for key, base in (("recon_ref", "babeldub-recon/references"), ("pack_ref", "babeldub-pack/references/formats")):
                ref = spec.get(key)
                if ref and not (skills / base / f"{ref}.md").is_file():
                    missing.append(f"{section}.{ident}.{key}={ref}")
    for extra in ("babeldub-recon/references/audio/loose.md", "babeldub-pack/references/formats/loose.md"):
        if not (skills / extra).is_file():
            missing.append(extra)
    assert missing == []
