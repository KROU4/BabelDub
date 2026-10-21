"""Guards: anti-cheat detection and running-process detection."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from babeldub.pack import guards

# the conftest replaces guards.check_running for command tests; keep the real one here
REAL_CHECK_RUNNING = guards.check_running


@pytest.mark.parametrize(
    ("recon", "expected"),
    [
        ({"anticheat": ["EasyAntiCheat", "BattlEye"]}, ["EasyAntiCheat", "BattlEye"]),
        ({"anticheat": "XIGNCODE3"}, ["XIGNCODE3"]),
        ({"anticheat": True}, ["anti-cheat"]),
        ({"anticheat": False}, []),
        ({"anticheat": {"detected": False, "names": ["EasyAntiCheat"]}}, []),
        ({"anticheat": {"detected": True, "names": ["EasyAntiCheat"]}}, ["EasyAntiCheat"]),
        ({"anti_cheat": [{"name": "EQU8"}]}, ["EQU8"]),
        ({"middleware": ["fmod", {"name": "BattlEye"}]}, ["BattlEye"]),
        ({"warnings": ["no anti-cheat found", "fonts lack Cyrillic"]}, []),
        (
            {"warnings": [{"message": "EasyAntiCheat present: online game"}]},
            ["EasyAntiCheat present: online game"],
        ),
        (None, []),
    ],
)
def test_anticheat_from_recon(recon: dict[str, object] | None, expected: list[str]) -> None:
    assert guards.anticheat_from_recon(recon) == expected


def test_anticheat_folder_scan(tmp_path: Path) -> None:
    game = tmp_path / "game"
    (game / "Binaries" / "Win64").mkdir(parents=True)
    (game / "Binaries" / "Win64" / "BEClient_x64.dll").write_bytes(b"")
    (game / "start_protected_game.exe").write_bytes(b"")
    deep = game / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "EasyAntiCheat_x64.dll").write_bytes(b"")  # deeper than the scan: ignored
    (game / "Data").mkdir()
    (game / "Data" / "battle_music.bank").write_bytes(b"")  # not a marker
    hits = sorted(guards.anticheat_in_folder(game))
    assert hits == [
        ("BattlEye", "Binaries/Win64/BEClient_x64.dll"),
        ("EasyAntiCheat", "start_protected_game.exe"),
    ]

    found = guards.detect_anticheat(game, {"anticheat": ["EasyAntiCheat"]})
    assert {f["name"] for f in found} == {"EasyAntiCheat", "BattlEye"}
    assert next(f for f in found if f["name"] == "EasyAntiCheat")["source"] == "recon.json"


def test_anticheat_absent(tmp_path: Path) -> None:
    (tmp_path / "Game_Data").mkdir()
    (tmp_path / "Game.exe").write_bytes(b"")
    assert guards.detect_anticheat(tmp_path, {"engine": "unity"}) == []
    assert guards.detect_anticheat(tmp_path / "missing", None) == []


def test_parse_tasklist_csv() -> None:
    text = (
        '"Image Name","PID","Session Name","Session#","Mem Usage"\r\n'
        '"System Idle Process","0","Services","0","8 K"\r\n'
        '"LowBudgetRepairs.exe","4242","Console","1","1,234,567 K"\r\n'
        "INFO: something localized\r\n"
        '"UnityCrashHandler64.exe","5150","Console","1","9,000 K","Running","PC\\user","0:00:01","N/A"\r\n'
    )
    rows = guards.parse_tasklist_csv(text)
    assert rows == [
        {"name": "System Idle Process", "pid": 0},
        {"name": "LowBudgetRepairs.exe", "pid": 4242},
        {"name": "UnityCrashHandler64.exe", "pid": 5150},
    ]


def test_parse_ps_rows() -> None:
    lines = ["4242|LowBudgetRepairs|G:\\Games\\LBR\\LowBudgetRepairs.exe", "17|System|", "garbage", "x|y|z"]
    assert guards.parse_ps_rows(lines) == [
        {"pid": 4242, "name": "LowBudgetRepairs", "exe": "G:\\Games\\LBR\\LowBudgetRepairs.exe"},
        {"pid": 17, "name": "System", "exe": None},
    ]


def test_hidden_path_counts_as_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A process with the game's exe name whose path is hidden (elevated) is treated as the game."""
    (tmp_path / "Game.exe").write_bytes(b"")
    monkeypatch.setattr(guards, "_windows_by_name", lambda names: [{"pid": 7, "name": "Game", "exe": None}])
    hits, method = guards._scan_windows(tmp_path, guards._roots(tmp_path))
    assert method == "powershell"
    assert hits == [{"pid": 7, "name": "Game", "exe": None, "confirmed": False}]


def test_check_running_empty_folder(tmp_path: Path) -> None:
    (tmp_path / "Data").mkdir()
    result = REAL_CHECK_RUNNING(tmp_path)
    assert result["processes"] == []
    assert result["error"] is None


def _interpreter_folders() -> list[Path]:
    """Folders of the running interpreter: the base install first (a venv launcher is its parent)."""
    base = Path(os.path.realpath(getattr(sys, "_base_executable", sys.executable))).parent
    venv = Path(os.path.realpath(sys.executable)).parent
    return [base] if base == venv else [base, venv]


def _assert_detects_this_process() -> None:
    own = {os.getpid(), os.getppid()}
    seen: list[dict[str, object]] = []
    for folder in _interpreter_folders():
        result = REAL_CHECK_RUNNING(folder)
        if result["method"] == "none":
            pytest.skip(f"no process detection available: {result['error']}")
        seen.extend(result["processes"])
        if own & {p["pid"] for p in seen}:
            return
    raise AssertionError(f"this process was not detected: {seen}")


def test_check_running_finds_live_process() -> None:
    """The interpreter running this test lives in a folder: that folder counts as a 'running game'."""
    _assert_detects_this_process()


@pytest.mark.skipif(sys.platform != "win32", reason="tasklist exists only on Windows")
def test_check_running_tasklist_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guards, "_windows_by_name", lambda names: None)
    if guards._scan_psutil(guards._roots(Path.cwd())) is not None:
        pytest.skip("psutil is installed: the Windows fallback is not used")
    _assert_detects_this_process()
