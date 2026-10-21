"""Backup, atomic replacement and rollback of game files.

The game folder stays read-only for every stage except install / uninstall / reapply.
Rules this module enforces:
  * every path inside the game comes from a manifest as a relative POSIX path and must
    resolve inside the game folder (no "..", no drive letters, no absolute paths);
  * the original is copied to build/backup/<relpath> and checked by sha1 before the game
    file is touched;
  * a game file is replaced through a temp file in the same folder plus os.replace, so a
    crash never leaves a half-written file;
  * a failed install rolls back the files it already replaced.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from babeldub.core import layout
from babeldub.core.jsonl import now_iso, read_json, write_json
from babeldub.core.output import EXIT_ENV, EXIT_ERROR, EXIT_USAGE, BabelDubError

CHUNK = 1 << 20
TMP_SUFFIX = ".babeldub-tmp"
SPACE_MARGIN = 64 << 20  # keep some free space after our writes

# install actions
CURRENT = "current"  # game file already holds the built file
INSTALL = "install"  # game file is the original the build was made from
REPLACE_OURS = "replace_ours"  # game file holds an older BabelDub build of the same original
CREATE = "create"  # the build adds a file the game does not have
CHANGED = "changed"  # game file is neither the original nor ours (game updated / other mod)
CONFLICT = "conflict"  # a file appeared where the build expected none, or a folder is in the way
BLOCKING = frozenset({CHANGED, CONFLICT})
WRITING = frozenset({INSTALL, REPLACE_OURS, CREATE})

# restore actions
RESTORE = "restore"
REMOVE = "remove"
ORIGINAL = "original"  # already the original (e.g. after Steam "verify integrity")
ABSENT = "absent"  # a file we created is already gone
BAD_BACKUP = "bad_backup"
NO_BACKUP = "no_backup"

ACTIVE_STATES = ("installed", "installing")


# ---- low-level helpers ------------------------------------------------------------------
def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def file_sha1(path: Path) -> str | None:
    """sha1 of a regular file, None when there is no file at that path."""
    return sha1_file(path) if path.is_file() else None


def clean_relpath(relpath: str) -> str:
    """Normalize a game-relative path to POSIX form and reject anything that can escape the root."""
    text = str(relpath).replace("\\", "/").strip()
    pure = PurePosixPath(text)
    parts = [p for p in pure.parts if p not in ("", ".")]
    if not text or text.startswith("/") or not parts or ".." in parts or any(":" in p for p in parts):
        raise BabelDubError(
            f"unsafe path in manifest: {relpath!r}",
            code=EXIT_USAGE,
            hint="paths inside the game must be relative, without '..' or drive letters; rebuild with `babeldub build`",
        )
    return "/".join(parts)


def safe_join(root: Path, relpath: str) -> Path:
    """root/relpath, guaranteed (after resolving symlinks) to stay strictly inside root."""
    rel = clean_relpath(relpath)
    base = root.resolve()
    target = (base / rel).resolve()
    if target == base or not target.is_relative_to(base):
        raise BabelDubError(
            f"path escapes its folder: {relpath!r}",
            code=EXIT_USAGE,
            hint="a link inside the game folder points outside of it; BabelDub will not write there",
        )
    return target


def _make_writable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return
    if not mode & stat.S_IWRITE:
        os.chmod(path, mode | stat.S_IWRITE)


def atomic_copy(src: Path, dst: Path, keep_times: bool = False) -> str:
    """Copy src over dst through a temp file in dst's folder + os.replace. Returns sha1 of the data."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=TMP_SUFFIX, dir=dst.parent)
    tmp = Path(tmp_name)
    digest = hashlib.sha1()
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as inp:
            while True:
                block = inp.read(CHUNK)
                if not block:
                    break
                digest.update(block)
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if keep_times:
            st = src.stat()
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        _make_writable(dst)
        os.replace(tmp, dst)
    except BaseException:
        if tmp.exists():
            _make_writable(tmp)
            tmp.unlink()
        raise
    return digest.hexdigest()


def remove_file(path: Path) -> None:
    _make_writable(path)
    path.unlink()


def _existing_anchor(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


# ---- install state (build/install.json) ---------------------------------------------------
def load_state(project_root: Path) -> dict[str, Any]:
    data = read_json(project_root / layout.INSTALL_LOG, default=None) or {}
    data.setdefault("installed", [])
    data.setdefault("state", "none")
    return data


def save_state(project_root: Path, state: dict[str, Any]) -> None:
    state = {**state, "ts": now_iso()}
    write_json(project_root / layout.INSTALL_LOG, state)


def active_entries(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Entries of files that BabelDub put into the game folder and has not rolled back yet."""
    if state.get("state") not in ACTIVE_STATES:
        return {}
    return {e["relpath"]: e for e in state.get("installed", []) if isinstance(e, dict) and e.get("relpath")}


# ---- plans --------------------------------------------------------------------------------
@dataclass
class FileTarget:
    """One file of a built container that has to land in the game folder."""

    relpath: str
    asset_id: str
    src_sha1: str | None  # original the build was made from; None = the build adds a new file
    out_sha1: str
    built: Path


@dataclass
class FileAction:
    target: FileTarget
    game_path: Path
    backup_path: Path
    kind: str
    cur_sha1: str | None
    orig_sha1: str | None = None
    created: bool = False
    write_backup: bool = False
    no_backup: bool = False  # ours is installed but build/backup does not hold the original
    note: str = ""

    @property
    def blocked(self) -> bool:
        return self.kind in BLOCKING

    @property
    def writes(self) -> bool:
        return self.kind in WRITING

    def describe(self) -> dict[str, Any]:
        row: dict[str, Any] = {"relpath": self.target.relpath, "action": self.kind}
        if self.blocked:
            row.update(expected=self.target.src_sha1, actual=self.cur_sha1)
        if self.note:
            row["note"] = self.note
        return row


@dataclass
class RestoreAction:
    entry: dict[str, Any]
    game_path: Path
    backup: Path | None
    kind: str
    cur_sha1: str | None
    outcome: str = ""
    error: str = ""

    @property
    def relpath(self) -> str:
        return self.entry["relpath"]


@dataclass
class ApplyResult:
    written: list[str] = field(default_factory=list)
    backed_up: list[str] = field(default_factory=list)


class Installer:
    """Plans and performs file swaps between build/out, build/backup and the game folder."""

    def __init__(self, project_root: Path, game_dir: Path):
        self.project_root = project_root
        self.game_dir = game_dir
        self.backup_root = project_root / layout.BACKUP_DIR

    # ---- paths ----
    def game_path(self, relpath: str) -> Path:
        return safe_join(self.game_dir, relpath)

    def backup_path(self, relpath: str) -> Path:
        return safe_join(self.backup_root, relpath)

    def backup_ref(self, relpath: str) -> str:
        return f"{layout.BACKUP_DIR}/{clean_relpath(relpath)}"

    def resolve_backup(self, ref: str | None) -> Path | None:
        if not ref:
            return None
        path = safe_join(self.project_root, ref)
        if not path.is_relative_to(self.backup_root.resolve()):
            raise BabelDubError(f"backup path outside build/backup: {ref!r}", code=EXIT_USAGE)
        return path

    # ---- install ----
    def plan(self, targets: Iterable[FileTarget], prev: dict[str, dict[str, Any]]) -> list[FileAction]:
        actions: list[FileAction] = []
        for target in targets:
            actions.append(self._classify(target, prev.get(target.relpath)))
        return actions

    def _classify(self, target: FileTarget, prev: dict[str, Any] | None) -> FileAction:
        game_path = self.game_path(target.relpath)
        backup_path = self.backup_path(target.relpath)
        if game_path.is_dir():
            return FileAction(target, game_path, backup_path, CONFLICT, None, note="a folder is in the way")
        cur = file_sha1(game_path)
        backup_cache: list[str | None] = []

        def backup_sha1() -> str | None:
            if not backup_cache:
                backup_cache.append(file_sha1(backup_path))
            return backup_cache[0]

        src, out = target.src_sha1, target.out_sha1
        prev_new = prev.get("new_sha1") if prev else None
        prev_created = bool(prev and prev.get("created"))

        def action(kind: str, **kw: Any) -> FileAction:
            return FileAction(target, game_path, backup_path, kind, cur, **kw)

        if cur is not None and cur == out:
            if prev and prev_new == out:
                lost = not prev_created and not prev.get("backup")
                return action(CURRENT, orig_sha1=prev.get("orig_sha1"), created=prev_created, no_backup=lost)
            if src is None:
                return action(CURRENT, created=True)
            if backup_sha1() == src:
                return action(CURRENT, orig_sha1=src)
            return action(CURRENT, orig_sha1=src, no_backup=True, note="installed without a valid backup")
        if src is None:
            if cur is None:
                return action(CREATE, created=True)
            if prev_created and cur == prev_new:
                return action(REPLACE_OURS, created=True)
            return action(CONFLICT, note="the game now has a file the build did not expect")
        if cur == src:
            return action(INSTALL, orig_sha1=src, write_backup=backup_sha1() != src)
        if cur is not None and prev and cur == prev_new and backup_sha1() == src:
            return action(REPLACE_OURS, orig_sha1=src)
        note = (
            "file is missing in the game folder" if cur is None else "differs from the original of this build"
        )
        return action(CHANGED, note=note)

    def entries_for(self, actions: Iterable[FileAction]) -> list[dict[str, Any]]:
        """install.json rows for every non-blocked action (what the game folder holds afterwards)."""
        rows: list[dict[str, Any]] = []
        for act in actions:
            if act.blocked:
                continue
            has_backup = not act.created and not act.no_backup
            rows.append(
                {
                    "relpath": act.target.relpath,
                    "asset_id": act.target.asset_id,
                    "orig_sha1": act.orig_sha1,
                    "new_sha1": act.target.out_sha1,
                    "backup": self.backup_ref(act.target.relpath) if has_backup else None,
                    "created": act.created,
                }
            )
        return rows

    def check_space(self, actions: Iterable[FileAction]) -> None:
        """Fail early (nothing written) when backups or temp copies would not fit on disk."""
        acts = list(actions)
        need: dict[int, int] = {}
        free: dict[int, int] = {}

        def add(path: Path, size: int) -> None:
            anchor = _existing_anchor(path)
            dev = anchor.stat().st_dev
            need[dev] = need.get(dev, 0) + size
            if dev not in free:
                free[dev] = shutil.disk_usage(anchor).free

        backups = sum(a.game_path.stat().st_size for a in acts if a.write_backup)
        if backups:
            add(self.backup_root, backups)
        writes = [a.target.built.stat().st_size for a in acts if a.writes]
        if writes:
            add(self.game_dir, max(writes))  # one temp copy at a time next to its target
        for dev, size in need.items():
            if size + SPACE_MARGIN > free[dev]:
                raise BabelDubError(
                    "not enough free disk space for backups and the atomic swap",
                    code=EXIT_ENV,
                    need_mb=round((size + SPACE_MARGIN) / 2**20),
                    free_mb=round(free[dev] / 2**20),
                    hint="free some space (BABELDUB_HOME and the game drive) and retry",
                )

    def apply(self, actions: list[FileAction]) -> ApplyResult:
        """Back up originals, then swap game files. On failure restore what was swapped and raise.

        The raised BabelDubError carries `stuck` (relpaths that could not be rolled back) so the
        caller can keep them in install.json for a later uninstall.
        """
        blocked = [a for a in actions if a.blocked]
        if blocked:
            raise BabelDubError("refusing to apply a plan with blocked files", code=EXIT_USAGE)
        result = ApplyResult()
        for act in actions:
            if not act.write_backup:
                continue
            got = atomic_copy(act.game_path, act.backup_path, keep_times=True)
            if got != act.orig_sha1:
                raise BabelDubError(
                    f"backup of {act.target.relpath} does not match the original (sha1 {got})",
                    code=EXIT_ERROR,
                    hint="the game file changed while copying; close the game and retry",
                )
            result.backed_up.append(act.target.relpath)
        done: list[FileAction] = []
        current = ""
        try:
            for act in actions:
                if not act.writes:
                    continue
                current = act.target.relpath
                got = atomic_copy(act.target.built, act.game_path)
                done.append(act)
                if got != act.target.out_sha1:
                    raise OSError(f"built file changed while copying (sha1 {got})")
                result.written.append(act.target.relpath)
        except (Exception, KeyboardInterrupt) as exc:  # roll back on any failure, including Ctrl+C
            rolled, stuck = self._rollback(done)
            raise BabelDubError(
                f"install failed at {current}: {exc}",
                code=EXIT_ERROR,
                rolled_back=rolled,
                stuck=stuck,
                hint="close the game (Windows locks open files) and run `babeldub install` again",
            ) from exc
        return result

    def _rollback(self, done: list[FileAction]) -> tuple[list[str], list[str]]:
        rolled: list[str] = []
        stuck: list[str] = []
        for act in reversed(done):
            try:
                if act.created:
                    if act.game_path.exists():
                        remove_file(act.game_path)
                else:
                    atomic_copy(act.backup_path, act.game_path, keep_times=True)
                rolled.append(act.target.relpath)
            except OSError:
                stuck.append(act.target.relpath)
        return rolled, stuck

    # ---- uninstall ----
    def plan_restore(self, entries: Iterable[dict[str, Any]]) -> list[RestoreAction]:
        actions: list[RestoreAction] = []
        for entry in entries:
            actions.append(self._classify_restore(entry))
        return actions

    def _classify_restore(self, entry: dict[str, Any]) -> RestoreAction:
        game_path = self.game_path(entry["relpath"])
        cur = file_sha1(game_path)
        new, orig = entry.get("new_sha1"), entry.get("orig_sha1")
        if entry.get("created") or orig is None:
            if cur is None:
                return RestoreAction(entry, game_path, None, ABSENT, cur)
            return RestoreAction(entry, game_path, None, REMOVE if cur == new else CHANGED, cur)
        if cur == orig:
            return RestoreAction(entry, game_path, None, ORIGINAL, cur)
        backup = self.resolve_backup(entry.get("backup"))
        if backup is not None and not backup.is_file():
            backup = None
        if cur != new:
            # neither ours nor the original: most likely a game update put a newer original here
            return RestoreAction(entry, game_path, backup, CHANGED, cur)
        if backup is None:
            return RestoreAction(entry, game_path, None, NO_BACKUP, cur)
        if sha1_file(backup) != orig:
            return RestoreAction(entry, game_path, backup, BAD_BACKUP, cur)
        return RestoreAction(entry, game_path, backup, RESTORE, cur)

    def restore(self, actions: list[RestoreAction], force: bool = False) -> None:
        """Execute restore actions in place, recording `outcome` (and `error`) on each one.

        outcome: restored | removed | kept_original | gone | left_changed | failed.
        With force, a CHANGED file is overwritten by the verified backup (or removed if we created it).
        """
        for act in actions:
            kind = act.kind
            if kind == CHANGED and force:
                if act.entry.get("created") or act.entry.get("orig_sha1") is None:
                    kind = REMOVE
                elif act.backup is not None and sha1_file(act.backup) == act.entry.get("orig_sha1"):
                    kind = RESTORE
                else:
                    kind = BAD_BACKUP
            try:
                if kind == RESTORE and act.backup is not None:
                    got = atomic_copy(act.backup, act.game_path, keep_times=True)
                    if got != act.entry.get("orig_sha1"):
                        raise OSError(f"restored data has sha1 {got}, expected {act.entry.get('orig_sha1')}")
                    act.outcome = "restored"
                elif kind == REMOVE:
                    if act.game_path.exists():
                        remove_file(act.game_path)
                    act.outcome = "removed"
                elif kind == ORIGINAL:
                    act.outcome = "kept_original"
                elif kind == ABSENT:
                    act.outcome = "gone"
                elif kind == CHANGED:
                    act.outcome = "left_changed"
                else:
                    act.outcome = "failed"
                    act.error = (
                        "backup is missing"
                        if kind == NO_BACKUP
                        else "backup sha1 does not match the original"
                    )
            except OSError as exc:
                act.outcome = "failed"
                act.error = f"{type(exc).__name__}: {exc}"
