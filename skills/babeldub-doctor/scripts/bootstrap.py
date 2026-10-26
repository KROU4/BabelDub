#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Install or update the BabelDub CLI (`babeldub`) as a uv tool and check its version.

Stdlib only, so it runs on any agent host with Python 3.10+ (`python bootstrap.py` or
`uv run --no-project --script bootstrap.py`). The expected CLI version is read from
`metadata.babeldub-cli` in this skill's SKILL.md.

Source of the package, first match wins:
  1. --source <dir|requirement>      explicit path or uv/pip requirement (e.g. git+https://...)
  2. --plugin-root <dir>             plugin folder: <dir>/runtime (Claude Code passes ${CLAUDE_PLUGIN_ROOT})
  3. $CLAUDE_PLUGIN_ROOT/runtime     when a host exports it
  4. <repo>/runtime                  when the skill sits inside a BabelDub checkout, plugin copy or kit
  5. git+https://github.com/KROU4/BabelDub@v<version>#subdirectory=runtime

Prints exactly one JSON object to stdout. Exit codes follow the CLI contract:
0 ok, 1 unexpected error (CLI installed but broken), 2 bad arguments, 4 environment problem.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_ENV = 4

FALLBACK_VERSION = "0.3.0"
GIT_SOURCE = "git+https://github.com/KROU4/BabelDub@v{version}#subdirectory=runtime"
PACKAGE = "babeldub"
IS_WINDOWS = sys.platform == "win32"
EXE = PACKAGE + (".exe" if IS_WINDOWS else "")

SKILL_DIR = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(r"(\d+\.\d+\.\d+(?:[.\-+]?[0-9A-Za-z.]+)?)")
META_RE = re.compile(r"""^\s*babeldub-cli\s*:\s*["']?([^"'\s#]+)["']?\s*(?:#.*)?$""")
UV_INSTALL_HINT = (
    'install uv: Windows PowerShell `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`; '
    "Linux/macOS `curl -LsSf https://astral.sh/uv/install.sh | sh`; then open a new terminal"
)


class BootstrapError(Exception):
    def __init__(self, message: str, code: int, hint: str | None = None, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.extra = extra


def emit(payload: dict[str, Any], code: int) -> int:
    payload = {"ok": code == EXIT_OK, **payload}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return code


def run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def tail(text: str, lines: int = 6) -> list[str]:
    return [line for line in (text or "").strip().splitlines()[-lines:] if line.strip()]


def normalize(version: str | None) -> str | None:
    if not version:
        return None
    version = version.strip()
    return version[1:] if version[:1] in ("v", "V") else version


def expected_version(skill_md: Path) -> str:
    """Read metadata.babeldub-cli from the SKILL.md frontmatter (no YAML dependency)."""
    try:
        text = skill_md.read_text(encoding="utf-8-sig")
    except OSError:
        return FALLBACK_VERSION
    parts = text.split("---", 2)
    if not text.startswith("---") or len(parts) < 3:
        return FALLBACK_VERSION
    for line in parts[1].splitlines():
        match = META_RE.match(line)
        if match:
            return normalize(match.group(1)) or FALLBACK_VERSION
    return FALLBACK_VERSION


def is_runtime_dir(path: Path) -> bool:
    pyproject = path / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        content = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r'^\s*name\s*=\s*"babeldub"\s*$', content, re.MULTILINE) is not None


def usable_dir(raw: str | None) -> Path | None:
    """A directory argument, ignoring unsubstituted host placeholders like ${CLAUDE_PLUGIN_ROOT}."""
    if not raw or "${" in raw or raw.startswith("$"):
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def resolve_source(args: argparse.Namespace, version: str) -> tuple[str, str]:
    """Return (source, kind) where kind is explicit|plugin|env|repo|git."""
    if args.source:
        local = Path(args.source).expanduser()
        if local.exists():
            if not is_runtime_dir(local):
                raise BootstrapError(
                    f'{local} is not the BabelDub runtime (no pyproject.toml with name = "babeldub")',
                    EXIT_USAGE,
                    hint="pass the runtime/ folder of the BabelDub repository or a git+https requirement",
                )
            return str(local.resolve()), "explicit"
        return args.source, "explicit"
    root = usable_dir(args.plugin_root)
    if root and is_runtime_dir(root / "runtime"):
        return str((root / "runtime").resolve()), "plugin"
    root = usable_dir(os.environ.get("CLAUDE_PLUGIN_ROOT"))
    if root and is_runtime_dir(root / "runtime"):
        return str((root / "runtime").resolve()), "env"
    # <repo>/skills/<skill>/ (checkout, plugin copy) or <kit>/.agents|.claude/skills/<skill>/ (workspace kit)
    for repo_runtime in (SKILL_DIR.parent.parent / "runtime", SKILL_DIR.parent.parent.parent / "runtime"):
        if is_runtime_dir(repo_runtime):
            return str(repo_runtime.resolve()), "repo"
    return GIT_SOURCE.format(version=version), "git"


def find_uv() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise BootstrapError("uv not found", EXIT_ENV, hint=UV_INSTALL_HINT)
    return uv


def tool_bin_dir(uv: str) -> Path | None:
    try:
        proc = run([uv, "tool", "dir", "--bin"], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip().splitlines()[-1])


def candidate_exes(uv: str | None) -> list[Path]:
    found: list[Path] = []
    if uv:
        bin_dir = tool_bin_dir(uv)
        if bin_dir and (bin_dir / EXE).is_file():
            found.append(bin_dir / EXE)
    on_path = shutil.which(PACKAGE)
    if on_path and Path(on_path) not in found:
        found.append(Path(on_path))
    return found


def cli_version(exe: Path) -> tuple[str | None, list[str]]:
    """Ask the CLI itself; returns (version, error_tail)."""
    try:
        proc = run([str(exe), "--version"], timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, [f"{type(exc).__name__}: {exc}"]
    match = re.search(r"babeldub\s+v?" + VERSION_RE.pattern, proc.stdout or "")
    if proc.returncode == 0 and match:
        return normalize(match.group(1)), []
    return None, tail(proc.stderr or proc.stdout)


def uv_tool_version(uv: str) -> str | None:
    """Version recorded by `uv tool list` (works even if the CLI fails to start)."""
    try:
        proc = run([uv, "tool", "list"], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"^babeldub\s+v" + VERSION_RE.pattern, proc.stdout or "", re.MULTILINE)
    return normalize(match.group(1)) if match else None


def inspect_installed(uv: str | None) -> dict[str, Any]:
    exes = candidate_exes(uv)
    exe = exes[0] if exes else None
    version, error = cli_version(exe) if exe else (None, [])
    recorded = uv_tool_version(uv) if uv else None
    on_path = shutil.which(PACKAGE)
    return {
        "exe": str(exe) if exe else None,
        "cli_version": version,
        "uv_tool_version": recorded,
        "cli_error": error,
        "on_path": bool(on_path) and exe is not None and Path(on_path).resolve() == exe.resolve(),
    }


def install_command(uv: str, source: str) -> list[str]:
    # --force replaces existing entry points; --reinstall-package rebuilds babeldub even at the same version
    return [uv, "tool", "install", "--force", "--reinstall-package", PACKAGE, "--from", source, PACKAGE]


def install(uv: str, source: str, timeout: float) -> None:
    cmd = install_command(uv, source)
    try:
        proc = run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise BootstrapError(
            f"uv tool install timed out after {int(timeout)} s",
            EXIT_ENV,
            hint="check the network, then rerun with a larger --timeout",
            command=cmd,
        ) from exc
    if proc.returncode != 0:
        hint = "check the source path or network"
        if source.startswith("git+") and not shutil.which("git"):
            hint = "git is required for a git+https source: install git or pass --source <path to runtime>"
        raise BootstrapError(
            f"uv tool install failed with code {proc.returncode}",
            EXIT_ENV,
            hint=hint,
            command=cmd,
            detail=tail(proc.stderr or proc.stdout),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or update the BabelDub CLI with uv and check its version."
    )
    parser.add_argument("--source", help="runtime folder or requirement to install from (default: auto)")
    parser.add_argument(
        "--plugin-root", help="plugin root that contains runtime/ (Claude Code: ${CLAUDE_PLUGIN_ROOT})"
    )
    parser.add_argument("--expect", help="expected CLI version (default: metadata.babeldub-cli of SKILL.md)")
    parser.add_argument("--check", action="store_true", help="only report, never install")
    parser.add_argument("--force", action="store_true", help="reinstall even if the version already matches")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the planned uv command without running it"
    )
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds allowed for uv tool install")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE

    version = normalize(args.expect) or expected_version(SKILL_DIR / "SKILL.md")
    try:
        uv = shutil.which("uv")
        before = inspect_installed(uv)
        current = before["cli_version"] or before["uv_tool_version"]
        matches = current == version and before["cli_version"] is not None

        if args.check or (matches and not args.force):
            code = EXIT_OK if matches else EXIT_ENV
            payload: dict[str, Any] = {"expected": version, "installed": current, "action": "none", **before}
            if not matches:
                payload["hint"] = "run bootstrap.py without --check to install the expected version"
            if not uv:
                payload["uv"] = None
                payload.setdefault("hint", UV_INSTALL_HINT)
            payload["next"] = "babeldub doctor" if matches else None
            return emit(payload, code)

        uv = find_uv()
        source, kind = resolve_source(args, version)
        if args.dry_run:
            payload = {
                "expected": version,
                "installed": current,
                "action": "planned",
                "source": source,
                "source_kind": kind,
                "command": install_command(uv, source),
                **before,
            }
            return emit(payload, EXIT_OK)

        install(uv, source, args.timeout)
        after = inspect_installed(uv)
        installed = after["cli_version"] or after["uv_tool_version"]
        payload = {
            "expected": version,
            "installed": installed,
            "previous": current,
            "action": "reinstalled" if current else "installed",
            "source": source,
            "source_kind": kind,
            **after,
        }
        if not after["on_path"]:
            payload["path_hint"] = (
                "the uv tool folder is not on PATH: call the CLI by the full path in `exe`, "
                "or run `uv tool update-shell` and open a new terminal"
            )
        if after["cli_version"] is None:
            payload["hint"] = "the package is installed but `babeldub --version` failed; see cli_error"
            return emit(payload, EXIT_ERROR)
        if installed != version:
            payload["hint"] = (
                f"installed {installed}, expected {version}: pass --source with the matching runtime"
            )
            return emit(payload, EXIT_ENV)
        payload["next"] = "babeldub doctor"
        return emit(payload, EXIT_OK)
    except BootstrapError as err:
        payload = {"expected": version, "error": str(err), **err.extra}
        if err.hint:
            payload["hint"] = err.hint
        return emit(payload, err.code)
    except Exception as exc:  # keep the one-JSON contract even on surprises
        return emit({"expected": version, "error": f"{type(exc).__name__}: {exc}"}, EXIT_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
