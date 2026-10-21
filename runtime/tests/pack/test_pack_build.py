"""babeldub build / verify on a synthetic game with fake adapters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from babeldub.core import layout
from babeldub.core.ids import uid
from babeldub.core.jsonl import read_jsonl, write_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_OK, EXIT_USAGE

from .conftest import (
    BANK_REL,
    CATALOG_REL,
    VOICE_DIR,
    Env,
    FakeBankAdapter,
    FakeDirAdapter,
    make_env,
    sha1_of,
)


def test_build_file_and_folder_containers(env: Env) -> None:
    before = {rel: env.game_sha(rel) for rel in (BANK_REL, f"{VOICE_DIR}/a.wav", f"{VOICE_DIR}/b.wav")}
    code, out = env.run("build")
    assert code == EXIT_OK, out
    assert out["replaced"] == 6
    assert {c["status"] for c in out["containers"]} == {"built"}

    manifest = env.manifest()
    assert manifest["target_lang"] == "ru"
    assert len(manifest["game_fingerprint"]) == 40
    assert manifest["models"] == [
        {
            "name": "Qwen3-TTS-12Hz-1.7B-Base",
            "revision": None,
            "license": "Apache-2.0",
            "engine": "qwen3tts",
            "lines": 6,
        }
    ]
    by_id = {c["asset_id"]: c for c in manifest["containers"]}

    bank = by_id[f"fakebank:{BANK_REL}"]
    assert bank["src_sha1"] == before[BANK_REL]
    assert bank["out"] == f"{layout.BUILD_DIR}/{BANK_REL}"
    built_bank = env.root / bank["out"]
    assert sha1_of(built_bank) == bank["out_sha1"]
    assert (bank["replaced"], bank["kept"], bank["codec"]) == (3, 1, "pcm16")
    data = json.loads(built_bank.read_text(encoding="utf-8"))
    assert [e["data"].startswith("dub:") for e in data["entries"]] == [True, True, True, False]

    folder = by_id[f"fakedir:{VOICE_DIR}"]
    assert folder["out"] == f"{layout.BUILD_DIR}/{VOICE_DIR}"
    assert (env.root / folder["out"]).is_dir()
    files = {f["relpath"]: f for f in folder["files"]}
    assert set(files) == {f"{VOICE_DIR}/a.wav", f"{VOICE_DIR}/b.wav", f"{VOICE_DIR}/c.wav"}
    assert files[f"{VOICE_DIR}/a.wav"]["src_sha1"] == before[f"{VOICE_DIR}/a.wav"]
    assert files[f"{VOICE_DIR}/c.wav"]["src_sha1"] is None  # a new file the game does not have
    for item in files.values():
        assert sha1_of(env.root / item["out"]) == item["out_sha1"]

    # the game folder is read-only for build
    assert {rel: env.game_sha(rel) for rel in before} == before
    assert not (env.game / VOICE_DIR / "c.wav").exists()
    assert not (env.root / "build" / "staging").exists()
    assert env.progress()["build"]["status"] == "done"


def test_build_is_idempotent(env: Env) -> None:
    code, first = env.run("build")
    assert code == EXIT_OK
    calls = (len(FakeBankAdapter.calls), len(FakeDirAdapter.calls))
    code, second = env.run("build")
    assert code == EXIT_OK
    assert {c["status"] for c in second["containers"]} == {"up_to_date"}
    assert (len(FakeBankAdapter.calls), len(FakeDirAdapter.calls)) == calls
    assert second["build_id"] == first["build_id"]

    code, forced = env.run("build", "--force")
    assert code == EXIT_OK
    assert {c["status"] for c in forced["containers"]} == {"built"}


def test_build_rebuilds_container_whose_audio_changed(env: Env) -> None:
    env.run("build")
    env.redub(env.bank_lines[0], 777.0)
    code, out = env.run("build")
    assert code == EXIT_OK
    status = {c["asset_id"]: c["status"] for c in out["containers"]}
    assert status == {f"fakebank:{BANK_REL}": "built", f"fakedir:{VOICE_DIR}": "up_to_date"}


def test_build_without_accepted_lines_needs_attention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = make_env(tmp_path, monkeypatch, capsys)
    env.decide(env.bank_lines, decision="regen")
    code, out = env.run("build")
    assert code == EXIT_ATTENTION
    assert out["containers"] == [] and out["replaced"] == 0
    assert "accept" in out["hint"]
    assert not (env.root / layout.BUILD_MANIFEST).exists()


def test_build_reports_missing_audio_and_stale_attempts(env: Env) -> None:
    (env.root / layout.POST_DIR / f"{uid(env.bank_lines[1])}.wav").unlink()
    env.decide([env.bank_lines[2]], attempt=2)  # accepted take 2, but post/ holds take 1
    code, out = env.run("build")
    assert code == EXIT_ATTENTION
    assert out["attention"]["missing_wav"] == [env.bank_lines[1]]
    assert out["attention"]["stale_attempt"] == [env.bank_lines[2]]
    bank = next(c for c in env.manifest()["containers"] if c["adapter"] == "fakebank")
    assert bank["replaced"] == 1


def test_build_only_keeps_other_containers(env: Env) -> None:
    env.run("build")
    old = {c["asset_id"]: c for c in env.manifest()["containers"]}
    env.redub(env.bank_lines[0], 900.0)
    env.redub(env.dir_lines[0], 901.0)
    code, out = env.run("build", "--only", f"fakebank:{BANK_REL}")
    assert code == EXIT_OK, out
    assert [c["asset_id"] for c in out["containers"]] == [f"fakebank:{BANK_REL}"]
    new = {c["asset_id"]: c for c in env.manifest()["containers"]}
    assert new[f"fakedir:{VOICE_DIR}"] == old[f"fakedir:{VOICE_DIR}"]
    assert new[f"fakebank:{BANK_REL}"]["out_sha1"] != old[f"fakebank:{BANK_REL}"]["out_sha1"]

    code, out = env.run("build", "--only", "nope:missing")
    assert code == EXIT_USAGE
    assert out["unmatched"] == ["nope:missing"]


def test_build_dry_run_writes_nothing(env: Env) -> None:
    code, out = env.run("build", "--dry-run")
    assert code == EXIT_OK
    assert out["dry_run"] is True and out["lines"] == 6
    assert not (env.root / layout.BUILD_MANIFEST).exists()
    assert FakeBankAdapter.calls == []


def test_build_drops_container_without_accepted_lines(env: Env) -> None:
    env.run("build")
    folder_out = env.root / layout.BUILD_DIR / VOICE_DIR / "a.wav"
    assert folder_out.is_file()
    env.decide(env.dir_lines, decision="skip")
    code, out = env.run("build")
    assert code == EXIT_OK
    assert [c["asset_id"] for c in env.manifest()["containers"]] == [f"fakebank:{BANK_REL}"]
    assert not folder_out.exists()


def test_build_with_extra_shared_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = make_env(tmp_path, monkeypatch, capsys, catalog=True)
    env.decide(env.bank_lines)
    code, out = env.run("build")
    assert code == EXIT_OK, out
    (bank,) = env.manifest()["containers"]
    files = {f["relpath"]: f for f in bank["files"]}
    assert set(files) == {BANK_REL, CATALOG_REL}
    assert files[CATALOG_REL]["src_sha1"] == env.game_sha(CATALOG_REL)
    patched = json.loads((env.root / files[CATALOG_REL]["out"]).read_text(encoding="utf-8"))
    assert patched["crc"][BANK_REL] == 0
    assert bank["out"] == f"{layout.BUILD_DIR}/{BANK_REL}"


def _make_alt_take(env: Env) -> tuple[str, str]:
    """bank_lines[1] becomes an alt take of bank_lines[0] (same text key, flagged by link)."""
    leader, alt = env.bank_lines[0], env.bank_lines[1]
    rows = read_jsonl(env.root / layout.LINES)
    for row in rows:
        if row["id"] in (leader, alt):
            row["entry"] = {"key": "dlg_Intro_i05" if row["id"] == leader else "dlg_Intro_i05_v1"}
            row["text"] = {"key": "dlg_Intro_i05", "src": "Hello."}
            row["flags"] = ["alt_take"] if row["id"] == alt else []
    write_jsonl(env.root / layout.LINES, rows, schema="babeldub.lines/1")
    return leader, alt


def test_build_gives_followers_the_leader_audio(env: Env) -> None:
    leader, alt = _make_alt_take(env)
    env.redub(alt, 999.0)  # an old dub of the alt take itself must not be used
    code, out = env.run("build")
    assert code == EXIT_OK and out["followers"] == 1, out
    bank = next(c for c in env.manifest()["containers"] if c["adapter"] == "fakebank")
    reps = bank["replacements"]
    assert reps[alt] == {**reps[leader], "via": leader} and "via" not in reps[leader]
    data = json.loads((env.root / bank["out"]).read_text(encoding="utf-8"))
    assert data["entries"][1]["data"] == data["entries"][0]["data"] == f"dub:{reps[leader]['sha1']}"
    code, out = env.run("verify")
    assert code == EXIT_OK, out
    seen = FakeBankAdapter.verify_options[-1]
    assert alt in seen["replaced_ids"] and seen["replacements"][alt] == seen["replacements"][leader]
    assert env.manifest()["models"][0]["lines"] == 6  # the alt take is voiced by the leader's model
    # the leader is no longer accepted: the alt take keeps the original despite its own accept
    env.decide([leader], decision="regen")
    code, out = env.run("build")
    assert "followers" not in out
    bank = next(c for c in env.manifest()["containers"] if c["adapter"] == "fakebank")
    assert set(bank["replacements"]) == {env.bank_lines[2]}
    assert env.manifest()["models"][0]["lines"] == 4  # the follower's own old accept does not count
    data = json.loads((env.root / bank["out"]).read_text(encoding="utf-8"))
    assert [e["data"] for e in data["entries"][:2]] == ["orig0", "orig1"]


def test_build_rejects_unsafe_relpath(env: Env) -> None:
    rows = [
        {
            "asset_id": "fakebank:../outside.fbank",
            "adapter": "fakebank",
            "relpath": "../outside.fbank",
            "role": "voice",
        }
    ]
    write_jsonl(env.root / layout.ASSETS, rows)
    lines = [
        {"id": env.bank_lines[0], "uid": uid(env.bank_lines[0]), "asset_id": "fakebank:../outside.fbank"}
    ]
    write_jsonl(env.root / layout.LINES, lines)
    code, out = env.run("build")
    assert code == EXIT_USAGE
    assert "unsafe path" in out["error"]
    assert FakeBankAdapter.calls == []


def test_build_failure_keeps_last_good_output(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.run("build")
    good = {c["asset_id"]: c for c in env.manifest()["containers"]}

    def broken(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("encoder crashed")

    monkeypatch.setattr(FakeBankAdapter, "build", broken)
    env.redub(env.bank_lines[0], 555.0)
    code, out = env.run("build")
    assert code == EXIT_ATTENTION
    assert out["failed"][0]["asset_id"] == f"fakebank:{BANK_REL}"
    assert "encoder crashed" in out["failed"][0]["error"]
    kept = {c["asset_id"]: c for c in env.manifest()["containers"]}
    assert kept[f"fakebank:{BANK_REL}"] == good[f"fakebank:{BANK_REL}"]
    assert sha1_of(env.root / layout.BUILD_DIR / BANK_REL) == good[f"fakebank:{BANK_REL}"]["out_sha1"]


def test_verify_ok_and_detects_tampering(env: Env) -> None:
    code, out = env.run("verify")
    assert code == EXIT_USAGE  # nothing built yet
    env.run("build")
    code, out = env.run("verify")
    assert code == EXIT_OK, out
    assert out["ok"] is True and out["verified"] == 2 and out["problems"] == []
    prog = env.progress()["verify"]
    assert prog["status"] == "done" and prog["build_id"] == env.manifest()["build_id"]
    # the adapter gets the replaced line ids and the exact replacement files of the build
    (seen,) = FakeBankAdapter.verify_options
    bank = next(c for c in env.manifest()["containers"] if c["adapter"] == "fakebank")
    assert seen["replaced_ids"] == sorted(bank["replacements"])
    assert sorted(seen["replacements"]) == seen["replaced_ids"]
    assert all(Path(p).is_file() for p in seen["replacements"].values())

    (env.root / layout.BUILD_DIR / BANK_REL).write_text("{}", encoding="utf-8")
    code, out = env.run("verify")
    assert code == EXIT_ATTENTION
    assert out["ok"] is False
    assert "changed after build" in out["problems"][0]["problem"]
    assert env.progress()["verify"]["status"] == "attention"
