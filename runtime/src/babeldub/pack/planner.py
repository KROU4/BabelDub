"""Build planning: which containers to rebuild, with which accepted audio, and what to record.

Input: qa/decisions.jsonl (last decision per line wins), inventory/lines.jsonl and
inventory/assets.jsonl, post/audio/<uid>.wav. A line goes into the build only when its last
decision is `accept` and its processed audio exists; anything else is reported, never guessed.
Alt takes and duplicates (core/takes.py followers) get their leader's accepted audio; their own
decisions are ignored, and they keep the original when the leader is not accepted.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from babeldub.adapters.base import Asset
from babeldub.core import layout
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import latest_by, read_jsonl
from babeldub.core.paths import data_dir
from babeldub.core.project import Project
from babeldub.core.takes import follower_map

ACCEPT = "accept"


@dataclass
class ContainerPlan:
    asset: Asset
    replacements: dict[str, Path] = field(default_factory=dict)  # line id -> processed wav
    attempts: dict[str, int | None] = field(default_factory=dict)
    via: dict[str, str] = field(default_factory=dict)  # follower line id -> leader whose audio it reuses

    @property
    def asset_id(self) -> str:
        return self.asset.asset_id


@dataclass
class BuildPlan:
    containers: list[ContainerPlan] = field(default_factory=list)
    accepted: int = 0
    missing_wav: list[str] = field(default_factory=list)
    unknown_lines: list[str] = field(default_factory=list)
    unknown_assets: list[str] = field(default_factory=list)
    stale_attempt: list[str] = field(default_factory=list)
    unmatched_only: list[str] = field(default_factory=list)
    filtered_out: int = 0
    followers: int = 0  # alt takes/duplicates replaced with their leader's audio

    @property
    def lines(self) -> int:
        return sum(len(c.replacements) for c in self.containers)

    def attention(self) -> dict[str, list[str]]:
        items = {
            "missing_wav": self.missing_wav,
            "unknown_lines": self.unknown_lines,
            "unknown_assets": self.unknown_assets,
            "stale_attempt": self.stale_attempt,
        }
        return {k: v for k, v in items.items() if v}


def load_assets(project: Project) -> dict[str, Asset]:
    rows = latest_by(read_jsonl(project.path(layout.ASSETS)), "asset_id")
    return {aid: Asset.from_row(row) for aid, row in rows.items()}


def accepted_decisions(project: Project) -> dict[str, dict[str, Any]]:
    latest = latest_by(read_jsonl(project.path(layout.DECISIONS)))
    return {lid: row for lid, row in latest.items() if row.get("decision") == ACCEPT}


def _resolve(project: Project, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project.root / path


def _post_wav(project: Project, uid: str, post_row: dict[str, Any] | None) -> Path | None:
    default = project.path(layout.POST_DIR, f"{uid}.wav")
    if default.is_file():
        return default
    if post_row and post_row.get("wav"):
        alt = _resolve(project, str(post_row["wav"]))
        if alt.is_file():
            return alt
    return None


def _matches(asset: Asset, only: set[str]) -> bool:
    return asset.asset_id in only or asset.relpath.replace("\\", "/") in only


def plan_build(project: Project, only: set[str] | None = None) -> BuildPlan:
    """Group accepted lines by container. `only` limits the plan to asset ids (or relpaths)."""
    plan = BuildPlan()
    accepted = accepted_decisions(project)
    plan.accepted = len(accepted)
    if not accepted:
        return plan
    lines = latest_by(read_jsonl(project.path(layout.LINES)))
    posts = latest_by(read_jsonl(project.path(layout.POST)))
    assets = load_assets(project)
    follows = follower_map(lines.values())
    by_leader: dict[str, list[str]] = {}
    for follower, leader in sorted(follows.items()):
        by_leader.setdefault(leader, []).append(follower)
    by_asset: dict[str, ContainerPlan] = {}
    matched_only: set[str] = set()
    for line_id in sorted(accepted):
        if line_id in follows:  # dubbed through its leader below
            continue
        decision = accepted[line_id]
        line = lines.get(line_id)
        if line is None:
            plan.unknown_lines.append(line_id)
            continue
        asset_id = line.get("asset_id")
        asset = assets.get(asset_id) if asset_id else None
        if asset is None:
            if asset_id not in plan.unknown_assets:
                plan.unknown_assets.append(str(asset_id))
            continue
        if only is not None:
            if not _matches(asset, only):
                plan.filtered_out += 1
                continue
            matched_only.update({asset.asset_id, asset.relpath.replace("\\", "/")})
        post_row = posts.get(line_id)
        want, have = decision.get("attempt"), (post_row or {}).get("attempt")
        if want is not None and have is not None and want != have:
            plan.stale_attempt.append(line_id)
            continue
        uid = str(line.get("uid") or make_uid(line_id))
        wav = _post_wav(project, uid, post_row)
        if wav is None:
            plan.missing_wav.append(line_id)
            continue
        cont = by_asset.setdefault(asset.asset_id, ContainerPlan(asset=asset))
        cont.replacements[line_id] = wav
        cont.attempts[line_id] = want if want is not None else have
        for follower in by_leader.get(line_id, []):
            if (lines.get(follower) or {}).get("asset_id") != asset.asset_id:
                continue
            cont.replacements[follower] = wav
            cont.attempts[follower] = cont.attempts[line_id]
            cont.via[follower] = line_id
            plan.followers += 1
    if only is not None:
        plan.unmatched_only = sorted(o for o in only if o not in matched_only)
    plan.containers = sorted(by_asset.values(), key=lambda c: c.asset_id)
    return plan


# ---- models used by the accepted takes ------------------------------------------------------
def models_catalog_path() -> Path:
    return data_dir() / "models.yaml"


def load_models_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or models_catalog_path()
    if not target.is_file():
        return []
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    models = data.get("models") if isinstance(data, dict) else None
    return [m for m in models or [] if isinstance(m, dict)]


def _basename(text: str) -> str:
    return re.split(r"[\\/]", text.rstrip("\\/"))[-1]


def _match_model(catalog: list[dict[str, Any]], model: str) -> dict[str, Any] | None:
    keys = {model.lower(), _basename(model).lower()}
    for entry in catalog:
        names = {str(entry.get("name", "")).lower(), str(entry.get("repo", "")).lower()}
        names.add(_basename(str(entry.get("repo", ""))).lower())
        if keys & (names - {""}):
            return entry
    return None


def collect_models(project: Project, accepted: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """{name, revision, license, engine, lines} for the TTS models behind the accepted takes."""
    chosen: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(project.path(layout.SYNTH_RESULTS)):
        line_id = row.get("id")
        if line_id not in accepted or row.get("status", "ok") != "ok":
            continue
        want = accepted[line_id].get("attempt")
        if want is not None and row.get("attempt") is not None and row.get("attempt") != want:
            continue
        chosen[line_id] = row
    catalog = load_models_catalog()
    out: dict[tuple[str, str | None], dict[str, Any]] = {}
    for row in chosen.values():
        model = str(row.get("model") or "")
        engine = row.get("engine")
        if not model and not engine:
            continue
        entry = _match_model(catalog, model) if model else None
        name = str(entry.get("name")) if entry else (_basename(model) if model else str(engine))
        revision = row.get("revision") or (entry or {}).get("revision")
        key = (name, revision)
        if key not in out:
            out[key] = {
                "name": name,
                "revision": revision,
                "license": (entry or {}).get("license"),
                "engine": engine or (entry or {}).get("engine"),
                "lines": 0,
            }
        out[key]["lines"] += 1
    return sorted(out.values(), key=lambda m: (str(m["name"]), str(m["revision"])))


# ---- game fingerprint -----------------------------------------------------------------------
def _tree_size(game_dir: Path, rel: str, overrides: dict[str, int]) -> int:
    path = game_dir / rel
    if path.is_file():
        return overrides.get(rel, path.stat().st_size)
    if not path.is_dir():
        return -1
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            sub = (Path(dirpath) / name).relative_to(game_dir).as_posix()
            total += overrides.get(sub, (Path(dirpath) / name).stat().st_size)
    return total


def game_fingerprint(
    game_dir: Path, assets: dict[str, Asset], overrides: dict[str, int] | None = None
) -> str:
    """sha1 over sorted (relpath, size) of the voice containers, using sizes of the originals.

    `overrides` maps relpath -> original size for files that currently hold our installed build.
    """
    sizes = overrides or {}
    items = sorted(
        f"{rel}\t{_tree_size(game_dir, rel, sizes)}"
        for rel in {a.relpath.replace("\\", "/") for a in assets.values() if a.role == "voice"}
    )
    return hashlib.sha1("\n".join(items).encode("utf-8")).hexdigest()
