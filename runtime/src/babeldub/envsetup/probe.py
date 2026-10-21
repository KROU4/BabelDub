"""Read-only probes of the machine: external tools, NVIDIA GPU, disk space, engine venvs."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from babeldub.core.paths import IS_WINDOWS, babeldub_home, legacy_homes
from babeldub.core.tools import find_tool
from babeldub.envsetup.catalog import ENV_MARKER, EngineSpec

REQUIRED_TOOLS = ["uv", "ffmpeg", "ffprobe"]
OPTIONAL_TOOLS = ["uvx", "vgmstream-cli", "rubberband", "git"]
_VERSION_ARGS: dict[str, list[str]] = {
    "uv": ["--version"],
    "uvx": ["--version"],
    "ffmpeg": ["-hide_banner", "-version"],
    "ffprobe": ["-hide_banner", "-version"],
    "vgmstream-cli": ["-V"],
    "rubberband": ["--version"],
    "git": ["--version"],
}


def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def parse_version(name: str, output: str) -> str | None:
    text = output.strip()
    if not text:
        return None
    if name == "vgmstream-cli" and text.startswith("{"):
        try:
            return str(json.loads(text).get("version"))
        except (json.JSONDecodeError, AttributeError):
            pass
    first = text.splitlines()[0].strip()
    for prefix in ("ffmpeg version ", "ffprobe version ", "git version ", "uv ", "uvx ", "Rubber Band "):
        if first.startswith(prefix):
            return first[len(prefix) :].split()[0]
    return first[:60]


def tool_info(name: str, timeout: float = 15) -> dict[str, Any]:
    path = find_tool(name)
    if path is None:
        return {"found": False}
    code, out = _run([str(path), *_VERSION_ARGS.get(name, ["--version"])], timeout)
    info: dict[str, Any] = {"found": True, "path": str(path), "version": parse_version(name, out)}
    if code not in (0, 1):  # several tools print the version and exit 1 without an input file
        info["warning"] = out.strip().splitlines()[-1][:120] if out.strip() else f"exit code {code}"
    return info


def tools_report() -> dict[str, dict[str, Any]]:
    names = REQUIRED_TOOLS + OPTIONAL_TOOLS
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        results = list(pool.map(tool_info, names))
    return dict(zip(names, results, strict=True))


def parse_nvidia_smi(output: str) -> list[dict[str, Any]]:
    """Rows of `--query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader[,nounits]`."""

    def mib(value: str) -> float | None:
        value = value.strip().replace("MiB", "").strip()
        try:
            return float(value)
        except ValueError:
            return None

    gpus: list[dict[str, Any]] = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        total, used = mib(parts[1]), mib(parts[2])
        if total is None:
            continue
        gpus.append(
            {
                "name": parts[0],
                "vram_gb": round(total / 1024, 1),
                "used_gb": round(used / 1024, 1) if used is not None else None,
                "driver": parts[3],
            }
        )
    return gpus


def gpu_report(timeout: float = 20) -> list[dict[str, Any]]:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return []
    code, out = _run(
        [exe, "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"], timeout
    )
    return parse_nvidia_smi(out) if code == 0 else []


def free_space_gb(path: Path) -> float | None:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return round(shutil.disk_usage(probe).free / 1024**3, 1)
    except OSError:
        return None


def check_imports(python: Path, modules: list[str], timeout: float = 180) -> tuple[bool, str | None]:
    """Import modules in an engine venv (fresh process, so a broken env never affects the CLI)."""
    if not python.exists():
        return False, "venv python not found"
    if not modules:
        return True, None
    code = "import " + ", ".join(modules) + "; print('BABELDUB_IMPORT_OK')"
    try:
        proc = subprocess.run(
            [str(python), "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    # packages print warnings (SoX, flash-attn) to stdout/stderr: only the sentinel and exit code count
    if proc.returncode == 0 and "BABELDUB_IMPORT_OK" in (proc.stdout or ""):
        return True, None
    lines = [ln for ln in (proc.stderr or proc.stdout or "").strip().splitlines() if ln.strip()]
    return False, (lines[-1][:200] if lines else f"exit code {proc.returncode}")


def env_report(engine: EngineSpec, import_check: bool = True, timeout: float = 180) -> dict[str, Any]:
    python = engine.python_path()
    env_dir = engine.env_dir()
    if python is None or env_dir is None:
        return {"status": "builtin"}
    if not python.exists():
        return {"status": "missing"}
    row: dict[str, Any] = {"status": "present", "marker": (env_dir / ENV_MARKER).exists()}
    if import_check:
        ok, err = check_imports(python, engine.imports, timeout)
        row["status"] = "ok" if ok else "broken"
        if err:
            row["error"] = err
    return row


def envs_report(engines: list[EngineSpec], import_check: bool = True) -> dict[str, dict[str, Any]]:
    if not engines:
        return {}
    with ThreadPoolExecutor(max_workers=len(engines)) as pool:
        rows = list(pool.map(lambda e: env_report(e, import_check), engines))
    return {e.name: r for e, r in zip(engines, rows, strict=True)}


def system_info() -> dict[str, Any]:
    return {
        "os": platform.platform(terse=True),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "executable": sys.executable,
    }


def home_warnings(home: Path | None = None) -> list[str]:
    """Point at data left in pre-release default locations (%LOCALAPPDATA%, XDG data dir).

    Those locations were dropped because packaged hosts (the Claude desktop app is MSIX)
    virtualise %LOCALAPPDATA%: data written there from an agent session is invisible to
    other terminals. Data found there is reported so the user can move it.
    """
    home = home or babeldub_home()
    warnings: list[str] = []
    candidates = list(legacy_homes())
    local = os.environ.get("LOCALAPPDATA")
    if IS_WINDOWS and local and (Path(local) / "Packages").is_dir():
        for pkg in (Path(local) / "Packages").glob("Claude_*"):
            candidates.append(pkg / "LocalCache" / "Local" / "BabelDub")
    for legacy in candidates:
        try:
            if legacy.resolve() == home.resolve() or not (legacy / "envs").is_dir():
                continue
        except OSError:
            continue
        warnings.append(
            f"found BabelDub data from an older default location: {legacy}; move its envs/, models/, "
            f"tools/ and projects/ into {home} (or set BABELDUB_HOME={legacy}) to reuse it"
        )
    return warnings
