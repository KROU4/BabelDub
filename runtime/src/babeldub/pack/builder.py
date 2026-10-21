"""build and verify: rebuild containers through their adapters and keep build_manifest.json.

Layout contract with adapters:
  * `out_path` handed to Adapter.build() is <build_root>/<asset.relpath> inside a per-container
    staging folder; the adapter may write a file there or a folder.
  * A folder result (loose files) or extra files (e.g. a patched Addressables catalog.json)
    are listed in result["files"] as game-relative paths and must be written to
    <build_root>/<that relpath>; an item may also be {"relpath": ..., "path": <absolute file>}.
  * Without "files", a file result maps to asset.relpath and a folder result maps every file
    inside it to asset.relpath/<sub path>.
Only after the adapter succeeded are the outputs moved into build/out, so a failed rebuild
never destroys the last good build.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from babeldub.adapters.base import Asset, get_adapter
from babeldub.core import layout
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import now_iso, read_json, write_json
from babeldub.core.output import EXIT_ATTENTION, EXIT_ERROR, EXIT_OK, EXIT_USAGE, BabelDubError, clip
from babeldub.core.project import Project
from babeldub.pack import installer, planner
from babeldub.pack.installer import FileTarget, clean_relpath, safe_join, sha1_file

MANIFEST_SCHEMA = "babeldub.build/1"
STAGING_DIR = "build/staging"


# ---- shared helpers ---------------------------------------------------------------------
def require_game_dir(project: Project) -> Path:
    game_dir = project.game_dir
    if not game_dir.is_dir():
        raise BabelDubError(
            f"game folder not found: {game_dir}",
            code=EXIT_USAGE,
            hint="fix game_dir in project.json or re-run `babeldub init <game_dir>`",
        )
    return game_dir


def load_manifest(project: Project, required: bool = True) -> dict[str, Any] | None:
    data = read_json(project.path(layout.BUILD_MANIFEST), default=None)
    if not data and required:
        raise BabelDubError("no build manifest yet", code=EXIT_USAGE, hint="run `babeldub build` first")
    return data


def manifest_files(container: dict[str, Any]) -> list[dict[str, Any]]:
    """Game files of a container; a minimal single-file entry is its own file list."""
    files = container.get("files")
    if files:
        return list(files)
    return [
        {
            "relpath": container["relpath"],
            "src_sha1": container.get("src_sha1"),
            "out": container["out"],
            "out_sha1": container["out_sha1"],
        }
    ]


def built_path(project: Project, ref: str) -> Path:
    """Absolute path of a manifest `out` (project-relative), which must stay inside build/out."""
    path = safe_join(project.root, ref)
    if not path.is_relative_to(project.path(layout.BUILD_DIR).resolve()):
        raise BabelDubError(f"manifest output outside {layout.BUILD_DIR}: {ref!r}", code=EXIT_USAGE)
    return path


def manifest_targets(project: Project, manifest: dict[str, Any]) -> list[FileTarget]:
    """Every game file the build wants installed. Identical duplicates collapse; different ones fail."""
    targets: dict[str, FileTarget] = {}
    for cont in manifest.get("containers", []):
        for item in manifest_files(cont):
            rel = clean_relpath(item["relpath"])
            target = FileTarget(
                relpath=rel,
                asset_id=cont["asset_id"],
                src_sha1=item.get("src_sha1"),
                out_sha1=item["out_sha1"],
                built=built_path(project, item["out"]),
            )
            other = targets.get(rel)
            if other is not None and other.out_sha1 != target.out_sha1:
                raise BabelDubError(
                    f"{rel} is produced by two containers with different content",
                    code=EXIT_USAGE,
                    containers=[other.asset_id, target.asset_id],
                    hint="build them so that one container owns the shared file, then build again",
                )
            targets.setdefault(rel, target)
    return list(targets.values())


def _sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _outputs_intact(project: Project, entry: dict[str, Any]) -> bool:
    try:
        for item in manifest_files(entry):
            path = built_path(project, item["out"])
            if not path.is_file() or sha1_file(path) != item["out_sha1"]:
                return False
    except (BabelDubError, KeyError):
        return False
    return True


def _remove_outputs(project: Project, entry: dict[str, Any]) -> None:
    root = project.path(layout.BUILD_DIR).resolve()
    for item in manifest_files(entry):
        try:
            path = built_path(project, item["out"])
        except (BabelDubError, KeyError):
            continue
        if path.is_file():
            installer.remove_file(path)
        parent = path.parent
        while parent != root and parent.is_relative_to(root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


# ---- build ------------------------------------------------------------------------------
@dataclass
class BuildContext:
    project: Project
    game_dir: Path
    options: dict[str, Any]
    installed: dict[str, dict[str, Any]]
    force: bool
    _sha: dict[Path, str] = field(default_factory=dict)

    @property
    def build_root(self) -> Path:
        return self.project.path(layout.BUILD_DIR)

    @property
    def staging_root(self) -> Path:
        return self.project.path(STAGING_DIR)

    def sha1(self, path: Path) -> str:
        key = path.resolve()
        if key not in self._sha:
            self._sha[key] = sha1_file(key)
        return self._sha[key]


def asset_rel(relpath: str) -> str:
    """Clean asset relpath; "" for an asset that is the game folder itself ("." in a loose scan)."""
    if str(relpath).strip().replace("\\", "/") in ("", ".", "./"):
        return ""
    return clean_relpath(relpath)


def _under(root: Path, rel: str) -> Path:
    return root.resolve() if not rel else safe_join(root, rel)


def _join(rel: str, sub: str) -> str:
    return f"{rel}/{sub}" if rel else sub


def _source_state(ctx: BuildContext, rel: str) -> tuple[str, str | None, list[str]]:
    """(fingerprint of the original source, its sha1 for a single file, files now holding our dub)."""
    path = _under(ctx.game_dir, rel)
    if rel and path.is_file():
        cur = ctx.sha1(path)
        entry = ctx.installed.get(rel)
        if entry and not entry.get("created") and entry.get("orig_sha1") and cur == entry.get("new_sha1"):
            return entry["orig_sha1"], entry["orig_sha1"], [rel]
        return cur, cur, []
    if path.is_dir():
        # (name, size, mtime) listing; a file holding our dub is represented by its backup,
        # which keeps the original size and mtime, so installing does not change the fingerprint
        items: list[str] = []
        ours: list[str] = []
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames.sort()
            for name in sorted(filenames):
                fp = Path(dirpath) / name
                sub = _join(rel, fp.relative_to(path).as_posix())
                entry = ctx.installed.get(sub)
                stat_of = fp
                if entry and ctx.sha1(fp) == entry.get("new_sha1"):
                    ours.append(sub)
                    if entry.get("created") or not entry.get("backup"):
                        continue
                    stat_of = safe_join(ctx.project.root, entry["backup"])
                    if not stat_of.is_file():
                        items.append(f"{sub}\tsha1:{entry.get('orig_sha1')}")
                        continue
                st = stat_of.stat()
                items.append(f"{sub}\t{st.st_size}\t{st.st_mtime_ns}")
        return "dir:" + _sha1_text("\n".join(items)), None, ours
    raise BabelDubError(
        f"{rel}: not found in the game folder",
        code=EXIT_USAGE,
        hint="the game changed since `babeldub scan`; re-run scan and extract",
    )


def _locate(grel: str, out: Path, staging: Path, rel: str) -> Path | None:
    if out.is_file() and grel == rel:
        return out
    candidates: list[Path] = []
    if out.is_dir() and (not rel or grel.startswith(rel + "/")):
        candidates.append(out / (grel[len(rel) + 1 :] if rel else grel))
    candidates.append(staging / grel)
    return next((c for c in candidates if c.is_file()), None)


def _collect_outputs(
    result: dict[str, Any], staged_out: Path, staging: Path, rel: str
) -> list[tuple[str, Path]]:
    """(game relpath, staged file) pairs from an adapter result (file, folder or explicit list)."""
    root = staging.resolve()
    out = Path(str(result["out"])) if result.get("out") else staged_out
    out = (out if out.is_absolute() else staging / out).resolve()
    pairs: list[tuple[str, Path]] = []
    files = result.get("files")
    if files:
        for item in files:
            if isinstance(item, dict):
                grel = clean_relpath(str(item.get("relpath", "")))
                given = item.get("path") or item.get("out")
                path = Path(str(given)).resolve() if given else _locate(grel, out, root, rel)
            else:
                grel = clean_relpath(str(item))
                path = _locate(grel, out, root, rel)
            if path is None or not path.is_file():
                raise BabelDubError(f"adapter listed {grel} but did not write it", code=EXIT_ERROR)
            pairs.append((grel, path))
    elif out.is_file() and rel:
        pairs.append((rel, out))
    elif out.is_dir():
        for fp in sorted(p for p in out.rglob("*") if p.is_file()):
            pairs.append((_join(rel, fp.relative_to(out).as_posix()), fp))
    if not pairs:
        raise BabelDubError("adapter produced no output", code=EXIT_ERROR)
    for grel, path in pairs:
        if not path.resolve().is_relative_to(root):
            raise BabelDubError(f"adapter wrote {grel} outside its staging folder", code=EXIT_ERROR)
    return pairs


def _inputs_sha1(
    cp: planner.ContainerPlan, rel: str, source: str, options: dict[str, Any], wavs: dict[str, str]
) -> str:
    payload = {
        "adapter": cp.asset.adapter,
        "relpath": rel,
        "source": source,
        "options": options,
        "lines": sorted([lid, digest] for lid, digest in wavs.items()),
    }
    return _sha1_text(json.dumps(payload, sort_keys=True))


def _build_container(
    ctx: BuildContext, cp: planner.ContainerPlan, prev: dict[str, Any] | None
) -> tuple[dict[str, Any], str]:
    asset = cp.asset
    rel = asset_rel(asset.relpath)
    source, src_sha, ours = _source_state(ctx, rel)
    wavs = {lid: ctx.sha1(path) for lid, path in cp.replacements.items()}
    inputs = _inputs_sha1(cp, rel, source, ctx.options, wavs)
    if not ctx.force and prev and prev.get("inputs_sha1") == inputs and _outputs_intact(ctx.project, prev):
        return prev, "up_to_date"
    if ours:
        raise BabelDubError(
            f"{rel}: the game folder holds the installed dub, not the original",
            code=EXIT_USAGE,
            installed=clip(ours, 3),
            hint="run `babeldub uninstall` first (a build must read the original files), "
            "or `babeldub build --only <asset_id>` for containers that are not installed",
        )
    staging = ctx.staging_root / make_uid(asset.asset_id)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    staged_out = staging / rel if rel else staging
    staged_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        adapter = get_adapter(asset.adapter)
        result = (
            adapter.build(ctx.game_dir, asset, dict(cp.replacements), staged_out, dict(ctx.options)) or {}
        )
        files: list[dict[str, Any]] = []
        for grel, path in _collect_outputs(result, staged_out, staging, rel):
            if grel == rel and src_sha is not None:
                src: str | None = src_sha
            else:
                game_file = safe_join(ctx.game_dir, grel)
                src = ctx.sha1(game_file) if game_file.is_file() else None
                entry = ctx.installed.get(grel)
                if entry and src is not None and src == entry.get("new_sha1"):
                    src = entry.get("orig_sha1")  # a shared file of ours: record its original
            files.append({"relpath": grel, "src_sha1": src, "out_sha1": sha1_file(path), "_staged": path})
        if prev:
            _remove_outputs(ctx.project, prev)
        for item in files:
            final = safe_join(ctx.build_root, item["relpath"])
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.is_dir():
                shutil.rmtree(final)
            os.replace(item.pop("_staged"), final)
            item["out"] = ctx.project.rel(final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    main = next((f for f in files if rel and f["relpath"] == rel), None)
    if main is not None:
        out_ref, out_sha = main["out"], main["out_sha1"]
    else:
        out_ref = ctx.project.rel(_under(ctx.build_root, rel))
        out_sha = _sha1_text(
            "\n".join(f"{f['relpath']}\t{f['out_sha1']}" for f in sorted(files, key=lambda f: f["relpath"]))
        )
    entry = {
        "asset_id": asset.asset_id,
        "adapter": asset.adapter,
        "relpath": rel,
        "src_sha1": src_sha if src_sha is not None else source,
        "out": out_ref,
        "out_sha1": out_sha,
        "replaced": int(result.get("replaced", len(cp.replacements))),
        "kept": result.get("kept"),
        "codec": result.get("codec", ctx.options.get("codec")),
        "lines": len(cp.replacements),
        "inputs_sha1": inputs,
        "built": now_iso(),
        "files": files,
        # what went in, so `verify` can compare the rebuilt audio with the exact replacement files;
        # `via`: an alt take/duplicate that got its leader's audio
        "replacements": {
            lid: {"wav": ctx.project.rel(path), "sha1": wavs[lid], **({"via": cp.via[lid]} if lid in cp.via else {})}
            for lid, path in sorted(cp.replacements.items())
        },
    }
    return entry, "built"


def _build_id(containers: list[dict[str, Any]], target_lang: str) -> str:
    core = [
        {
            "asset_id": c["asset_id"],
            "files": sorted([f["relpath"], f.get("src_sha1"), f["out_sha1"]] for f in manifest_files(c)),
        }
        for c in containers
    ]
    return _sha1_text(json.dumps({"lang": target_lang, "containers": core}, sort_keys=True))


def _conflicts(containers: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, tuple[str, str]] = {}
    out: list[str] = []
    for cont in containers:
        for item in manifest_files(cont):
            rel = item["relpath"]
            if rel in seen and seen[rel][1] != item["out_sha1"]:
                out.append(f"{rel}: {seen[rel][0]} vs {cont['asset_id']}")
            seen.setdefault(rel, (cont["asset_id"], item["out_sha1"]))
    return out


def _original_sizes(project: Project, installed: dict[str, dict[str, Any]]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for rel, entry in installed.items():
        if entry.get("backup"):
            try:
                path = safe_join(project.root, entry["backup"])
            except BabelDubError:
                continue
            if path.is_file():
                sizes[rel] = path.stat().st_size
    return sizes


def _write_manifest(
    project: Project,
    game_dir: Path,
    containers: list[dict[str, Any]],
    options: dict[str, Any],
    installed: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    manifest = {
        "_schema": MANIFEST_SCHEMA,
        "created": now_iso(),
        "build_id": _build_id(containers, project.target_lang),
        "game_fingerprint": planner.game_fingerprint(
            game_dir, planner.load_assets(project), _original_sizes(project, installed)
        ),
        "target_lang": project.target_lang,
        "options": options,
        "containers": containers,
        "models": planner.collect_models(project, planner.accepted_decisions(project)) if containers else [],
    }
    write_json(project.path(layout.BUILD_MANIFEST), manifest)
    return manifest


def run_build(
    project: Project,
    *,
    codec: str,
    sample_rate: int | None,
    only: set[str] | None,
    force: bool,
    dry_run: bool,
) -> tuple[dict[str, Any], int]:
    game_dir = require_game_dir(project)
    plan = planner.plan_build(project, only)
    attention: dict[str, Any] = {k: clip(v, 5) for k, v in plan.attention().items()}
    if only is not None and plan.unmatched_only and not plan.containers:
        raise BabelDubError(
            "--only matches no container with accepted lines",
            code=EXIT_USAGE,
            unmatched=clip(plan.unmatched_only, 5),
            hint="use asset ids from inventory/assets.jsonl (see `babeldub query`)",
        )
    options: dict[str, Any] = {"codec": codec}
    if sample_rate:
        options["sample_rate"] = sample_rate
    if not plan.containers:
        hint = (
            "no accepted line with processed audio: run `babeldub qa` / `babeldub decide apply` "
            "(decision=accept) and `babeldub post` (post/audio/<uid>.wav)"
        )
        dropped = 0
        if not dry_run:
            prev_manifest = load_manifest(project, required=False) or {}
            stale = [c for c in prev_manifest.get("containers", []) if isinstance(c, dict)]
            if stale:  # the build must reflect the decisions: nothing accepted means no dub
                for entry in stale:
                    _remove_outputs(project, entry)
                installed = installer.active_entries(installer.load_state(project.root))
                _write_manifest(project, game_dir, [], options, installed)
                dropped = len(stale)
                hint += f"; the previous build ({dropped} containers) was emptied"
            project.mark_stage("build", "attention", containers=0, replaced=0, dropped=dropped)
        return {
            "containers": [],
            "replaced": 0,
            "accepted": plan.accepted,
            "dropped": dropped,
            "attention": attention,
            "hint": hint,
        }, EXIT_ATTENTION
    if dry_run:
        rows = [
            {"asset_id": c.asset_id, "adapter": c.asset.adapter, "lines": len(c.replacements)}
            for c in plan.containers
        ]
        payload = {"dry_run": True, "containers": clip(rows), "lines": plan.lines, "options": options}
        if attention:
            payload["attention"] = attention
        return payload, EXIT_ATTENTION if attention else EXIT_OK

    prev_manifest = load_manifest(project, required=False) or {}
    prev = {
        c["asset_id"]: c
        for c in prev_manifest.get("containers", [])
        if isinstance(c, dict) and "asset_id" in c
    }
    installed = installer.active_entries(installer.load_state(project.root))
    ctx = BuildContext(project=project, game_dir=game_dir, options=options, installed=installed, force=force)
    built: dict[str, dict[str, Any]] = {}
    statuses: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    fail_codes: list[int] = []
    for cp in plan.containers:
        try:
            entry, status = _build_container(ctx, cp, prev.get(cp.asset_id))
        except BabelDubError as err:
            failed.append(
                {"asset_id": cp.asset_id, "error": str(err), **({"hint": err.hint} if err.hint else {})}
            )
            fail_codes.append(err.code)
            continue
        except Exception as exc:  # an adapter bug must not stop the other containers
            failed.append({"asset_id": cp.asset_id, "error": f"{type(exc).__name__}: {exc}"})
            fail_codes.append(EXIT_ERROR)
            continue
        built[cp.asset_id] = entry
        statuses.append(
            {
                "asset_id": cp.asset_id,
                "status": status,
                "replaced": entry.get("replaced"),
                "kept": entry.get("kept"),
                "codec": entry.get("codec"),
            }
        )
    shutil.rmtree(ctx.staging_root, ignore_errors=True)
    if not built:
        project.mark_stage("build", "failed", containers=0, failed=len(failed))
        first = failed[0]
        raise BabelDubError(
            f"no container was built: {first['error']}",
            code=fail_codes[0],
            failed=clip(failed, 5),
            hint=first.get("hint", "see the adapter error above"),
        )

    containers: dict[str, dict[str, Any]] = {}
    if only is not None:
        containers.update(prev)  # a partial build keeps every other container as it was
    else:
        failed_ids = {f["asset_id"] for f in failed}
        for aid, entry in prev.items():
            if aid in failed_ids and _outputs_intact(project, entry):
                containers[aid] = entry  # keep the last good build of a container that failed now
            elif aid not in built:
                _remove_outputs(project, entry)  # nothing accepted any more: drop stale output
    containers.update(built)
    ordered = [containers[k] for k in sorted(containers)]
    conflicts = _conflicts(ordered)
    if conflicts:
        attention["conflicting_outputs"] = clip(conflicts, 5)
    partial = [
        c["asset_id"] for c in built.values() if int(c.get("replaced") or 0) < int(c.get("lines") or 0)
    ]
    if partial:
        attention["partial"] = clip(partial, 5)
    manifest = _write_manifest(project, game_dir, ordered, options, installed)
    replaced = sum(int(c.get("replaced") or 0) for c in ordered)
    needs_attention = bool(failed or attention)
    project.mark_stage(
        "build",
        "attention" if needs_attention else "done",
        containers=len(ordered),
        replaced=replaced,
        build_id=manifest["build_id"],
        failed=len(failed),
    )
    payload: dict[str, Any] = {
        "containers": clip(statuses),
        "replaced": replaced,
        "total_containers": len(ordered),
        "build_id": manifest["build_id"],
        "manifest": layout.BUILD_MANIFEST,
    }
    if plan.followers:
        payload["followers"] = plan.followers
    if failed:
        payload["failed"] = clip(failed, 5)
    if attention:
        payload["attention"] = attention
    payload["next"] = "babeldub verify"
    return payload, EXIT_ATTENTION if needs_attention else EXIT_OK


# ---- verify -----------------------------------------------------------------------------
def _replacement_options(project: Project, cont: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Build inputs for adapter.verify: replaced line ids, their wavs (only if unchanged), sample rate.

    A replacement whose post file changed after the build is passed by id only (format checks),
    since its audio no longer matches what went into the container.
    """
    out: dict[str, Any] = {}
    reps = cont.get("replacements") or {}
    if reps:
        exact: dict[str, str] = {}
        for lid, rec in sorted(reps.items()):
            ref = str((rec or {}).get("wav") or "")
            if not ref:
                continue
            path = Path(ref) if Path(ref).is_absolute() else project.root / ref
            if path.is_file() and sha1_file(path) == (rec or {}).get("sha1"):
                exact[lid] = str(path)
        out["replaced_ids"] = sorted(reps)
        if exact:
            out["replacements"] = exact
    rate = (manifest.get("options") or {}).get("sample_rate")
    if rate:
        out["sample_rate"] = rate
    return out


def run_verify(project: Project, *, game_runtime: bool) -> tuple[dict[str, Any], int]:
    game_dir = require_game_dir(project)
    manifest = load_manifest(project) or {}
    assets = planner.load_assets(project)
    problems: list[dict[str, Any]] = []
    checks = 0
    verified = 0
    try:
        manifest_targets(project, manifest)
    except BabelDubError as err:
        problems.append({"asset_id": None, "problem": str(err)})
    for cont in manifest.get("containers", []):
        aid = cont["asset_id"]
        own: list[str] = []
        files: list[dict[str, str]] = []
        for item in manifest_files(cont):
            try:
                path = built_path(project, item["out"])
            except BabelDubError as err:
                own.append(str(err))
                continue
            if not path.is_file():
                own.append(f"{item['relpath']}: built file is missing")
            elif sha1_file(path) != item["out_sha1"]:
                own.append(f"{item['relpath']}: built file changed after build")
            files.append({"relpath": item["relpath"], "path": str(path)})
        if own:
            problems.extend({"asset_id": aid, "problem": p} for p in own)
            continue
        asset = assets.get(aid) or Asset(
            asset_id=aid, adapter=cont["adapter"], relpath=cont["relpath"], role="voice"
        )
        options = {
            "game_runtime": game_runtime,
            "codec": cont.get("codec"),
            "files": files,
            **_replacement_options(project, cont, manifest),
        }
        try:
            built = built_path(project, cont["out"])
            res = get_adapter(asset.adapter).verify(game_dir, asset, built, options) or {}
        except BabelDubError as err:
            problems.append(
                {"asset_id": aid, "problem": str(err), **({"hint": err.hint} if err.hint else {})}
            )
            continue
        except Exception as exc:
            problems.append({"asset_id": aid, "problem": f"{type(exc).__name__}: {exc}"})
            continue
        verified += 1
        checks += len(res.get("checks") or [])
        if not res.get("ok", False):
            for problem in res.get("problems") or ["adapter verification failed"]:
                problems.append({"asset_id": aid, "problem": str(problem)})
    ok = not problems
    project.mark_stage(
        "verify",
        "done" if ok else "attention",
        ok=ok,
        build_id=manifest.get("build_id"),
        containers=verified,
        problems=len(problems),
        game_runtime=game_runtime,
    )
    payload: dict[str, Any] = {
        "ok": ok,
        "containers": len(manifest.get("containers", [])),
        "verified": verified,
        "checks": checks,
        "problems": clip(problems),
        "build_id": manifest.get("build_id"),
    }
    payload["next"] = (
        "babeldub install" if ok else "fix the problems, then `babeldub build` and `babeldub verify`"
    )
    return payload, EXIT_OK if ok else EXIT_ATTENTION
