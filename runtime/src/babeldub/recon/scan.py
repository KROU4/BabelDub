"""`babeldub scan`: detect the engine, ask every adapter for its assets, write the inventory.

Outputs (project folder): inventory/recon.json, inventory/assets.jsonl, inventory/report.md.
A failing adapter never aborts the scan: its error becomes a warning. Voice and text assets get a
sha1 (streamed for big files; reused from the previous scan when size and mtime are unchanged);
a folder asset gets a listing fingerprint (sha1 over name/size/mtime of its files).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from babeldub.adapters import base
from babeldub.adapters.base import Asset, get_adapter
from babeldub.core import layout
from babeldub.core.jsonl import now_iso, read_jsonl, write_json, write_jsonl
from babeldub.core.output import EXIT_USAGE, BabelDubError
from babeldub.core.project import Project
from babeldub.recon.detect import detect
from babeldub.recon.report import render_report
from babeldub.recon.walk import clear_cache

SHA1_ROLES = ("voice", "text")
LEVEL_ORDER = {"A": 0, "B": 1, "C": 2}


def file_sha1(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def dir_fingerprint(root: Path) -> tuple[str, int]:
    """sha1 over 'relpath<TAB>size<TAB>mtime_ns' of every file below root; also the newest mtime."""
    digest = hashlib.sha1()
    newest = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            try:
                st = path.stat()
            except OSError:
                continue
            newest = max(newest, st.st_mtime_ns)
            rel = path.relative_to(root).as_posix()
            digest.update(f"{rel}\t{st.st_size}\t{st.st_mtime_ns}\n".encode())
    return digest.hexdigest(), newest


def fill_sha1(game_dir: Path, assets: list[Asset], previous: dict[str, dict[str, Any]], force: bool) -> int:
    """Set sha1 on voice/text assets; returns how many hashes were computed (not reused)."""
    computed = 0
    for asset in assets:
        if asset.role not in SHA1_ROLES:
            continue
        path = game_dir / asset.relpath
        try:
            if path.is_dir():
                asset.sha1, newest = dir_fingerprint(path)
                asset.info.setdefault("mtime_ns", newest)
                asset.info["sha1_of"] = "listing"
                continue
            st = path.stat()
        except OSError:
            continue
        asset.info["mtime_ns"] = st.st_mtime_ns
        prev = previous.get(asset.asset_id)
        if (
            not force
            and prev
            and prev.get("sha1")
            and prev.get("size") == st.st_size
            and (prev.get("info") or {}).get("mtime_ns") == st.st_mtime_ns
        ):
            asset.sha1 = prev["sha1"]
            continue
        asset.sha1 = file_sha1(path)
        computed += 1
    return computed


def overall_level(assets: list[Asset]) -> str:
    voice = [a.level for a in assets if a.role == "voice"]
    if not voice:
        return "C"
    return min(voice, key=lambda lv: LEVEL_ORDER.get(lv, 9))


def _asset_label(asset: Asset) -> str:
    parts = [p for p in (asset.locale, asset.level) if p]
    return f"{asset.asset_id} [{', '.join(parts)}]" if parts else asset.asset_id


def run_scan(project: Project, force: bool = False) -> tuple[dict[str, Any], list[Asset], dict[str, Any]]:
    """Run recon; returns (recon dict, assets, compact summary for the CLI)."""
    game_dir = project.game_dir
    if not game_dir.is_dir():
        raise BabelDubError(
            f"game folder not found: {game_dir}",
            code=EXIT_USAGE,
            hint="check game_dir in project.json (`babeldub set game_dir=...` or re-run init)",
        )
    clear_cache()
    recon = detect(game_dir)
    warnings: list[str] = list(recon.get("warnings", []))
    assets: list[Asset] = []
    adapters_report: dict[str, Any] = {}
    for name in list(base.REGISTRY):
        try:
            adapter = get_adapter(name)
            found = adapter.discover(game_dir)
        except BabelDubError as err:
            warnings.append(f"adapter {name} failed: {err}")
            adapters_report[name] = {"error": str(err)}
            continue
        except Exception as exc:  # broken optional dependency or a parser bug: report, keep scanning
            warnings.append(f"adapter {name} failed: {type(exc).__name__}: {exc}")
            adapters_report[name] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        adapters_report[name] = {"assets": len(found), "level": adapter.level}
        for asset in found:
            if asset.info.get("warning"):
                warnings.append(f"{asset.asset_id}: {asset.info['warning']}")
        assets.extend(found)
    previous = {r["asset_id"]: r for r in read_jsonl(project.path(layout.ASSETS)) if "asset_id" in r}
    hashed = fill_sha1(game_dir, assets, previous, force)
    level = overall_level(assets)
    voice = [a for a in assets if a.role == "voice"]
    text = [a for a in assets if a.role == "text"]
    if not voice:
        warnings.append(
            "no voice assets found by the built-in adapters; follow the recon references for this engine "
            "(manual extraction or a runtime approach)"
        )
    recon.update(
        {
            "scanned": now_iso(),
            "game_name": project.data.get("game_name"),
            "adapters": adapters_report,
            "level": level,
            "counts": {
                role: sum(1 for a in assets if a.role == role) for role in ("voice", "text", "audio", "other")
            },
            "warnings": warnings,
        }
    )
    if any(a.adapter == "loose" and a.role == "voice" for a in assets):
        for bucket, ref in (
            ("recon", "skills/babeldub-recon/references/audio/loose.md"),
            ("pack", "skills/babeldub-pack/references/formats/loose.md"),
        ):
            if ref not in recon["refs"][bucket]:
                recon["refs"][bucket].append(ref)
    write_json(project.path(layout.RECON), recon)
    write_jsonl(project.path(layout.ASSETS), (a.to_row() for a in assets), schema="babeldub.assets/1")
    report = render_report(recon, assets, project.data)
    report_path = project.path(layout.INVENTORY_REPORT)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8", newline="\n")
    engine = recon["engine"]
    summary: dict[str, Any] = {
        "engine": {k: engine.get(k) for k in ("id", "version", "backend") if engine.get(k)},
        "middleware": [
            {k: m.get(k) for k in ("id", "version") if m.get(k)} for m in recon.get("middleware", [])
        ],
        "text_systems": [t["id"] for t in recon.get("text_systems", [])],
        "voice_assets": [_asset_label(a) for a in voice],
        "text_assets": [f"{a.relpath.rsplit('/', 1)[-1]} [{a.locale or '-'}]" for a in text],
        "counts": recon["counts"],
        "level": level,
        "anticheat": [a["id"] for a in recon.get("anticheat", [])],
        "warnings": warnings,
        "sha1_computed": hashed,
    }
    return recon, assets, summary
