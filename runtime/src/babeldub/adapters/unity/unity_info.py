"""Facts about a Unity build, read from files only (the game is never executed).

- Unity version: SerializedFile header of `globalgamemanagers` (or `data.unity3d` UnityFS header,
  or the ProductVersion string of UnityPlayer.dll as a last resort).
- Scripting backend: IL2CPP (`GameAssembly.dll` + `il2cpp_data/Metadata/global-metadata.dat`, whose
  header holds the metadata version) or Mono (`Managed/Assembly-CSharp.dll`).
- Addressables: `StreamingAssets/aa/settings.json` (m_AddressablesVersion) and the content
  catalog (`catalog.json` 1.x / `catalog.bin` 2.x). `addressables_crc()` lists per-bundle CRCs:
  a non-zero m_Crc makes Unity reject a modified bundle unless the catalog is patched.
"""

from __future__ import annotations

import base64
import json
import re
import struct
from pathlib import Path
from typing import Any

from babeldub.recon.pe import version_string

_VERSION_RE = re.compile(rb"(\d{1,4}\.\d{1,2}\.\d{1,3}[abfpxc]\d{1,3})")
_VERSION_STR_RE = r"(\d{4}\.\d{1,2}\.\d{1,3}[abfpxc]\d{1,3})"
_METADATA_MAGIC = 0xFAB11BAF


def data_dirs(game_dir: Path) -> list[Path]:
    """`<Game>_Data` folders (Windows/Linux layout) that look like Unity player data."""
    out: list[Path] = []
    try:
        children = sorted(game_dir.iterdir())
    except OSError:
        return out
    for child in children:
        if not child.is_dir() or not child.name.lower().endswith("_data"):
            continue
        if any((child / name).exists() for name in ("globalgamemanagers", "data.unity3d", "mainData")):
            out.append(child)
    return out


def version_from_serialized(path: Path) -> str | None:
    """Unity version string stored near the start of a SerializedFile or a UnityFS bundle."""
    try:
        with path.open("rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    match = _VERSION_RE.search(head)
    return match.group(1).decode("ascii") if match else None


def unity_version(game_dir: Path, data_dir: Path | None = None) -> tuple[str | None, str | None]:
    """(version, source file) using the most reliable source available."""
    dirs = [data_dir] if data_dir else data_dirs(game_dir)
    for ddir in dirs:
        for name in ("globalgamemanagers", "data.unity3d", "mainData", "level0"):
            path = ddir / name
            if path.is_file():
                version = version_from_serialized(path)
                if version:
                    return version, f"{ddir.name}/{name}"
    for name in ("UnityPlayer.dll", "UnityPlayer.so"):
        path = game_dir / name
        if path.is_file():
            version = version_string(path, _VERSION_STR_RE)
            if version:
                return version, name
    return None, None


def metadata_version(path: Path) -> int | None:
    """IL2CPP global-metadata.dat version (e.g. 31 for Unity 2022.3), None if the header is unknown."""
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    if len(head) < 8:
        return None
    magic, version = struct.unpack("<Ii", head)
    return version if magic == _METADATA_MAGIC else None


def scripting_backend(game_dir: Path, data_dir: Path | None) -> dict[str, Any]:
    info: dict[str, Any] = {"backend": None}
    metadata = data_dir / "il2cpp_data" / "Metadata" / "global-metadata.dat" if data_dir else None
    has_ga = (game_dir / "GameAssembly.dll").is_file() or (game_dir / "GameAssembly.so").is_file()
    if metadata is not None and metadata.is_file():
        info["backend"] = "il2cpp"
        version = metadata_version(metadata)
        info["metadata_version"] = version
        if version is None:
            info["metadata_note"] = "global-metadata.dat header is not standard (obfuscated or encrypted)"
    elif has_ga:
        info["backend"] = "il2cpp"
        info["metadata_note"] = "GameAssembly present but global-metadata.dat not found at the usual path"
    elif data_dir is not None and (data_dir / "Managed" / "Assembly-CSharp.dll").is_file():
        info["backend"] = "mono"
    return info


def addressables_dir(data_dir: Path) -> Path | None:
    aa = data_dir / "StreamingAssets" / "aa"
    return aa if aa.is_dir() else None


def addressables_info(data_dir: Path) -> dict[str, Any] | None:
    aa = addressables_dir(data_dir)
    if aa is None:
        return None
    info: dict[str, Any] = {"dir": f"{data_dir.name}/StreamingAssets/aa"}
    settings = aa / "settings.json"
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8-sig"))
            info["version"] = data.get("m_AddressablesVersion")
            info["build_target"] = data.get("m_buildTarget")
        except (OSError, ValueError):
            info["settings_error"] = "settings.json is not valid JSON"
    if (aa / "catalog.json").is_file():
        info["catalog"] = "catalog.json"
    elif (aa / "catalog.bin").is_file():
        info["catalog"] = "catalog.bin"
    else:
        found = sorted(p.name for p in aa.glob("catalog*.*"))
        info["catalog"] = found[0] if found else None
    bundles = [p for p in aa.rglob("*.bundle")]
    info["bundles"] = len(bundles)
    return info


def _decode_request_options(extra: bytes) -> list[dict[str, Any]]:
    """AssetBundleRequestOptions JSON objects embedded (UTF-16LE) in m_ExtraDataString."""
    found: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for shift in (0, 1):  # payloads may start at odd byte offsets
        text = extra[shift:].decode("utf-16-le", errors="ignore")
        for match in re.finditer(r'\{"m_Hash":.*?\}', text):
            try:
                opt = json.loads(match.group(0))
            except ValueError:
                continue
            key = (opt.get("m_BundleName"), opt.get("m_Hash"))
            if key in seen:
                continue
            seen.add(key)
            found.append(opt)
    return found


_RUNTIME_PATH = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}"
_HASH_OPEN = '{"m_Hash"'.encode("utf-16-le")
_BRACE_CLOSE = "}".encode("utf-16-le")


def _options_at(extra: bytes, offset: int) -> dict[str, Any] | None:
    """AssetBundleRequestOptions serialized at `offset` of m_ExtraDataString (UTF-16LE JSON)."""
    start = extra.find(_HASH_OPEN, offset)
    if start < 0 or start - offset > 1024:
        return None
    end = extra.find(_BRACE_CLOSE, start)
    if end < 0:
        return None
    try:
        return json.loads(extra[start : end + 2].decode("utf-16-le"))
    except ValueError:
        return None


def _bundle_entries(catalog: dict[str, Any], extra: bytes) -> list[tuple[str, dict[str, Any]]]:
    """(internal id, options) for every bundle location of a JSON content catalog (1.x).

    m_EntryDataString = int32 count + count * 7 int32 (internalId, provider, dependencyKey,
    depHash, dataIndex, primaryKey, resourceType); dataIndex is the byte offset of the entry's
    serialized data in m_ExtraDataString.
    """
    entries = base64.b64decode(catalog.get("m_EntryDataString") or "")
    internal_ids: list[str] = catalog.get("m_InternalIds") or []
    prefixes: list[str] = catalog.get("m_InternalIdPrefixes") or []
    if len(entries) < 4:
        return []
    (count,) = struct.unpack_from("<i", entries, 0)
    if count < 0 or 4 + count * 28 > len(entries):
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for i in range(count):
        iid, _prov, _dep, _dh, data_index, _pk, _rt = struct.unpack_from("<7i", entries, 4 + i * 28)
        if data_index < 0 or not (0 <= iid < len(internal_ids)):
            continue
        internal = internal_ids[iid]
        match = re.match(r"^(\d+)#(.*)$", internal)
        if match and prefixes and int(match.group(1)) < len(prefixes):
            internal = prefixes[int(match.group(1))] + match.group(2)
        if not internal.lower().endswith(".bundle"):
            continue
        opt = _options_at(extra, data_index)
        if opt is not None:
            out.append((internal, opt))
    return out


def addressables_crc(game_dir: Path) -> list[dict[str, Any]]:
    """Per-bundle CRC/size from the Addressables JSON catalog of every Unity data folder.

    Returns [{"bundle", "crc", "size", "disk_size", "size_ok", "file"}] where `file` is the
    bundle relpath inside the game folder (None for remote bundles). A binary catalog
    (Addressables 2.x catalog.bin) yields one row {"catalog": ..., "parsed": False}.
    """
    rows: list[dict[str, Any]] = []
    for ddir in data_dirs(game_dir):
        aa = addressables_dir(ddir)
        if aa is None:
            continue
        catalog = aa / "catalog.json"
        if not catalog.is_file():
            if (aa / "catalog.bin").is_file():
                rows.append({"catalog": f"{ddir.name}/StreamingAssets/aa/catalog.bin", "parsed": False})
            continue
        try:
            data = json.loads(catalog.read_text(encoding="utf-8-sig"))
            extra = base64.b64decode(data.get("m_ExtraDataString") or "")
        except (OSError, ValueError):
            rows.append({"catalog": f"{ddir.name}/StreamingAssets/aa/catalog.json", "parsed": False})
            continue
        located = _bundle_entries(data, extra)
        if not located:  # unknown entry layout: report options without file mapping
            located = [("", opt) for opt in _decode_request_options(extra)]
        for internal, opt in located:
            path: Path | None = None
            if internal.startswith(_RUNTIME_PATH):
                rel = internal[len(_RUNTIME_PATH) :].replace("\\", "/").lstrip("/")
                path = aa / rel
            disk_size = path.stat().st_size if path is not None and path.is_file() else None
            rows.append(
                {
                    "bundle": opt.get("m_BundleName"),
                    "crc": int(opt.get("m_Crc") or 0),
                    "size": opt.get("m_BundleSize"),
                    "disk_size": disk_size,
                    "size_ok": disk_size == opt.get("m_BundleSize") if disk_size is not None else None,
                    "file": path.relative_to(game_dir).as_posix() if disk_size is not None and path else None,
                }
            )
    return rows


def unity_info(game_dir: Path) -> dict[str, Any]:
    """Everything recon reports about a Unity build (cheap: headers and small JSON files only)."""
    dirs = data_dirs(game_dir)
    data_dir = dirs[0] if dirs else None
    version, source = unity_version(game_dir, data_dir)
    info: dict[str, Any] = {
        "version": version,
        "version_source": source,
        "data_dir": data_dir.name if data_dir else None,
    }
    info.update(scripting_backend(game_dir, data_dir))
    if data_dir is not None:
        aa = addressables_info(data_dir)
        if aa:
            crc_rows = [r for r in addressables_crc(game_dir) if "bundle" in r]
            if crc_rows:
                aa["crc_nonzero"] = sum(1 for r in crc_rows if r["crc"])
                aa["crc_entries"] = len(crc_rows)
            info["addressables"] = aa
        app_info = data_dir / "app.info"
        if app_info.is_file():
            try:
                lines = app_info.read_text(encoding="utf-8", errors="replace").splitlines()
                info["company"] = lines[0].strip() if lines else None
                info["product"] = lines[1].strip() if len(lines) > 1 else None
            except OSError:
                pass
    return info
