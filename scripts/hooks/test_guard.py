"""Tests for scripts/hooks/guard.py and claude/hooks.json (synthetic data only).

Run: <runtime venv python> -m pytest scripts/hooks -q
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
GUARD = Path(guard.__file__).resolve()
WIN_GAME = r"G:\Low-Budget Repairs"
POSIX_GAME = "/home/user/Games/My Game"


def win(command: str, tool: str = "Bash", cwd: str = r"C:\work\proj") -> str | None:
    event = {"tool_name": tool, "tool_input": {"command": command}, "cwd": cwd}
    return guard.check_event(event, [WIN_GAME], windows=True)


def nix(command: str, cwd: str = "/home/user/work") -> str | None:
    event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}
    return guard.check_event(event, [POSIX_GAME], windows=False)


def ps(command: str) -> str | None:
    return win(command, tool="PowerShell")


# ---------------------------------------------------------------- file tools


@pytest.mark.parametrize(
    ("tool", "key", "path"),
    [
        (
            "Write",
            "file_path",
            r"G:\Low-Budget Repairs\LowBudgetRepairs_Data\StreamingAssets\Dialogue_en-US.bank",
        ),
        ("Edit", "file_path", "g:/low-budget repairs/notes.txt"),
        ("MultiEdit", "file_path", r"G:\LOW-BUDGET REPAIRS\a\..\b.txt"),
        ("NotebookEdit", "notebook_path", r"G:\Low-Budget Repairs\x.ipynb"),
        ("Write", "file_path", "/g/Low-Budget Repairs/x.txt"),
        ("Write", "file_path", r"G:\Low-Budget Repairs"),
    ],
)
def test_file_tools_inside_game_are_blocked(tool: str, key: str, path: str) -> None:
    event = {"tool_name": tool, "tool_input": {key: path}, "cwd": r"C:\work"}
    reason = guard.check_event(event, [WIN_GAME], windows=True)
    assert reason and "Low-Budget Repairs" in reason


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Users\me\AppData\Local\BabelDub\projects\lbr\translate\batches\b001.out.jsonl",
        r"G:\Low-Budget Repairs Mods\x.txt",  # sibling folder with the same prefix
        r"G:\x.txt",
    ],
)
def test_file_tools_outside_game_are_allowed(path: str) -> None:
    event = {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": r"C:\work"}
    assert guard.check_event(event, [WIN_GAME], windows=True) is None


def test_relative_write_with_cwd_inside_game_is_blocked() -> None:
    event = {
        "tool_name": "Write",
        "tool_input": {"file_path": "notes.txt"},
        "cwd": r"G:\Low-Budget Repairs\Data",
    }
    assert guard.check_event(event, [WIN_GAME], windows=True)


def test_other_tools_and_empty_registry_are_allowed() -> None:
    event = {"tool_name": "Read", "tool_input": {"file_path": r"G:\Low-Budget Repairs\x"}}
    assert guard.check_event(event, [WIN_GAME], windows=True) is None
    event = {"tool_name": "Write", "tool_input": {"file_path": r"G:\Low-Budget Repairs\x"}}
    assert guard.check_event(event, [], windows=True) is None


# ---------------------------------------------------------------- Bash (Git Bash on Windows)

BLOCKED_BASH = [
    'cp lab/out/Dialogue_en-US.bank "G:/Low-Budget Repairs/LowBudgetRepairs_Data/StreamingAssets/"',
    'rm -rf "/g/Low-Budget Repairs/LowBudgetRepairs_Data/StreamingAssets/Dialogue_en-US.bank"',
    'mv "G:/Low-Budget Repairs/a.bank" /c/tmp/a.bank',
    'echo hi > "G:/Low-Budget Repairs/readme.txt"',
    'cat x >> "G:\\Low-Budget Repairs\\log.txt"',
    'vgmstream-cli -o "G:/Low-Budget Repairs/out.wav" in.bank',
    'babeldub install && rm "G:/Low-Budget Repairs/x.bank"',
    "cd /tmp\nrm -r '/mnt/g/Low-Budget Repairs/Data'",
    """python -c "import shutil; shutil.rmtree(r'G:\\Low-Budget Repairs\\Data')" """,
    """python - <<'EOF'\nopen(r"G:/Low-Budget Repairs/a.txt", "w").write("x")\nEOF""",
    """python -c "import shutil; shutil.copy('C:/tmp/a.bank', r'G:/Low-Budget Repairs/a.bank')" """,
    """bash -c 'rm "G:/Low-Budget Repairs/x"'""",
    'find "G:/Low-Budget Repairs" -name "*.tmp" -delete',
    'find "G:/Low-Budget Repairs" -name "*.tmp" -exec rm {} \\;',
    'ls "G:/Low-Budget Repairs"/*.bak | xargs rm -f',
    'tar -xzf mod.tgz -C "G:/Low-Budget Repairs"',
    'tar -czf "G:/Low-Budget Repairs/backup.tgz" src',
    'unzip mod.zip -d "G:/Low-Budget Repairs"',
    'sed -i "s/a/b/" "G:/Low-Budget Repairs/config.ini"',
    'touch "G:/Low-Budget Repairs/x"',
    'cmd //c del "G:\\Low-Budget Repairs\\x.bank"',
    "powershell -NoProfile -Command \"Remove-Item 'G:\\Low-Budget Repairs\\x'\"",
    'dd if=/dev/zero of="G:/Low-Budget Repairs/x.bin" bs=1 count=1',
    'cp -t "G:/Low-Budget Repairs" a.bank b.bank',
    'sudo rm -rf "G:/Low-Budget Repairs"',
    'uv pip install --target "G:/Low-Budget Repairs/py" requests',
    'curl -o "G:/Low-Budget Repairs/x.zip" https://example.com/x.zip',
]


@pytest.mark.parametrize("command", BLOCKED_BASH)
def test_bash_writes_into_game_are_blocked(command: str) -> None:
    assert win(command), command


ALLOWED_BASH = [
    'ls -la "G:/Low-Budget Repairs/LowBudgetRepairs_Data/StreamingAssets"',
    'ls "G:\\Low-Budget Repairs" > files.txt',
    'cp "G:/Low-Budget Repairs/LowBudgetRepairs_Data/StreamingAssets/Dialogue_en-US.bank" lab/data/',
    'vgmstream-cli -o out.wav -s 3 "G:/Low-Budget Repairs/StreamingAssets/Dialogue_en-US.bank"',
    'babeldub init "G:/Low-Budget Repairs" --lang ru',
    "babeldub install",
    "babeldub uninstall --project low-budget-repairs",
    "uv run --project runtime babeldub reapply --pretty",
    "python -m babeldub install",
    'find "G:/Low-Budget Repairs" -name "*.bank"',
    'sha1sum "G:/Low-Budget Repairs/StreamingAssets/"*.bank',
    "rm -rf build/out && mkdir -p build/out",
    'grep -r "Dialogue" "G:/Low-Budget Repairs/StreamingAssets" | head -n 5',
    """python -c "import shutil; shutil.copy(r'G:/Low-Budget Repairs/a.bank', 'C:/tmp/a.bank')" """,
    'python lab/lbr/scripts/fsb5_info.py "G:/Low-Budget Repairs" --out lab/lbr/data/fsb.json',
    "cat > notes.md <<'EOF'\nThe game lives in G:/Low-Budget Repairs and stays read-only.\nEOF",
    'tar -tzf "G:/Low-Budget Repairs/archive.tgz"',
    'robocopy "G:\\Low-Budget Repairs" D:\\backup /E',
    "echo done",
]


@pytest.mark.parametrize("command", ALLOWED_BASH)
def test_bash_reads_and_babeldub_writers_are_allowed(command: str) -> None:
    assert win(command) is None, command


def test_python_launcher_must_run_babeldub_module() -> None:
    assert win("python -c \"import os; os.remove(r'G:/Low-Budget Repairs/x')\" babeldub install")


def test_cwd_inside_game_makes_relative_writes_visible() -> None:
    assert win("rm save.dat", cwd=r"G:\Low-Budget Repairs\Saves")
    assert win("ls -la", cwd=r"G:\Low-Budget Repairs\Saves") is None


def test_cd_into_game_is_followed_within_one_command() -> None:
    assert win('cd "G:/Low-Budget Repairs/Data" && rm save.dat')
    assert win('cd "/g/Low-Budget Repairs"; cp /c/tmp/a.bank StreamingAssets/')
    assert ps('Set-Location "G:\\Low-Budget Repairs"; Remove-Item notes.txt')
    assert win('cd "G:/Low-Budget Repairs" && ls -la && cd /c/work && rm -rf build') is None
    assert win('cd "G:/Low-Budget Repairs" && sha1sum StreamingAssets/*.bank') is None


# ---------------------------------------------------------------- PowerShell

BLOCKED_PS = [
    'Remove-Item -Recurse -Force "G:\\Low-Budget Repairs\\LowBudgetRepairs_Data\\x"',
    "Copy-Item C:\\tmp\\Dialogue_en-US.bank -Destination 'G:\\Low-Budget Repairs\\StreamingAssets\\'",
    'Copy-Item C:\\tmp\\a.bank "G:\\Low-Budget Repairs\\a.bank"',
    'Move-Item "G:\\Low-Budget Repairs\\a.bank" C:\\tmp\\a.bank',
    'Set-Content -Path "G:\\Low-Budget Repairs\\x.txt" -Value 1',
    '"x" | Out-File -FilePath "G:\\Low-Budget Repairs\\x.txt"',
    'Get-ChildItem "G:\\Low-Budget Repairs" -Filter *.tmp | Remove-Item',
    '[IO.File]::WriteAllBytes("G:\\Low-Budget Repairs\\a.bin", $bytes)',
    'New-Item -ItemType Directory -Path "G:\\Low-Budget Repairs\\Mods"',
    'robocopy C:\\build "G:\\Low-Budget Repairs\\StreamingAssets" /E',
    'robocopy "G:\\Low-Budget Repairs\\Old" D:\\archive /MOVE',
    'cmd /c del "G:\\Low-Budget Repairs\\x.bank"',
    'Expand-Archive mod.zip -DestinationPath "G:\\Low-Budget Repairs"',
    '$r = Remove-Item "G:\\Low-Budget Repairs\\x"',
    'Rename-Item "G:\\Low-Budget Repairs\\a.bank" b.bank',
]


@pytest.mark.parametrize("command", BLOCKED_PS)
def test_powershell_writes_into_game_are_blocked(command: str) -> None:
    assert ps(command), command


ALLOWED_PS = [
    'Get-ChildItem "G:\\Low-Budget Repairs\\LowBudgetRepairs_Data\\StreamingAssets"',
    'Copy-Item "G:\\Low-Budget Repairs\\a.bank" -Destination C:\\tmp\\a.bank',
    'Get-Content "G:\\Low-Budget Repairs\\x.txt" | Out-File C:\\tmp\\y.txt',
    'Get-FileHash "G:\\Low-Budget Repairs\\StreamingAssets\\Dialogue_en-US.bank" -Algorithm SHA1',
    "babeldub install --project low-budget-repairs",
    '& "C:\\Users\\me\\.local\\bin\\babeldub.exe" uninstall',
    'Set-Content -Path C:\\tmp\\note.txt -Value "G:\\Low-Budget Repairs"',
    "(Get-Content 'G:\\Low-Budget Repairs\\x.txt').Replace('a', 'b') | Set-Content C:\\tmp\\y.txt",
    'Get-ChildItem "G:\\Low-Budget Repairs" -Recurse | Select-Object FullName | Out-File C:\\tmp\\list.txt',
]


@pytest.mark.parametrize("command", ALLOWED_PS)
def test_powershell_reads_are_allowed(command: str) -> None:
    assert ps(command) is None, command


def test_powershell_encoded_command_is_decoded() -> None:
    inner = 'Remove-Item "G:\\Low-Budget Repairs\\x.bank"'
    encoded = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
    assert ps(f"powershell -NoProfile -EncodedCommand {encoded}")
    assert win(f"pwsh -enc {encoded}")


# ---------------------------------------------------------------- Linux paths


@pytest.mark.parametrize(
    "command",
    [
        'rm -r "/home/user/Games/My Game/data"',
        "rm -r /home/user/Games/My\\ Game/data",
        "cp mod.pak '/home/user/Games/My Game/Content/Paks/'",
        "mv ~/Games/'My Game'/a.bank /tmp/",
    ],
)
def test_posix_paths_are_blocked(command: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/user")
    monkeypatch.setenv("USERPROFILE", "/home/user")
    assert nix(command), command


@pytest.mark.parametrize(
    "command",
    [
        'cp "/home/user/Games/My Game/a.bank" /tmp/',
        'ls "/home/user/Games/My Game"',
        "rm -rf /home/user/Games/My\\ GameMods/x",
    ],
)
def test_posix_reads_are_allowed(command: str) -> None:
    assert nix(command) is None, command


# ---------------------------------------------------------------- tokenizer edge cases


def test_unbalanced_quotes_use_tolerant_tokenizer() -> None:
    assert win('rm "G:/Low-Budget Repairs/x')
    assert win('echo "unterminated G:/Low-Budget Repairs') is None


def test_main_is_fail_open_on_internal_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_: str) -> int:
        raise RuntimeError("bug")

    monkeypatch.setattr(guard, "run_pre", boom)
    monkeypatch.setattr(guard, "_read_stdin", lambda: "{}")
    assert guard.main(["pre"]) == guard.EXIT_ALLOW


# ---------------------------------------------------------------- end to end (subprocess)


def _run(
    args: list[str], home: Path, stdin: str = "", extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ, BABELDUB_HOME=str(home))
    env.pop("BABELDUB_GUARD", None)
    env.update(extra_env or {})
    return subprocess.run(args, input=stdin.encode("utf-8"), capture_output=True, env=env, timeout=60)


@pytest.fixture()
def registry(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    game = tmp_path / "Some Game"
    (game / "Data").mkdir(parents=True)
    home.mkdir()
    (home / "games.json").write_text(
        json.dumps({"games": [{"game_dir": str(game.resolve()), "slug": "some-game"}]}), encoding="utf-8"
    )
    return home, game


def _event(tool: str, **tool_input: str) -> str:
    return json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": str(Path.cwd())})


def test_e2e_empty_registry_exits_zero_silently(tmp_path: Path) -> None:
    proc = _run([sys.executable, str(GUARD), "pre"], tmp_path, _event("Write", file_path=str(tmp_path / "x")))
    assert proc.returncode == 0 and proc.stdout == b"" and proc.stderr == b""


def test_e2e_block_and_allow(registry: tuple[Path, Path]) -> None:
    home, game = registry
    target = game / "Data" / "a.bank"
    proc = _run([sys.executable, str(GUARD), "pre"], home, _event("Write", file_path=str(target)))
    assert proc.returncode == 2
    message = proc.stderr.decode("utf-8")
    assert "BabelDub" in message and "babeldub install" in message
    proc = _run(
        [sys.executable, str(GUARD), "pre"], home, _event("Bash", command=f'cp "{target}" /tmp/a.bank')
    )
    assert proc.returncode == 0


def test_e2e_fail_open_on_garbage(registry: tuple[Path, Path]) -> None:
    home, _ = registry
    assert _run([sys.executable, str(GUARD), "pre"], home, "not json").returncode == 0
    (home / "games.json").write_text("{broken", encoding="utf-8")
    assert _run([sys.executable, str(GUARD), "pre"], home, _event("Write", file_path="x")).returncode == 0


def test_e2e_kill_switch(registry: tuple[Path, Path]) -> None:
    home, game = registry
    event = _event("Write", file_path=str(game / "x.txt"))
    proc = _run([sys.executable, str(GUARD), "pre"], home, event, {"BABELDUB_GUARD": "off"})
    assert proc.returncode == 0


# ---------------------------------------------------------------- SessionStart


def _make_project(home: Path, progress: dict[str, dict[str, str]]) -> Path:
    project = home / "projects" / "some-game"
    (project / "state").mkdir(parents=True)
    (project / "project.json").write_text(
        json.dumps(
            {
                "slug": "some-game",
                "game_name": "Some Game",
                "game_dir": "D:/Games/Some Game",
                "target_lang": "ru",
                "tts": {"engine": "qwen3tts", "ref_strategy": "auto"},
            }
        ),
        encoding="utf-8",
    )
    (project / "state" / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
    (home / "active.json").write_text(json.dumps({"project": str(project)}), encoding="utf-8")
    return project


def test_session_without_active_project_prints_nothing(tmp_path: Path) -> None:
    assert guard.session_summary(tmp_path) == []
    proc = _run([sys.executable, str(GUARD), "session"], tmp_path)
    assert proc.returncode == 0 and proc.stdout == b""


def test_session_summary_points_to_next_stage(tmp_path: Path) -> None:
    done = {"status": "done", "updated": "2026-10-21T10:00:00+00:00"}
    _make_project(
        tmp_path, {s: done for s in ("init", "scan", "extract", "link")} | {"cast": {"status": "attention"}}
    )
    lines = guard.session_summary(tmp_path)
    assert 5 <= len(lines) <= 10
    text = "\n".join(lines)
    assert "Some Game" in text and "→ ru" in text
    assert "стадия cast ждёт решений" in text and "babeldub-cast" in text
    assert "ref_strategy=auto" in text and "оригинального героя" in text
    proc = _run([sys.executable, str(GUARD), "session"], tmp_path)
    assert proc.returncode == 0 and "Some Game" in proc.stdout.decode("utf-8")


@pytest.mark.parametrize(
    ("progress", "expected"),
    [
        ({}, "`babeldub init"),
        ({"init": {"status": "done"}}, "`babeldub scan`"),
        ({"init": {"status": "done"}, "scan": {"status": "failed"}}, "завершилась ошибкой"),
        ({"init": {"status": "done"}, "scan": {"status": "running"}}, "не завершена"),
        (
            {s: {"status": "done"} for s in ("init", "scan", "extract")} | {"link": {"status": "attention"}},
            "`babeldub cast` (скилл babeldub-cast); стадия link ждёт решений",
        ),
        ({s: {"status": "done", "updated": "1"} for s in guard.STAGES}, "озвучка установлена"),
        (
            {s: {"status": "done", "updated": "1"} for s in guard.STAGES}
            | {"install": {"status": "attention", "state": "uninstalled"}},
            "озвучка снята",
        ),
        (
            {s: {"status": "done"} for s in guard.STAGES}
            | {"install": {"status": "done", "state": "installed"}},
            "озвучка установлена",
        ),
    ],
)
def test_next_step(progress: dict[str, dict[str, str]], expected: str) -> None:
    assert expected in guard.next_step(progress)


def test_session_ignores_missing_project_folder(tmp_path: Path) -> None:
    (tmp_path / "active.json").write_text(json.dumps({"project": str(tmp_path / "gone")}), encoding="utf-8")
    assert guard.session_summary(tmp_path) == []


# ---------------------------------------------------------------- claude/hooks.json


def _hooks() -> dict:
    raw = (REPO / "claude" / "hooks.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "hooks.json must be UTF-8 without BOM"
    return json.loads(raw.decode("utf-8"))


def test_hooks_json_shape() -> None:
    hooks = _hooks()["hooks"]
    assert set(hooks) == {"PreToolUse", "SessionStart"}
    (pre,) = hooks["PreToolUse"]
    assert pre["matcher"] == "Write|Edit|MultiEdit|NotebookEdit|Bash|PowerShell"
    (session,) = hooks["SessionStart"]
    assert session["matcher"] == "startup|resume|compact"
    for group, mode in ((pre, "pre"), (session, "session")):
        (handler,) = group["hooks"]
        assert handler["type"] == "command" and handler["timeout"] == 5
        assert handler["command"] == "uv"
        assert handler["args"][-2:] == ["${CLAUDE_PLUGIN_ROOT}/scripts/hooks/guard.py", mode]
        assert "--script" in handler["args"] and "--no-project" in handler["args"]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed on this machine")
def test_hooks_json_command_runs_through_uv(registry: tuple[Path, Path]) -> None:
    home, game = registry
    handler = _hooks()["hooks"]["PreToolUse"][0]["hooks"][0]
    args = [a.replace("${CLAUDE_PLUGIN_ROOT}", REPO.as_posix()) for a in handler["args"]]
    proc = _run([shutil.which("uv") or "uv", *args], home, _event("Edit", file_path=str(game / "a.txt")))
    assert proc.returncode == 2 and "BabelDub" in proc.stderr.decode("utf-8")
