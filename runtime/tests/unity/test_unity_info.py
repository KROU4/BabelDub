"""Unity facts from synthetic headers and a synthetic Addressables catalog."""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path

from babeldub.adapters.unity.unity_info import (
    addressables_crc,
    metadata_version,
    scripting_backend,
    unity_info,
    unity_version,
    version_from_serialized,
)


def _w(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def serialized_header_v22(version: str) -> bytes:
    head = bytearray(0x30)
    struct.pack_into(">I", head, 0x08, 22)
    return bytes(head) + version.encode() + b"\0" + b"\0" * 32


def serialized_header_v17(version: str) -> bytes:
    head = struct.pack(">IIII", 0x100, 0x2000, 17, 0x1000) + b"\0\0\0\0"
    return head + version.encode() + b"\0" + b"\0" * 32


def unityfs_header(version: str) -> bytes:
    return b"UnityFS\0" + struct.pack(">I", 7) + b"5.x.x\0" + version.encode() + b"\0" + b"\0" * 32


def test_version_from_headers(tmp_path: Path) -> None:
    assert version_from_serialized(_w(tmp_path / "a", serialized_header_v22("2022.3.62f3"))) == "2022.3.62f3"
    assert version_from_serialized(_w(tmp_path / "b", serialized_header_v17("2017.4.40f1"))) == "2017.4.40f1"
    assert version_from_serialized(_w(tmp_path / "c", unityfs_header("2019.4.31f1"))) == "2019.4.31f1"
    assert version_from_serialized(_w(tmp_path / "d", b"\0" * 64)) is None


def test_version_sources_in_order(tmp_path: Path) -> None:
    data = tmp_path / "G_Data"
    _w(data / "data.unity3d", unityfs_header("2020.3.48f1"))
    assert unity_version(tmp_path) == ("2020.3.48f1", "G_Data/data.unity3d")
    _w(data / "globalgamemanagers", serialized_header_v22("2020.3.49f1"))
    assert unity_version(tmp_path)[0] == "2020.3.49f1"


def test_version_from_unityplayer_strings(tmp_path: Path) -> None:
    _w(tmp_path / "G_Data" / "level0", b"\0" * 64)  # data dir without a readable version
    (tmp_path / "G_Data" / "globalgamemanagers").write_bytes(b"\0" * 64)
    fixed = struct.pack("<IIII", 0xFEEF04BD, 0x10000, 2022 << 16 | 3, 62 << 16)
    strings = (
        ("ProductVersion\0").encode("utf-16-le")
        + b"\0\0"
        + ("2022.3.62f3 (96770f904ca7)\0").encode("utf-16-le")
    )
    _w(tmp_path / "UnityPlayer.dll", b"MZ" + b"\0" * 300 + fixed + b"\0" * 36 + strings)
    assert unity_version(tmp_path) == ("2022.3.62f3", "UnityPlayer.dll")


def test_backend_detection(tmp_path: Path) -> None:
    data = tmp_path / "G_Data"
    _w(data / "globalgamemanagers", serialized_header_v22("2022.3.1f1"))
    meta = _w(data / "il2cpp_data" / "Metadata" / "global-metadata.dat", struct.pack("<Ii", 0xFAB11BAF, 31))
    _w(tmp_path / "GameAssembly.dll", b"MZ")
    assert metadata_version(meta) == 31
    info = scripting_backend(tmp_path, data)
    assert info["backend"] == "il2cpp" and info["metadata_version"] == 31
    meta.write_bytes(b"\x00\x11\x22\x33" * 4)  # obfuscated header
    info = scripting_backend(tmp_path, data)
    assert info["backend"] == "il2cpp" and info["metadata_version"] is None and "metadata_note" in info
    mono = tmp_path / "mono"
    _w(mono / "M_Data" / "Managed" / "Assembly-CSharp.dll", b"MZ")
    assert scripting_backend(mono, mono / "M_Data")["backend"] == "mono"


def _options_blob(prefix: bytes, options: dict) -> bytes:
    """ObjectInitializationData-like record: some header bytes then UTF-16LE JSON."""
    return prefix + json.dumps(options).encode("utf-16-le")


def make_catalog(aa: Path, bundles: list[tuple[str, int, int]]) -> None:
    """bundles: (file name, crc, size). Writes catalog.json with entries pointing at extra data."""
    internal_ids = ["Assets/Prefabs/Some.prefab"]
    extra = b""
    entries: list[tuple[int, ...]] = [(0, 0, -1, 0, -1, 0, 0)]
    for name, crc, size in bundles:
        internal_ids.append(
            "{UnityEngine.AddressableAssets.Addressables.RuntimePath}\\StandaloneWindows64\\" + name
        )
        offset = len(extra)
        opts = {"m_Hash": "h" * 32, "m_Crc": crc, "m_BundleName": "x" + name[:6], "m_BundleSize": size}
        extra += _options_blob(b"\x07\x0bUnity.ResourceManager\x00", opts)
        entries.append((len(internal_ids) - 1, 1, -1, 0, offset, 0, 1))
    entry_bytes = struct.pack("<i", len(entries)) + b"".join(struct.pack("<7i", *e) for e in entries)
    catalog = {
        "m_InternalIds": internal_ids,
        "m_EntryDataString": base64.b64encode(entry_bytes).decode(),
        "m_ExtraDataString": base64.b64encode(extra).decode(),
    }
    _w(aa / "catalog.json", json.dumps(catalog).encode())


def test_addressables_crc_maps_bundles_to_files(tmp_path: Path) -> None:
    data = tmp_path / "G_Data"
    _w(data / "globalgamemanagers", serialized_header_v22("2022.3.1f1"))
    aa = data / "StreamingAssets" / "aa"
    _w(aa / "settings.json", b'{"m_AddressablesVersion": "1.22.3"}')
    _w(aa / "StandaloneWindows64" / "localization-string-tables-english_assets_all.bundle", b"U" * 100)
    _w(aa / "StandaloneWindows64" / "defaultlocalgroup_assets_all_0123.bundle", b"U" * 7)
    make_catalog(
        aa,
        [
            ("localization-string-tables-english_assets_all.bundle", 12345, 100),
            ("defaultlocalgroup_assets_all_0123.bundle", 0, 999),
        ],
    )
    rows = addressables_crc(tmp_path)
    assert len(rows) == 2
    loc = rows[0]
    assert loc["crc"] == 12345 and loc["size_ok"] is True
    assert (
        loc["file"]
        == "G_Data/StreamingAssets/aa/StandaloneWindows64/localization-string-tables-english_assets_all.bundle"
    )
    assert rows[1]["crc"] == 0 and rows[1]["size_ok"] is False
    info = unity_info(tmp_path)
    assert info["addressables"]["version"] == "1.22.3"
    assert info["addressables"]["crc_nonzero"] == 1 and info["addressables"]["crc_entries"] == 2
    assert info["addressables"]["bundles"] == 2


def test_addressables_binary_catalog_is_reported_unparsed(tmp_path: Path) -> None:
    data = tmp_path / "G_Data"
    _w(data / "globalgamemanagers", serialized_header_v22("6000.0.1f1"))
    _w(data / "StreamingAssets" / "aa" / "catalog.bin", b"\0" * 16)
    rows = addressables_crc(tmp_path)
    assert rows == [{"catalog": "G_Data/StreamingAssets/aa/catalog.bin", "parsed": False}]
    assert unity_info(tmp_path)["addressables"]["catalog"] == "catalog.bin"
