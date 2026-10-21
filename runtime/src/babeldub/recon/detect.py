"""Engine / middleware / text-system / anti-cheat detection from files (data/engines.yaml).

Only metadata and a few header bytes are read; nothing in the game folder is executed or changed.
Engine-specific probes add versions: Unity (globalgamemanagers, IL2CPP metadata, Addressables),
Godot (PCK header), Ren'Py (vc_version.py), Unreal (IoStore), FMOD/Wwise (DLL FileVersion).
"""

from __future__ import annotations

import re
import struct
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from babeldub.adapters.unity.unity_info import unity_info
from babeldub.core.paths import data_dir
from babeldub.recon.pe import file_version
from babeldub.recon.walk import GameIndex, walk

MAGIC_MAX_FILES = 24
SECTIONS = ("engines", "middleware", "text_systems", "anticheat")


@lru_cache(maxsize=1)
def load_signatures() -> dict[str, Any]:
    with (data_dir() / "engines.yaml").open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _read_head(path: Path, size: int) -> bytes:
    try:
        with path.open("rb") as fh:
            return fh.read(size)
    except OSError:
        return b""


def _read_tail(path: Path, size: int) -> bytes:
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            end = fh.tell()
            fh.seek(max(0, end - size))
            return fh.read(size)
    except OSError:
        return b""


def _bytes_of(spec: dict[str, Any]) -> bytes:
    if "ascii" in spec:
        return str(spec["ascii"]).encode("ascii")
    return bytes.fromhex(str(spec["hex"]))


def match_signal(index: GameIndex, sig: dict[str, Any]) -> str | None:
    """Evidence string when the signal matches the indexed game folder, else None."""
    if "file" in sig or "name" in sig:
        pattern = str(sig.get("file") or sig.get("name"))
        hits = index.files_matching_name(pattern)
        return hits[0].rel if hits else None
    if "path" in sig:
        hits = index.files_matching_path(str(sig["path"]))
        return hits[0].rel if hits else None
    if "dir" in sig:
        dirs = index.dirs_named(str(sig["dir"]))
        return dirs[0] + "/" if dirs else None
    if "dirpath" in sig:
        dirs = index.dirs_matching_path(str(sig["dirpath"]))
        return dirs[0] + "/" if dirs else None
    if "ext" in sig:
        ext = str(sig["ext"]).lower()
        stat = index.by_ext.get(ext)
        return f"{stat[0]} x *{ext}" if stat else None
    if "magic" in sig or "tail" in sig:
        spec = sig.get("magic") or sig.get("tail")
        want = _bytes_of(spec)
        offset = int(spec.get("offset", 0))
        for entry in index.files_matching_name(str(spec["name"]))[:MAGIC_MAX_FILES]:
            path = index.root / entry.rel
            if "magic" in sig:
                data = _read_head(path, offset + len(want))[offset:]
            else:
                data = _read_tail(path, len(want))
            if data == want:
                return f"{entry.rel} ({'tail ' if 'tail' in sig else ''}{want.decode('ascii', 'replace')})"
        return None
    return None


def score_entries(index: GameIndex, section: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for ident, spec in (section or {}).items():
        score = 0
        evidence: list[str] = []
        for sig in spec.get("signals", []):
            hit = match_signal(index, sig)
            if hit:
                score += int(sig.get("w", 1))
                evidence.append(hit)
        if score >= int(spec.get("min_score", 2)):
            found.append(
                {"id": ident, "name": spec.get("name", ident), "score": score, "evidence": evidence[:6]}
            )
    found.sort(key=lambda e: (-e["score"], e["id"]))
    return found


# ---- engine-specific probes ---------------------------------------------
def godot_pck_version(path: Path, embedded: bool = False) -> str | None:
    """Godot version from a PCK header ("GDPC", u32 pack format, u32 major, minor, patch)."""
    try:
        with path.open("rb") as fh:
            if embedded:
                fh.seek(-12, 2)
                tail = fh.read(12)
                if len(tail) < 12 or tail[8:] != b"GDPC":
                    return None
                (pck_size,) = struct.unpack("<Q", tail[:8])
                fh.seek(-12 - pck_size, 2)
            head = fh.read(20)
    except OSError:
        return None
    if len(head) < 20 or head[:4] != b"GDPC":
        return None
    _fmt, major, minor, patch = struct.unpack_from("<4I", head, 4)
    if major > 20:
        return None
    return f"{major}.{minor}.{patch}"


def renpy_version(game_dir: Path) -> str | None:
    for rel in ("renpy/vc_version.py", "renpy/__init__.py"):
        path = game_dir / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")[:20000]
        match = re.search(r"^version\s*=\s*u?['\"]([\d.]+)", text, re.M) or re.search(
            r"version_tuple\s*=\s*\((\d+),\s*(\d+),\s*(\d+)", text
        )
        if match:
            return ".".join(g for g in match.groups() if g)
    return None


def _engine_extra(engine_id: str, game_dir: Path, index: GameIndex) -> dict[str, Any]:
    if engine_id == "unity":
        return unity_info(game_dir)
    if engine_id == "godot":
        for entry in index.files_with_ext(".pck")[:MAGIC_MAX_FILES]:
            version = godot_pck_version(game_dir / entry.rel)
            if version:
                return {"version": version, "pck": entry.rel}
        for pattern in ("*.exe", "*.x86_64"):
            for entry in index.files_matching_name(pattern)[:MAGIC_MAX_FILES]:
                version = godot_pck_version(game_dir / entry.rel, embedded=True)
                if version:
                    return {"version": version, "pck": f"{entry.rel} (embedded)"}
        return {}
    if engine_id == "renpy":
        return {"version": renpy_version(game_dir)}
    if engine_id == "unreal":
        paks = index.files_matching_name("*.pak")
        projects = sorted({e.rel.split("/")[0] for e in paks if "/content/paks/" in e.rel.lower()})
        return {
            "iostore": bool(index.by_ext.get(".utoc")),
            "paks": len(paks),
            "project": projects[0] if projects else None,
            "version": "UE5 or late UE4 (IoStore)" if index.by_ext.get(".utoc") else "UE4 (.pak)",
        }
    if engine_id == "rpgmaker":
        variants = [
            ("js/rmmz_core.js", "MZ"),
            ("www/js/rpg_core.js", "MV"),
            ("js/rpg_core.js", "MV"),
            ("game.rgss3a", "VX Ace"),
            ("game.rgss2a", "VX"),
            ("game.rgssad", "XP"),
        ]
        for rel, name in variants:
            if index.files_matching_path(rel) or ("/" not in rel and index.files_named(rel)):
                return {"variant": name}
        return {}
    if engine_id in ("source", "source2"):
        infos = index.files_named("gameinfo.gi" if engine_id == "source2" else "gameinfo.txt")
        return {"mod_dirs": sorted({e.parent for e in infos})[:5]} if infos else {}
    return {}


def _middleware_extra(mw_id: str, spec: dict[str, Any], game_dir: Path, index: GameIndex) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for dll in spec.get("version_from", []):
        for entry in index.files_named(dll)[:3]:
            version = file_version(game_dir / entry.rel)
            if version:
                extra["version"] = version
                extra["version_source"] = entry.rel
                return extra
    if mw_id == "wwise":
        for entry in index.files_with_ext(".bnk")[:5]:
            head = _read_head(game_dir / entry.rel, 12)
            if head[:4] == b"BKHD" and len(head) >= 12:
                (ver,) = struct.unpack_from("<I", head, 8)
                extra["bank_version"] = ver if ver < 0x1000 else struct.unpack_from(">I", head, 8)[0]
                break
    if mw_id == "fmod":
        banks = index.files_with_ext(".bank")
        extra["banks"] = len(banks)
    return extra


def _fill_ref(template: str | None, info: dict[str, Any]) -> str | None:
    """'unity-{backend}' -> 'unity-il2cpp'; an unknown placeholder drops with its separator ('unity')."""
    if not template:
        return None

    def sub(match: re.Match[str]) -> str:
        value = info.get(match.group(2))
        return f"{match.group(1)}{value}" if isinstance(value, str) and value else ""

    return re.sub(r"([-_]?)\{(\w+)\}", sub, template)


def detect(game_dir: Path) -> dict[str, Any]:
    """Recon facts about a game folder (see module docstring)."""
    index = walk(game_dir)
    sigs = load_signatures()
    result: dict[str, Any] = {"game_dir": str(game_dir)}
    engines = score_entries(index, sigs.get("engines", {}))
    warnings: list[str] = []
    recon_refs: list[str] = []
    pack_refs: list[str] = []
    if engines:
        top = engines[0]
        spec = sigs["engines"][top["id"]]
        extra = _engine_extra(top["id"], game_dir, index)
        engine = {**top, **{k: v for k, v in extra.items() if v is not None}}
        for ref_key, bucket in (("recon_ref", recon_refs), ("pack_ref", pack_refs)):
            ref = _fill_ref(spec.get(ref_key), engine)
            if ref:
                bucket.append(ref)
        result["engine"] = engine
        if len(engines) > 1 and engines[1]["score"] >= top["score"] - 1:
            warnings.append(
                f"engine is ambiguous: {top['id']} ({top['score']}) vs {engines[1]['id']} ({engines[1]['score']})"
            )
    else:
        result["engine"] = {"id": "unknown", "name": "unknown", "score": 0, "evidence": []}
        recon_refs.append("engines/other")
        warnings.append(
            "engine not recognized from file signatures; see skills/babeldub-recon/references/engines/other.md "
            "for manual recon"
        )
    result["engine_candidates"] = [{"id": e["id"], "score": e["score"]} for e in engines[:5]]
    for section in ("middleware", "text_systems"):
        found = score_entries(index, sigs.get(section, {}))
        for item in found:
            spec = sigs[section][item["id"]]
            if section == "middleware":
                item.update(_middleware_extra(item["id"], spec, game_dir, index))
            for ref_key, bucket in (("recon_ref", recon_refs), ("pack_ref", pack_refs)):
                ref = _fill_ref(spec.get(ref_key), item)
                if ref and ref not in bucket:
                    bucket.append(ref)
        result[section] = found
    anticheat = score_entries(index, sigs.get("anticheat", {}))
    result["anticheat"] = anticheat
    for item in anticheat:
        warnings.append(
            f"anti-cheat detected: {item['name']} ({', '.join(item['evidence'][:2])}); modify only offline "
            "single-player games, never online/multiplayer ones"
        )
    if index.truncated:
        warnings.append(
            f"file walk stopped at {len(index.files)} files; assets deeper in the tree may be missed"
        )
    elif index.max_depth_hit:
        warnings.append("file walk hit the depth limit; very deep folders were not inspected")
    result["stats"] = {
        "files": len(index.files),
        "dirs": len(index.dirs),
        "bytes": index.total_bytes,
        "truncated": index.truncated,
        "max_depth_hit": index.max_depth_hit,
        "top_ext": index.ext_stats(20),
    }
    result["refs"] = {
        "recon": [f"skills/babeldub-recon/references/{r}.md" for r in recon_refs],
        "pack": [f"skills/babeldub-pack/references/formats/{r}.md" for r in pack_refs],
    }
    result["warnings"] = warnings
    return result
