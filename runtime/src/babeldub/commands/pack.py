"""Pack stage: build, verify, install, uninstall, reapply (docs/dev/CONTRACT.md section 5).

install / uninstall / reapply are the only commands that write into the game folder. Each of
them refuses while the game is running; install and reapply also refuse when an anti-cheat
is found (--force overrides both guards for an offline game). Content checks are never
overridden: a game file that is not the original of the current build blocks the install.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from babeldub.core import layout
from babeldub.core.jsonl import read_json
from babeldub.core.output import EXIT_ATTENTION, EXIT_OK, EXIT_USAGE, BabelDubError, clip, emit
from babeldub.core.project import Project
from babeldub.pack import builder, guards, installer
from babeldub.pack.installer import ApplyResult, FileAction, FileTarget, Installer, sha1_file

CODECS = ["auto", "pcm16", "fadpcm"]


# ---- argument parsing -------------------------------------------------------------------
def register(sub: Any, common: Callable[[argparse.ArgumentParser], None]) -> None:
    p = sub.add_parser("build", help="rebuild game containers with the accepted dubbed lines")
    p.add_argument(
        "--codec",
        choices=CODECS,
        default=None,
        help="audio codec inside the container (default: project build.codec)",
    )
    p.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="ASSET_ID",
        help="rebuild only these containers (asset id or relpath; repeat or comma-separate)",
    )
    p.add_argument("--sample-rate", type=int, default=None, help="resample replacements to this rate")
    p.add_argument("--force", action="store_true", help="rebuild containers even when inputs did not change")
    p.add_argument("--dry-run", action="store_true", help="show what would be built")
    common(p)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("verify", help="check every rebuilt container (adapter.verify)")
    p.add_argument(
        "--game-runtime", action="store_true", help="let adapters use the game's own audio runtime for checks"
    )
    common(p)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("install", help="back up originals and put the verified build into the game folder")
    p.add_argument(
        "--force", action="store_true", help="install even if the game seems running or has an anti-cheat"
    )
    p.add_argument("--dry-run", action="store_true", help="show the plan without touching the game folder")
    common(p)
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="restore the original game files from the backup")
    p.add_argument(
        "--force", action="store_true", help="also restore files changed since install; ignore a running game"
    )
    p.add_argument("--dry-run", action="store_true", help="show the plan without touching the game folder")
    common(p)
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("reapply", help="install the dub again after a game update or integrity check")
    p.add_argument(
        "--force", action="store_true", help="reapply even if the game seems running or has an anti-cheat"
    )
    p.add_argument("--dry-run", action="store_true", help="show the plan without touching the game folder")
    common(p)
    p.set_defaults(func=cmd_reapply)


def _split(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    out = {part.strip().replace("\\", "/") for value in values for part in value.split(",") if part.strip()}
    return out or None


# ---- build / verify ---------------------------------------------------------------------
def cmd_build(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    codec = args.codec or str(project.setting("build", "codec", "auto") or "auto")
    payload, code = builder.run_build(
        project,
        codec=codec,
        sample_rate=args.sample_rate,
        only=_split(args.only),
        force=args.force,
        dry_run=args.dry_run,
    )
    return emit(payload, code)


def cmd_verify(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    payload, code = builder.run_verify(project, game_runtime=args.game_runtime)
    return emit(payload, code)


# ---- shared checks for commands that write into the game ------------------------------
def _require_verified(project: Project, manifest: dict[str, Any]) -> None:
    prog = project.progress().get("verify") or {}
    if prog.get("status") != "done" or prog.get("build_id") != manifest.get("build_id"):
        raise BabelDubError(
            "the current build has not passed `babeldub verify`",
            code=EXIT_USAGE,
            hint="run `babeldub verify` and fix its problems first",
        )


def _check_guards(project: Project, game_dir: Path, force: bool, anticheat: bool) -> list[str]:
    warnings: list[str] = []
    running = guards.check_running(game_dir)
    procs = running.get("processes") or []
    if procs:
        shown = clip([{k: p.get(k) for k in ("pid", "name", "exe")} for p in procs], 5)
        if not force:
            raise BabelDubError(
                "the game is running",
                code=EXIT_USAGE,
                processes=shown,
                hint="close the game and its launcher, then retry",
            )
        warnings.append(f"game seems to be running ({len(procs)} process(es)); continuing because of --force")
    elif running.get("error"):
        warnings.append(f"could not check running processes: {running['error']}")
    if anticheat:
        try:
            recon = read_json(project.path(layout.RECON), default=None)
        except ValueError:
            recon = None
            warnings.append(f"{layout.RECON} is not valid JSON; only the folder scan was used for anti-cheat")
        found = guards.detect_anticheat(game_dir, recon)
        if found:
            if not force:
                raise BabelDubError(
                    "anti-cheat detected: BabelDub does not modify games protected by an anti-cheat",
                    code=EXIT_USAGE,
                    anticheat=clip(found, 5),
                    hint="modified files can get the account banned; only for an offline single-player game "
                    "re-run with --force",
                )
            names = ", ".join(sorted({f["name"] for f in found}))
            warnings.append(f"anti-cheat present ({names}); installing because of --force")
    return warnings


def _check_built(targets: list[FileTarget]) -> None:
    bad = [t.relpath for t in targets if not t.built.is_file() or sha1_file(t.built) != t.out_sha1]
    if bad:
        raise BabelDubError(
            "built files are missing or changed since `babeldub build`",
            code=EXIT_USAGE,
            files=clip(bad, 5),
            hint="run `babeldub build` and `babeldub verify` again",
        )


def _load_for_write(args: argparse.Namespace) -> tuple[Project, Path, dict[str, Any], Installer]:
    project = Project.load(args.project)
    game_dir = builder.require_game_dir(project)
    manifest = builder.load_manifest(project) or {}
    _require_verified(project, manifest)
    return project, game_dir, manifest, Installer(project.root, game_dir)


def _apply_journaled(
    project: Project,
    inst: Installer,
    actions: list[FileAction],
    prev: dict[str, dict[str, Any]],
    keep: list[dict[str, Any]],
    build_id: str | None,
) -> tuple[ApplyResult, list[dict[str, Any]]]:
    """Apply actions with a write-ahead install.json, so a crash never loses track of our files.

    `keep` are entries of other files that stay as they are. Returns the apply result and the
    entries describing the game folder afterwards (keep + this plan).
    """
    new_entries = inst.entries_for(actions)
    covered = {e["relpath"] for e in new_entries}
    keep = [e for e in keep if e["relpath"] not in covered]
    installer.save_state(
        project.root, {"installed": keep + new_entries, "state": "installing", "build_id": build_id}
    )
    try:
        result = inst.apply(actions)
    except Exception as exc:
        stuck = set(exc.extra.get("stuck") or []) if isinstance(exc, BabelDubError) else set()
        by_rel = {e["relpath"]: e for e in new_entries}
        recovered: list[dict[str, Any]] = []
        for act in actions:
            rel = act.target.relpath
            if rel in stuck or act.kind == installer.CURRENT:
                recovered.append(by_rel[rel])
            elif rel in prev:
                recovered.append(prev[rel])
        rows = keep + recovered
        installer.save_state(
            project.root,
            {"installed": rows, "state": "installed" if rows else "uninstalled", "build_id": build_id},
        )
        raise
    return result, keep + new_entries


def _counts(kinds: list[str]) -> dict[str, int]:
    return dict(Counter(kinds))


# ---- install ----------------------------------------------------------------------------
def cmd_install(args: argparse.Namespace) -> int:
    project, game_dir, manifest, inst = _load_for_write(args)
    warnings = _check_guards(project, game_dir, args.force, anticheat=True)
    state = installer.load_state(project.root)
    prev = installer.active_entries(state)
    targets = builder.manifest_targets(project, manifest)
    _check_built(targets)
    actions = inst.plan(targets, prev)
    blocked = [a for a in actions if a.blocked]
    if blocked:
        raise BabelDubError(
            f"{len(blocked)} game file(s) are not the originals this build was made from",
            code=EXIT_USAGE,
            changed=clip([a.describe() for a in blocked], 5),
            hint="the game was updated or modified after the build: run `babeldub build` "
            "(with --only <asset_id> if other containers are installed), `babeldub verify`, `babeldub install`",
        )
    planned = {t.relpath for t in targets}
    stale = inst.plan_restore([e for rel, e in prev.items() if rel not in planned])
    if args.dry_run:
        return emit(
            {
                "dry_run": True,
                "plan": _counts([a.kind for a in actions]),
                "write": clip([a.target.relpath for a in actions if a.writes]),
                "stale": clip([f"{a.relpath} ({a.kind})" for a in stale]),
                "warnings": warnings,
            }
        )
    inst.check_space(actions)
    build_id = manifest.get("build_id")
    result, entries = _apply_journaled(project, inst, actions, prev, [a.entry for a in stale], build_id)
    # files of containers that are no longer part of the build go back to their originals
    inst.restore(stale)
    stale_done = [a.relpath for a in stale if a.outcome in ("restored", "removed")]
    stale_stuck = {a.relpath for a in stale if a.outcome == "failed"}
    stale_rels = {a.relpath for a in stale}
    final = [e for e in entries if e["relpath"] not in stale_rels or e["relpath"] in stale_stuck]
    new_state = "installed" if final else "uninstalled"  # an empty build only restores originals
    installer.save_state(project.root, {"installed": final, "state": new_state, "build_id": build_id})
    project.mark_stage(
        "install", "done", state=new_state, files=len(final), written=len(result.written), build_id=build_id
    )
    no_backup = [a.target.relpath for a in actions if a.no_backup]
    payload: dict[str, Any] = {
        "installed": clip(result.written),
        "unchanged": sum(1 for a in actions if a.kind == installer.CURRENT),
        "files": len(final),
        "backup": layout.BACKUP_DIR,
        "backed_up": len(result.backed_up),
        "rollback": "babeldub uninstall",
    }
    if stale_done:
        payload["restored_stale"] = clip(stale_done)
    attention: list[str] = []
    if stale_stuck:
        attention.append(
            f"could not restore {len(stale_stuck)} file(s) dropped from the build: {sorted(stale_stuck)[:3]}"
        )
    if no_backup:
        attention.append(f"no valid backup for {len(no_backup)} installed file(s): {no_backup[:3]}")
    if attention:
        payload["attention"] = attention
    if warnings:
        payload["warnings"] = warnings
    return emit(payload, EXIT_ATTENTION if attention else EXIT_OK)


# ---- uninstall --------------------------------------------------------------------------
def cmd_uninstall(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    game_dir = builder.require_game_dir(project)
    state = installer.load_state(project.root)
    entries = list(installer.active_entries(state).values())
    if not entries:
        return emit({"restored": [], "state": state.get("state", "none"), "note": "nothing is installed"})
    warnings = _check_guards(project, game_dir, args.force, anticheat=False)
    inst = Installer(project.root, game_dir)
    actions = inst.plan_restore(entries)
    if args.dry_run:
        return emit(
            {
                "dry_run": True,
                "plan": _counts([a.kind for a in actions]),
                "restore": clip(
                    [a.relpath for a in actions if a.kind in (installer.RESTORE, installer.REMOVE)]
                ),
                "warnings": warnings,
            }
        )
    inst.restore(actions, force=args.force)
    restored = [a.relpath for a in actions if a.outcome in ("restored", "removed")]
    failed = [a for a in actions if a.outcome == "failed"]
    changed = [a for a in actions if a.outcome == "left_changed"]
    remaining = [a.entry for a in failed]
    new_state = {
        "installed": remaining,
        "state": "installed" if remaining else "uninstalled",
        "build_id": state.get("build_id"),
        "restored": restored,
    }
    installer.save_state(project.root, new_state)
    project.mark_stage(
        "install", "attention", state=new_state["state"], restored=len(restored), remaining=len(remaining)
    )
    payload: dict[str, Any] = {
        "restored": clip(restored),
        "already_original": sum(1 for a in actions if a.outcome in ("kept_original", "gone")),
        "state": new_state["state"],
    }
    hints: list[str] = []
    if changed:
        payload["left_changed"] = clip([a.relpath for a in changed])
        hints.append(
            "files that differ from both the original and the dub were left as they are (game update?); "
            "--force restores the backup over them"
        )
    if failed:
        payload["failed"] = clip([{"relpath": a.relpath, "error": a.error} for a in failed], 5)
        hints.append(
            "close the game and retry; if a backup is broken, restore originals with the launcher's integrity check"
        )
    if hints:
        payload["hint"] = " ".join(hints)
    if warnings:
        payload["warnings"] = warnings
    return emit(payload, EXIT_ATTENTION if (changed or failed) else EXIT_OK)


# ---- reapply ----------------------------------------------------------------------------
def cmd_reapply(args: argparse.Namespace) -> int:
    project, game_dir, manifest, inst = _load_for_write(args)
    state = installer.load_state(project.root)
    prev = installer.active_entries(state)
    if not prev:
        raise BabelDubError(
            "nothing to reapply: the dub is not installed",
            code=EXIT_USAGE,
            hint="run `babeldub install`",
        )
    warnings = _check_guards(project, game_dir, args.force, anticheat=True)
    targets = builder.manifest_targets(project, manifest)
    _check_built(targets)
    by_asset: dict[str, list[FileTarget]] = {}
    for target in targets:
        by_asset.setdefault(target.asset_id, []).append(target)
    to_apply: list[FileAction] = []
    current: list[str] = []
    reapplied_assets: list[str] = []
    changed: list[dict[str, Any]] = []
    for asset_id, group in by_asset.items():
        acts = inst.plan(group, prev)
        bad = [a for a in acts if a.blocked]
        if bad:  # the game shipped a new version of this container: it must be rebuilt
            changed.append({"asset_id": asset_id, "files": clip([a.target.relpath for a in bad], 3)})
            continue
        to_apply.extend(acts)
        if all(a.kind == installer.CURRENT for a in acts):
            current.append(asset_id)
        else:
            reapplied_assets.append(asset_id)
    if args.dry_run:
        return emit(
            {
                "dry_run": True,
                "reapply": clip(reapplied_assets),
                "current": len(current),
                "changed": clip(changed, 5),
                "warnings": warnings,
            }
        )
    written: list[str] = []
    build_id = manifest.get("build_id")
    if to_apply:
        inst.check_space(to_apply)
        planned = {a.target.relpath for a in to_apply}
        keep = [e for rel, e in prev.items() if rel not in planned]
        result, entries = _apply_journaled(project, inst, to_apply, prev, keep, build_id)
        written = result.written
        installer.save_state(project.root, {"installed": entries, "state": "installed", "build_id": build_id})
    project.mark_stage(
        "install",
        "attention" if changed else "done",
        state="installed",
        reapplied=len(reapplied_assets),
        changed=len(changed),
    )
    payload: dict[str, Any] = {
        "reapplied": clip(written),
        "containers_reapplied": len(reapplied_assets),
        "current": len(current),
        "changed": clip(changed, 5),
    }
    if changed:
        only = ",".join(c["asset_id"] for c in changed[:5])
        payload["hint"] = (
            "the game updated these containers, the dub for them must be rebuilt from the new originals: "
            f"`babeldub build --only {only}`, `babeldub verify`, `babeldub install`"
        )
    if warnings:
        payload["warnings"] = warnings
    return emit(payload, EXIT_ATTENTION if changed else EXIT_OK)
