"""Safety checks before BabelDub touches a game folder.

* Is the game running? A process whose executable lives inside the game folder means the
  game (or its launcher / crash handler) has the files open: on Windows the swap would fail
  half-way, elsewhere the running game would keep stale data.
* Is the game protected by an anti-cheat? Modified files can get the player banned, so
  install refuses unless the user explicitly passes --force for an offline game.

Process detection: psutil when it is importable; otherwise on Windows one PowerShell
`Get-Process -Name <the game's exe names>` call that also returns paths (falling back to
`tasklist /FO CSV` when PowerShell is unavailable), /proc on Linux, `ps` elsewhere.
Executables are only listed, never started.
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
from pathlib import Path
from typing import Any

from babeldub.core.paths import IS_WINDOWS

# (lower-case name prefix of a file or folder, product)
ANTICHEAT_MARKERS: tuple[tuple[str, str], ...] = (
    ("easyanticheat", "EasyAntiCheat"),
    ("start_protected_game", "EasyAntiCheat"),
    ("eac_launcher", "EasyAntiCheat"),
    ("battleye", "BattlEye"),
    ("beservice", "BattlEye"),
    ("beclient", "BattlEye"),
    ("xigncode", "XIGNCODE3"),
    ("x3.xem", "XIGNCODE3"),
    ("gameguard", "nProtect GameGuard"),
    ("npggnt", "nProtect GameGuard"),
    ("pnkbstr", "PunkBuster"),
    ("pbsvc", "PunkBuster"),
    ("punkbuster", "PunkBuster"),
    ("equ8", "EQU8"),
    ("mhyprot", "mhyprot"),
    ("ace-base", "Tencent ACE"),
    ("anticheatexpert", "Tencent ACE"),
    ("denuvo-anti-cheat", "Denuvo Anti-Cheat"),
    ("denuvoanticheat", "Denuvo Anti-Cheat"),
    ("hackshield", "AhnLab HackShield"),
)
# recon warnings are free text: match product names only ("no anti-cheat found" must not block)
ANTICHEAT_PRODUCTS = tuple(sorted({p.lower() for _, p in ANTICHEAT_MARKERS}))
SCAN_DEPTH = 3  # game root + two levels of sub-folders
RECON_KEYS = ("anticheat", "anti_cheat", "antiCheat", "anticheats")


# ---- anti-cheat ---------------------------------------------------------------------------
def _marker(name: str) -> str | None:
    low = name.lower()
    for prefix, product in ANTICHEAT_MARKERS:
        if low.startswith(prefix):
            return product
    return None


def _names(value: Any) -> list[str]:
    """Product names out of whatever shape recon stored: bool, str, list, dict."""
    if value is None or value is False:
        return []
    if value is True:
        return ["anti-cheat"]
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        if value.get("detected") is False:
            return []
        for key in ("names", "found", "list", "products", "items"):
            if key in value:
                return _names(value[key])
        for key in ("name", "product", "id"):
            if isinstance(value.get(key), str):
                return [value[key]]
        return ["anti-cheat"] if value.get("detected") else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_names(item))
        return out
    return []


def anticheat_from_recon(recon: dict[str, Any] | None) -> list[str]:
    if not isinstance(recon, dict):
        return []
    found: list[str] = []
    for key in RECON_KEYS:
        found.extend(_names(recon.get(key)))
    for item in recon.get("middleware") or []:
        for name in _names(item.get("name") if isinstance(item, dict) else item):
            if _marker(name) or any(w in name.lower() for w in ("anticheat", "anti-cheat")):
                found.append(name)
    for warning in recon.get("warnings") or []:
        text = (
            warning
            if isinstance(warning, str)
            else str(warning.get("message", ""))
            if isinstance(warning, dict)
            else ""
        )
        if any(product in text.lower() for product in ANTICHEAT_PRODUCTS):
            found.append(text[:120])
    return found


def anticheat_in_folder(game_dir: Path, depth: int = SCAN_DEPTH) -> list[tuple[str, str]]:
    """(product, relpath) for anti-cheat files/folders near the top of the game folder."""
    hits: list[tuple[str, str]] = []
    if not game_dir.is_dir():
        return hits
    base_parts = len(game_dir.parts)
    for dirpath, dirnames, filenames in os.walk(game_dir):
        level = len(Path(dirpath).parts) - base_parts
        for name in list(dirnames) + filenames:
            product = _marker(name)
            if product:
                rel = (Path(dirpath) / name).relative_to(game_dir).as_posix()
                hits.append((product, rel))
        if level + 1 >= depth:
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames if not _marker(d)]
    return hits


def detect_anticheat(game_dir: Path, recon: dict[str, Any] | None) -> list[dict[str, str]]:
    """Anti-cheat found by recon (recon.json) or by a shallow scan of the game folder."""
    found: dict[str, dict[str, str]] = {}
    for name in anticheat_from_recon(recon):
        found.setdefault(name.lower(), {"name": name, "source": "recon.json"})
    for product, rel in anticheat_in_folder(game_dir):
        found.setdefault(product.lower(), {"name": product, "source": rel})
    return list(found.values())


# ---- running game -------------------------------------------------------------------------
def _roots(game_dir: Path) -> tuple[str, ...]:
    raw = os.path.normcase(os.path.abspath(str(game_dir)))
    real = os.path.normcase(os.path.realpath(str(game_dir)))
    return tuple({raw.rstrip("\\/"), real.rstrip("\\/")})


def _inside(path: str, roots: tuple[str, ...]) -> bool:
    cand = os.path.normcase(os.path.abspath(path))
    return any(cand == root or cand.startswith(root + os.sep) for root in roots)


def _scan_psutil(roots: tuple[str, ...]) -> list[dict[str, Any]] | None:
    try:
        import psutil  # optional dependency
    except ImportError:
        return None
    hits: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        info = proc.info
        paths = [info["exe"]] if info.get("exe") else []
        if not IS_WINDOWS:  # Wine/Proton: the game exe shows up in the command line
            paths += [
                arg for arg in (info.get("cmdline") or []) if isinstance(arg, str) and os.path.isabs(arg)
            ]
        for path in paths:
            if _inside(path, roots):
                hits.append({"pid": info["pid"], "name": info.get("name"), "exe": path})
                break
    return hits


def parse_tasklist_csv(text: str) -> list[dict[str, Any]]:
    """Rows of `tasklist /FO CSV [/NH] [/V]`: image name in column 1, PID in column 2."""
    rows: list[dict[str, Any]] = []
    for rec in csv.reader(io.StringIO(text)):
        if len(rec) < 2:
            continue
        try:
            pid = int(rec[1].strip())
        except ValueError:
            continue  # header or an "INFO:" line
        rows.append({"name": rec[0].strip(), "pid": pid})
    return rows


def _game_exe_names(game_dir: Path) -> set[str]:
    names: set[str] = set()
    for _dirpath, _dirnames, filenames in os.walk(game_dir):
        names.update(f.lower() for f in filenames if f.lower().endswith(".exe"))
    return names


PS_SENTINEL = "BABELDUB_PS_OK"
PS_ROW = 'ForEach-Object { "$($_.Id)|$($_.ProcessName)|$($_.Path)" }'


def _powershell(script: str) -> list[str] | None:
    """Output lines of a PowerShell snippet; None when PowerShell is missing or did not finish."""
    full = f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; {script}; '{PS_SENTINEL}'; exit 0"
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", full],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line.strip() for line in proc.stdout.splitlines()]
    if PS_SENTINEL not in lines:
        return None
    return [line for line in lines if line and line != PS_SENTINEL]


def parse_ps_rows(lines: list[str]) -> list[dict[str, Any]]:
    """ "<pid>|<process name>|<path>" rows printed by PS_ROW (path is empty when access is denied)."""
    rows: list[dict[str, Any]] = []
    for line in lines:
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].isdigit():
            rows.append({"pid": int(parts[0]), "name": parts[1], "exe": parts[2] or None})
    return rows


def _ps_names(names: set[str]) -> str:
    quoted = ",".join("'" + n.removesuffix(".exe").replace("'", "''") + "'" for n in sorted(names))
    return f"@({quoted}) | ForEach-Object {{ [Management.Automation.WildcardPattern]::Escape($_) }}"


def _windows_by_name(names: set[str]) -> list[dict[str, Any]] | None:
    """Processes named like the game's executables, with paths, in one PowerShell call."""
    lines = _powershell(
        f"$n = {_ps_names(names)}; Get-Process -Name $n -ErrorAction SilentlyContinue | {PS_ROW}"
    )
    return None if lines is None else parse_ps_rows(lines)


def _windows_by_tasklist(names: set[str]) -> list[dict[str, Any]]:
    """Fallback without PowerShell process listing: tasklist names, then paths where available."""
    proc = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
        timeout=60,
    )
    if proc.returncode != 0:
        raise OSError(f"tasklist failed with code {proc.returncode}")
    candidates = [row for row in parse_tasklist_csv(proc.stdout) if row["name"].lower() in names]
    if not candidates:
        return []
    ids = ",".join(str(row["pid"]) for row in candidates)
    lines = _powershell(f"Get-Process -Id {ids} -ErrorAction SilentlyContinue | {PS_ROW}") or []
    paths = {row["pid"]: row["exe"] for row in parse_ps_rows(lines)}
    return [{"pid": row["pid"], "name": row["name"], "exe": paths.get(row["pid"])} for row in candidates]


def _scan_windows(game_dir: Path, roots: tuple[str, ...]) -> tuple[list[dict[str, Any]], str]:
    names = _game_exe_names(game_dir)
    if not names:
        return [], "powershell"
    rows = _windows_by_name(names)
    method = "powershell"
    if rows is None:
        rows, method = _windows_by_tasklist(names), "tasklist"
    hits: list[dict[str, Any]] = []
    for row in rows:
        if row["exe"] is None:  # same name, path hidden (elevated process): assume it is the game
            hits.append({**row, "confirmed": False})
        elif _inside(row["exe"], roots):
            hits.append(row)
    return hits, method


def _scan_proc(roots: tuple[str, ...]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        paths: list[str] = []
        try:
            paths.append(os.readlink(f"/proc/{entry.name}/exe"))
        except OSError:
            pass
        try:
            raw = Path(f"/proc/{entry.name}/cmdline").read_bytes()
            paths += [a for a in raw.decode("utf-8", "replace").split("\0") if a.startswith("/")]
        except OSError:
            pass
        for path in paths:
            if _inside(path, roots):
                hits.append({"pid": int(entry.name), "name": Path(path).name, "exe": path})
                break
    return hits


def _scan_ps(roots: tuple[str, ...]) -> list[dict[str, Any]]:
    proc = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=60)
    hits: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        pid_text, _, path = line.strip().partition(" ")
        path = path.strip()
        if pid_text.isdigit() and path.startswith("/") and _inside(path, roots):
            hits.append({"pid": int(pid_text), "name": Path(path).name, "exe": path})
    return hits


def check_running(game_dir: Path) -> dict[str, Any]:
    """{"processes": [{pid, name, exe}], "method": str, "error": str | None}.

    When no detection method works, processes is empty and error explains why; callers
    report that as a warning instead of blocking.
    """
    roots = _roots(game_dir)
    try:
        hits = _scan_psutil(roots)
        if hits is not None:
            return {"processes": hits, "method": "psutil", "error": None}
        if IS_WINDOWS:
            procs, method = _scan_windows(game_dir, roots)
            return {"processes": procs, "method": method, "error": None}
        if os.path.isdir("/proc"):
            return {"processes": _scan_proc(roots), "method": "proc", "error": None}
        return {"processes": _scan_ps(roots), "method": "ps", "error": None}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"processes": [], "method": "none", "error": f"{type(exc).__name__}: {exc}"}
