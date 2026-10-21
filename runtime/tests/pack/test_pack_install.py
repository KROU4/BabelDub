"""babeldub install / uninstall / reapply against a synthetic game folder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from babeldub.core import layout
from babeldub.core.ids import line_id, uid
from babeldub.core.jsonl import append_jsonl, write_json
from babeldub.core.output import EXIT_ATTENTION, EXIT_ERROR, EXIT_OK, EXIT_USAGE
from babeldub.pack import guards, installer

from .conftest import BANK_REL, CATALOG_REL, VOICE_DIR, Env, make_env, sha1_of

A_REL, B_REL, C_REL = (f"{VOICE_DIR}/{n}" for n in ("a.wav", "b.wav", "c.wav"))
GAME_FILES = (BANK_REL, A_REL, B_REL)


def originals(env: Env) -> dict[str, str | None]:
    return {rel: env.game_sha(rel) for rel in GAME_FILES}


def built_sha(env: Env) -> dict[str, str]:
    out: dict[str, str] = {}
    for cont in env.manifest()["containers"]:
        for item in cont["files"]:
            out[item["relpath"]] = item["out_sha1"]
    return out


def test_install_requires_verify(env: Env) -> None:
    env.run("build")
    code, out = env.run("install")
    assert code == EXIT_USAGE
    assert "verify" in out["error"]
    env.run("verify")
    env.redub(env.bank_lines[0], 999.0)
    env.run("build")  # a new build invalidates the earlier verify
    code, out = env.run("install")
    assert code == EXIT_USAGE


def test_install_uninstall_roundtrip(env: Env) -> None:
    orig = originals(env)
    env.ready()
    code, out = env.run("install")
    assert code == EXIT_OK, out
    assert sorted(out["installed"]) == sorted([BANK_REL, A_REL, B_REL, C_REL])
    assert out["backed_up"] == 3 and out["backup"] == layout.BACKUP_DIR

    new = built_sha(env)
    for rel in (BANK_REL, A_REL, B_REL, C_REL):
        assert env.game_sha(rel) == new[rel]
    for rel in GAME_FILES:  # originals are in the backup, verified by sha1
        assert sha1_of(env.root / layout.BACKUP_DIR / rel) == orig[rel]
    assert not (env.root / layout.BACKUP_DIR / C_REL).exists()
    assert not list(env.game.rglob("*.babeldub-tmp"))

    state = env.install_state()
    assert state["state"] == "installed" and state["build_id"] == env.manifest()["build_id"]
    rows = {e["relpath"]: e for e in state["installed"]}
    assert rows[BANK_REL] == {
        "relpath": BANK_REL,
        "asset_id": f"fakebank:{BANK_REL}",
        "orig_sha1": orig[BANK_REL],
        "new_sha1": new[BANK_REL],
        "backup": f"{layout.BACKUP_DIR}/{BANK_REL}",
        "created": False,
    }
    assert rows[C_REL]["created"] is True and rows[C_REL]["backup"] is None
    assert env.progress()["install"]["status"] == "done"

    # second install is a no-op
    code, again = env.run("install")
    assert code == EXIT_OK
    assert again["installed"] == [] and again["unchanged"] == 4 and again["backed_up"] == 0

    code, out = env.run("uninstall")
    assert code == EXIT_OK, out
    assert sorted(out["restored"]) == sorted([BANK_REL, A_REL, B_REL, C_REL])
    assert originals(env) == orig
    assert not (env.game / C_REL).exists()
    assert env.install_state()["state"] == "uninstalled"

    code, out = env.run("uninstall")
    assert code == EXIT_OK and out["note"] == "nothing is installed"
    code, out = env.run("reapply")
    assert code == EXIT_USAGE


def test_install_dry_run_touches_nothing(env: Env) -> None:
    orig = originals(env)
    env.ready()
    code, out = env.run("install", "--dry-run")
    assert code == EXIT_OK
    assert out["plan"] == {"install": 3, "create": 1}
    assert originals(env) == orig
    assert not (env.root / layout.BACKUP_DIR).exists()
    assert not (env.root / layout.INSTALL_LOG).exists()


def test_reapply_after_integrity_check(env: Env) -> None:
    orig = originals(env)
    env.ready()
    env.run("install")
    # the launcher's "verify integrity" puts the originals back (and deletes nothing extra)
    for rel in GAME_FILES:
        (env.game / rel).write_bytes((env.root / layout.BACKUP_DIR / rel).read_bytes())
    assert originals(env) == orig

    code, out = env.run("reapply")
    assert code == EXIT_OK, out
    assert sorted(out["reapplied"]) == sorted(GAME_FILES)
    new = built_sha(env)
    assert all(env.game_sha(rel) == new[rel] for rel in new)

    code, out = env.run("reapply")
    assert code == EXIT_OK
    assert out["reapplied"] == [] and out["current"] == 2


def test_reapply_after_game_update_needs_rebuild(env: Env) -> None:
    env.ready()
    env.run("install")
    # a game update ships a new version of the bank (one more line)
    updated = {"format": "fakebank", "entries": [{"index": i, "data": f"v2-{i}"} for i in range(5)]}
    (env.game / BANK_REL).write_text(json.dumps(updated), encoding="utf-8")
    updated_sha = env.game_sha(BANK_REL)

    code, out = env.run("reapply")
    assert code == EXIT_ATTENTION, out
    assert [c["asset_id"] for c in out["changed"]] == [f"fakebank:{BANK_REL}"]
    assert f"build --only fakebank:{BANK_REL}" in out["hint"]
    assert env.game_sha(BANK_REL) == updated_sha  # an updated file is never overwritten by an old build

    # the suggested path: rebuild the changed container from the new original, verify, install
    code, out = env.run("build", "--only", f"fakebank:{BANK_REL}")
    assert code == EXIT_OK, out
    assert out["containers"][0]["status"] == "built"
    # a full build also works: the installed loose files are recognised and stay up to date
    code, out = env.run("build")
    assert code == EXIT_OK, out
    assert {c["asset_id"]: c["status"] for c in out["containers"]} == {
        f"fakebank:{BANK_REL}": "up_to_date",
        f"fakedir:{VOICE_DIR}": "up_to_date",
    }
    bank = next(c for c in env.manifest()["containers"] if c["adapter"] == "fakebank")
    assert bank["src_sha1"] == updated_sha
    code, out = env.run("verify")
    assert code == EXIT_OK, out
    code, out = env.run("install")
    assert code == EXIT_OK, out
    assert out["installed"] == [BANK_REL]
    # the backup now holds the updated original, so uninstall returns v2, not v1
    assert sha1_of(env.root / layout.BACKUP_DIR / BANK_REL) == updated_sha
    env.run("uninstall")
    assert env.game_sha(BANK_REL) == updated_sha


def test_build_after_install_is_up_to_date_but_refuses_rebuild(env: Env) -> None:
    env.ready()
    env.run("install")
    code, out = env.run("build")
    assert code == EXIT_OK, out
    assert {c["status"] for c in out["containers"]} == {"up_to_date"}

    env.redub(env.bank_lines[0], 1234.0)
    code, out = env.run("build")
    assert code == EXIT_ATTENTION
    assert out["failed"][0]["asset_id"] == f"fakebank:{BANK_REL}"
    assert "uninstall" in out["failed"][0]["hint"]


def test_install_refuses_when_game_running(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    orig = originals(env)
    env.ready()
    proc = {"pid": 4242, "name": "Game.exe", "exe": str(env.game / "Game.exe")}
    monkeypatch.setattr(
        guards, "check_running", lambda game_dir: {"processes": [proc], "method": "mock", "error": None}
    )
    code, out = env.run("install")
    assert code == EXIT_USAGE
    assert out["error"] == "the game is running"
    assert out["processes"][0]["pid"] == 4242
    assert originals(env) == orig and not (env.root / layout.BACKUP_DIR).exists()

    code, out = env.run("install", "--force")
    assert code == EXIT_OK
    assert "running" in out["warnings"][0]


def test_uninstall_refuses_when_game_running(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.ready()
    env.run("install")
    monkeypatch.setattr(
        guards, "check_running", lambda game_dir: {"processes": [{"pid": 1}], "method": "mock", "error": None}
    )
    code, out = env.run("uninstall")
    assert code == EXIT_USAGE
    assert env.install_state()["state"] == "installed"


@pytest.mark.parametrize(
    "recon",
    [
        {"anticheat": ["EasyAntiCheat"]},
        {"anticheat": True},
        {"middleware": [{"name": "BattlEye"}]},
        {"warnings": ["EasyAntiCheat files found in the game folder"]},
    ],
)
def test_install_refuses_anticheat_from_recon(env: Env, recon: dict[str, object]) -> None:
    orig = originals(env)
    env.ready()
    write_json(env.root / layout.RECON, recon)
    code, out = env.run("install")
    assert code == EXIT_USAGE
    assert out["error"].startswith("anti-cheat detected")
    assert originals(env) == orig

    code, out = env.run("install", "--force")
    assert code == EXIT_OK
    assert any("anti-cheat" in w for w in out["warnings"])


def test_install_refuses_anticheat_found_in_folder(env: Env) -> None:
    env.ready()
    (env.game / "EasyAntiCheat").mkdir()
    (env.game / "EasyAntiCheat" / "Settings.json").write_text("{}", encoding="utf-8")
    code, out = env.run("install")
    assert code == EXIT_USAGE
    assert out["anticheat"] == [{"name": "EasyAntiCheat", "source": "EasyAntiCheat"}]


def test_install_refuses_modified_game_file(env: Env) -> None:
    env.ready()
    (env.game / A_REL).write_bytes(b"patched by another mod")
    code, out = env.run("install")
    assert code == EXIT_USAGE
    assert out["changed"][0]["relpath"] == A_REL
    assert not (env.root / layout.BACKUP_DIR).exists()
    assert env.game_sha(BANK_REL) != built_sha(env)[BANK_REL]


def test_install_refuses_tampered_build(env: Env) -> None:
    env.ready()
    (env.root / layout.BUILD_DIR / BANK_REL).write_text("{}", encoding="utf-8")
    code, out = env.run("install")
    assert code == EXIT_USAGE
    assert out["files"] == [BANK_REL]


def test_install_rolls_back_on_failure(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    orig = originals(env)
    env.ready()
    real_copy = installer.atomic_copy
    writes: list[Path] = []

    def flaky(src: Path, dst: Path, keep_times: bool = False) -> str:
        if dst.is_relative_to(env.game.resolve()) and not keep_times:
            writes.append(dst)
            if len(writes) == 2:
                raise PermissionError(13, "file is locked by another process")
        return real_copy(src, dst, keep_times=keep_times)

    monkeypatch.setattr(installer, "atomic_copy", flaky)
    code, out = env.run("install")
    assert code == EXIT_ERROR
    assert "locked" in out["error"]
    assert len(out["rolled_back"]) == 1 and out["stuck"] == []
    assert originals(env) == orig
    assert not (env.game / C_REL).exists()
    assert env.install_state()["state"] == "uninstalled"

    monkeypatch.setattr(installer, "atomic_copy", real_copy)
    code, out = env.run("install")
    assert code == EXIT_OK, out


def test_install_restores_files_dropped_from_build(env: Env) -> None:
    orig = originals(env)
    env.ready()
    env.run("install")
    env.decide(env.dir_lines, decision="keep_original")
    env.ready()
    code, out = env.run("install")
    assert code == EXIT_OK, out
    assert sorted(out["restored_stale"]) == sorted([A_REL, B_REL, C_REL])
    assert env.game_sha(A_REL) == orig[A_REL] and env.game_sha(B_REL) == orig[B_REL]
    assert not (env.game / C_REL).exists()
    assert [e["relpath"] for e in env.install_state()["installed"]] == [BANK_REL]


def test_rejecting_everything_returns_originals(env: Env) -> None:
    orig = originals(env)
    env.ready()
    env.run("install")
    env.decide(env.bank_lines + env.dir_lines, decision="regen")
    code, out = env.run("build")
    assert code == EXIT_ATTENTION
    assert out["dropped"] == 2
    assert env.manifest()["containers"] == []
    assert not (env.root / layout.BUILD_DIR / BANK_REL).exists()
    code, out = env.run("verify")
    assert code == EXIT_OK, out
    code, out = env.run("install")
    assert code == EXIT_OK, out
    assert sorted(out["restored_stale"]) == sorted([BANK_REL, A_REL, B_REL, C_REL])
    assert originals(env) == orig and not (env.game / C_REL).exists()
    state = env.install_state()
    assert state["installed"] == [] and state["state"] == "uninstalled"


def test_uninstall_leaves_files_changed_by_update(env: Env) -> None:
    env.ready()
    env.run("install")
    (env.game / BANK_REL).write_text('{"format": "fakebank", "entries": []}', encoding="utf-8")
    updated = env.game_sha(BANK_REL)
    code, out = env.run("uninstall")
    assert code == EXIT_ATTENTION
    assert out["left_changed"] == [BANK_REL]
    assert env.game_sha(BANK_REL) == updated
    assert env.install_state()["state"] == "uninstalled"


def test_uninstall_force_restores_changed_file(env: Env) -> None:
    orig = originals(env)
    env.ready()
    env.run("install")
    (env.game / BANK_REL).write_text("garbage", encoding="utf-8")
    code, out = env.run("uninstall", "--force")
    assert code == EXIT_OK, out
    assert originals(env) == orig


def test_uninstall_keeps_entry_when_backup_is_broken(env: Env) -> None:
    env.ready()
    env.run("install")
    (env.root / layout.BACKUP_DIR / BANK_REL).write_text("corrupted", encoding="utf-8")
    code, out = env.run("uninstall")
    assert code == EXIT_ATTENTION
    assert out["failed"][0]["relpath"] == BANK_REL
    state = env.install_state()
    assert state["state"] == "installed"
    assert [e["relpath"] for e in state["installed"]] == [BANK_REL]
    assert env.game_sha(BANK_REL) == built_sha(env)[BANK_REL]


def test_install_with_shared_extra_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = make_env(tmp_path, monkeypatch, capsys, catalog=True)
    env.decide(env.bank_lines)
    orig_catalog = env.game_sha(CATALOG_REL)
    env.ready()
    code, out = env.run("install")
    assert code == EXIT_OK, out
    assert sorted(out["installed"]) == sorted([BANK_REL, CATALOG_REL])
    assert json.loads((env.game / CATALOG_REL).read_text(encoding="utf-8"))["crc"][BANK_REL] == 0
    env.run("uninstall")
    assert env.game_sha(CATALOG_REL) == orig_catalog


def test_asset_at_game_root(env: Env) -> None:
    """A loose-audio asset can be the game folder itself (relpath ".")."""
    (env.game / "intro.wav").write_bytes(b"RIFF-original-intro")
    orig = env.game_sha("intro.wav")
    root_asset = {
        "asset_id": "fakedir:.",
        "adapter": "fakedir",
        "relpath": ".",
        "role": "voice",
        "level": "A",
    }
    append_jsonl(env.root / layout.ASSETS, [root_asset])
    lid = line_id("fakedir", ".", "intro.wav", 0)
    append_jsonl(env.root / layout.LINES, [{"id": lid, "uid": uid(lid), "asset_id": "fakedir:."}])
    append_jsonl(env.root / layout.POST, [{"id": lid, "attempt": 1}])
    env.redub(lid, 640.0)
    env.decide([lid])
    env.ready()
    root = next(c for c in env.manifest()["containers"] if c["asset_id"] == "fakedir:.")
    assert root["relpath"] == "" and root["out"] == layout.BUILD_DIR
    assert [f["relpath"] for f in root["files"]] == ["intro.wav"]
    assert root["files"][0]["src_sha1"] == orig

    code, out = env.run("install")
    assert code == EXIT_OK, out
    assert "intro.wav" in out["installed"]
    assert env.game_sha("intro.wav") == root["files"][0]["out_sha1"]
    env.run("uninstall")
    assert env.game_sha("intro.wav") == orig


def test_manifest_with_escaping_path_is_rejected(env: Env) -> None:
    env.ready()
    manifest = env.manifest()
    manifest["containers"][0]["files"][0]["relpath"] = "../../evil.dll"
    write_json(env.root / layout.BUILD_MANIFEST, manifest)
    prog = json.loads((env.root / layout.PROGRESS).read_text(encoding="utf-8"))
    assert prog["verify"]["build_id"] == manifest["build_id"]
    code, out = env.run("install")
    assert code == EXIT_USAGE
    assert "unsafe path" in out["error"]
    assert not (env.game.parent.parent / "evil.dll").exists()
