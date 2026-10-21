"""Low-level installer helpers: path safety, atomic copy, plan classification."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from babeldub.core.output import BabelDubError
from babeldub.pack import installer
from babeldub.pack.installer import FileTarget, Installer


@pytest.mark.parametrize(
    "bad", ["", "/etc/passwd", "../x", "a/../../b", "C:/Windows/x.dll", "a/b:stream", "..", "."]
)
def test_clean_relpath_rejects(bad: str) -> None:
    with pytest.raises(BabelDubError):
        installer.clean_relpath(bad)


def test_clean_relpath_normalizes() -> None:
    assert installer.clean_relpath("Data\\Sub\\file.bank") == "Data/Sub/file.bank"
    assert installer.clean_relpath("./Data//x.bank") == "Data/x.bank"


def test_safe_join_stays_inside(tmp_path: Path) -> None:
    root = tmp_path / "game"
    root.mkdir()
    assert installer.safe_join(root, "a/b.txt") == (root / "a" / "b.txt").resolve()
    with pytest.raises(BabelDubError):
        installer.safe_join(root, "../outside.txt")


def test_atomic_copy_replaces_read_only_and_keeps_times(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"new data" * 1000)
    os.utime(src, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    dst = tmp_path / "game" / "file.bin"
    dst.parent.mkdir()
    dst.write_bytes(b"old")
    os.chmod(dst, stat.S_IREAD)
    digest = installer.atomic_copy(src, dst, keep_times=True)
    assert digest == hashlib.sha1(src.read_bytes()).hexdigest()
    assert dst.read_bytes() == src.read_bytes()
    assert dst.stat().st_mtime_ns == src.stat().st_mtime_ns
    assert [p.name for p in dst.parent.iterdir()] == ["file.bin"]  # no temp files left


def test_atomic_copy_failure_leaves_target_intact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"new")
    dst = tmp_path / "dst.bin"
    dst.write_bytes(b"old")

    def boom(a: object, b: object) -> None:
        raise PermissionError(13, "locked")

    monkeypatch.setattr(installer.os, "replace", boom)
    with pytest.raises(PermissionError):
        installer.atomic_copy(src, dst)
    assert dst.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dst.bin", "src.bin"]


def _sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def test_plan_classification(tmp_path: Path) -> None:
    project, game = tmp_path / "proj", tmp_path / "game"
    (project / "build" / "out").mkdir(parents=True)
    game.mkdir()
    built = project / "build" / "out" / "f.bin"
    built.write_bytes(b"dub")
    inst = Installer(project, game)

    def target(src: bytes | None) -> FileTarget:
        return FileTarget("f.bin", "x:f.bin", _sha(src) if src is not None else None, _sha(b"dub"), built)

    (game / "f.bin").write_bytes(b"orig")
    (act,) = inst.plan([target(b"orig")], {})
    assert (act.kind, act.write_backup, act.orig_sha1) == (installer.INSTALL, True, _sha(b"orig"))

    (game / "f.bin").write_bytes(b"dub")
    (act,) = inst.plan([target(b"orig")], {})
    assert act.kind == installer.CURRENT and act.no_backup  # ours, but nothing in build/backup

    (game / "f.bin").write_bytes(b"older dub")
    backup = project / "build" / "backup" / "f.bin"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"orig")
    prev = {"f.bin": {"relpath": "f.bin", "orig_sha1": _sha(b"orig"), "new_sha1": _sha(b"older dub")}}
    (act,) = inst.plan([target(b"orig")], prev)
    assert (act.kind, act.write_backup) == (installer.REPLACE_OURS, False)

    (game / "f.bin").write_bytes(b"game v2")
    (act,) = inst.plan([target(b"orig")], prev)
    assert act.kind == installer.CHANGED and act.blocked

    (game / "f.bin").unlink()
    (act,) = inst.plan([target(None)], {})
    assert act.kind == installer.CREATE and act.created
    (game / "f.bin").write_bytes(b"someone else's file")
    (act,) = inst.plan([target(None)], {})
    assert act.kind == installer.CONFLICT
