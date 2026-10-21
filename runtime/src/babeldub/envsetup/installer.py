"""Installing engine venvs, model weights and external tools under BABELDUB_HOME.

Everything is idempotent: an engine venv whose marker matches the catalog spec and whose key modules
import is skipped, a model folder with weights (or our marker) is skipped, vgmstream with INSTALLED.json
is skipped. Long subprocess output goes to BABELDUB_HOME/logs/setup-<name>.log, never to stdout.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from babeldub import __version__
from babeldub.core.compute import detect_gpu
from babeldub.core.jsonl import now_iso, read_json, write_json
from babeldub.core.output import EXIT_ENV, EXIT_USAGE, BabelDubError
from babeldub.core.paths import IS_WINDOWS, babeldub_home, hf_home, tools_dir
from babeldub.core.tools import find_tool
from babeldub.envsetup.catalog import (
    ENV_MARKER,
    MODEL_MARKER,
    Catalog,
    EngineSpec,
    ModelSpec,
    is_model_downloaded,
    model_dir,
)
from babeldub.envsetup.probe import check_imports

ENGINE_STEP_TIMEOUT = 3600.0
MODEL_TIMEOUT = 4 * 3600.0
VGMSTREAM_API = "https://api.github.com/repos/vgmstream/vgmstream/releases/latest"
VGMSTREAM_ASSETS: dict[str, list[str]] = {
    "win32": ["vgmstream-win64.zip", "vgmstream-win.zip"],
    "linux": ["vgmstream-linux-cli.tar.gz", "vgmstream-linux.zip", "vgmstream-linux-cli.zip"],
}
_EXE = ".exe" if IS_WINDOWS else ""


def logs_dir() -> Path:
    return babeldub_home() / "logs"


def downloads_dir() -> Path:
    return babeldub_home() / "cache" / "downloads"


@dataclass
class PlanItem:
    kind: str  # engine | model | tool
    name: str
    action: str  # install | skip | adopt | blocked
    reason: str | None = None
    size_gb: float | None = None
    license: str | None = None
    commercial: bool | None = None
    warning: str | None = None
    commands: list[list[str]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        row: dict[str, Any] = {"kind": self.kind, "name": self.name, "action": self.action}
        for key in ("reason", "size_gb", "license", "commercial", "warning"):
            value = getattr(self, key)
            if value is not None:
                row[key] = value
        return row

    def detail(self) -> dict[str, Any]:
        row = self.summary()
        row["commands"] = [subprocess.list2cmdline(c) for c in self.commands]
        return row


# ---- subprocess helpers -------------------------------------------------------------------------


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("HF_HOME", str(hf_home()))
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _tail(path: Path, n: int = 8) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln[:240] for ln in lines if ln.strip()][-n:]


def run_logged(cmd: list[str], log_path: Path, timeout: float, cwd: Path | None = None) -> int:
    """Run a command with stdout+stderr appended to log_path; returns the exit code (-1: could not start)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n[{now_iso()}] $ {subprocess.list2cmdline(cmd)}\n")
        log.flush()
        try:
            proc = subprocess.run(
                cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout, env=child_env(),
                cwd=str(cwd) if cwd else None,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            log.write(f"\n[babeldub] timeout after {timeout:.0f}s\n")
            code = -2
        except OSError as exc:
            log.write(f"\n[babeldub] cannot start: {exc}\n")
            code = -1
    return code


# ---- engines ------------------------------------------------------------------------------------


def accel_for_tier(tier: str) -> str:
    """Torch build to install: CUDA whenever an NVIDIA GPU is visible (GPU-first, contract §1.6).

    The tier only picks model sizes. A small GPU (tier "cpu" by VRAM) still runs ASR, speaker
    embeddings and the 0.6B TTS far faster than the CPU, so it still gets CUDA wheels.
    """
    if detect_gpu().get("available"):
        return "cuda"
    return "cpu" if tier == "cpu" else "cuda"


def env_spec(engine: EngineSpec, accel: str) -> dict[str, Any]:
    torch = dict(engine.torch or {})
    if torch:
        torch["index"] = torch.get("index") if accel == "cuda" else torch.get("cpu_index") or "https://download.pytorch.org/whl/cpu"
        torch.pop("cpu_index", None)
    spec: dict[str, Any] = {
        "engine": engine.name,
        "python": engine.python,
        "accel": accel,
        "torch": torch or None,
        "packages": list(engine.packages),
        "gpu_packages": list(engine.gpu_packages) if accel == "cuda" else [],
    }
    if engine.git:  # the marker records the pinned checkout too
        spec["git"] = dict(engine.git)
        spec["requirements"] = engine.requirements
        spec["post_install"] = [list(c) for c in engine.post_install]
    return spec


FILTERED_REQUIREMENTS = "req-babeldub.txt"  # requirements of a git engine without torch/torchaudio
_TORCH_REQ = re.compile(r"^\s*(torch|torchaudio)\s*([=<>!~;\[]|$)", re.IGNORECASE)


def filter_requirements(src: Path, dst: Path) -> int:
    """Copy a requirements file without its torch/torchaudio lines (torch comes from the CUDA index)."""
    lines = src.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines if not _TORCH_REQ.match(ln)]
    dst.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    return len(lines) - len(kept)


def git_head(folder: Path) -> str | None:
    """Commit checked out in a git engine folder (None: no checkout or no git)."""
    git = find_tool("git")
    if git is None or not (folder / ".git").exists():
        return None
    try:
        proc = subprocess.run([str(git), "-C", str(folder), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def source_ready(engine: EngineSpec) -> bool:
    """A git engine's checkout is at the pinned commit (always True for a pip-only engine)."""
    folder = engine.source_dir()
    if folder is None or not engine.git:
        return True
    commit = str(engine.git.get("commit") or "")
    head = git_head(folder)
    return head is not None and (not commit or head == commit)


def engine_commands(engine: EngineSpec, accel: str, uv: str) -> list[list[str]]:
    """Install steps. Steps starting with "@" run inside the CLI: `@filter-requirements src dst`
    and `@cd <dir> <cmd...>` (a command run in that folder)."""
    env_dir, python = engine.env_dir(), engine.python_path()
    if env_dir is None or python is None:
        return []
    spec = env_spec(engine, accel)
    cmds: list[list[str]] = []
    source = engine.source_dir()
    if source is not None and engine.git:
        git = str(find_tool("git") or "git")
        commit = str(engine.git.get("commit") or "HEAD")
        cmds += [
            [git, "init", "--quiet", str(source)],
            [git, "-C", str(source), "fetch", "--depth", "1", str(engine.git["url"]), commit],
            [git, "-C", str(source), "checkout", "--force", "--detach", "FETCH_HEAD"],
        ]
    cmds.append([uv, "venv", str(env_dir), "--python", engine.python, "--allow-existing"])
    torch = spec["torch"]
    if torch:
        version = torch["version"]
        cmds.append(
            [uv, "pip", "install", "--python", str(python), f"torch=={version}", f"torchaudio=={version}",
             "--index-url", torch["index"]]
        )
    if source is not None and engine.requirements:
        filtered = source / FILTERED_REQUIREMENTS
        cmds.append(["@filter-requirements", str(source / engine.requirements), str(filtered)])
        cmds.append([uv, "pip", "install", "--python", str(python), "-r", str(filtered)])
    if engine.packages:
        cmds.append([uv, "pip", "install", "--python", str(python), *engine.packages])
    if spec["gpu_packages"]:
        cmds.append([uv, "pip", "install", "--python", str(python), *spec["gpu_packages"]])
    for step in engine.post_install:
        cmds.append(["@cd", str(source or env_dir), str(python), *step])
    return cmds


def run_step(cmd: list[str], log: Path, timeout: float) -> int:
    """One install step: a subprocess, or an "@" step handled here (see engine_commands)."""
    if cmd and cmd[0] == "@filter-requirements":
        try:
            dropped = filter_requirements(Path(cmd[1]), Path(cmd[2]))
        except OSError as exc:
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"\n[babeldub] cannot filter {cmd[1]}: {exc}\n")
            return -1
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{now_iso()}] filtered {cmd[1]} -> {cmd[2]} ({dropped} torch lines dropped)\n")
        return 0
    if cmd and cmd[0] == "@cd":
        return run_logged(cmd[2:], log, timeout, cwd=Path(cmd[1]))
    return run_logged(cmd, log, timeout)


def plan_engine(engine: EngineSpec, tier: str, force: bool = False, import_check: bool = True) -> PlanItem:
    item = PlanItem(kind="engine", name=engine.name, action="install", size_gb=engine.size_gb or None,
                    license=engine.license)
    python = engine.python_path()
    env_dir = engine.env_dir()
    if python is None or env_dir is None:
        item.action, item.reason, item.size_gb = "skip", "built-in engine, no venv", None
        return item
    if engine.cuda_only and tier == "cpu":
        item.action = "blocked"
        item.reason = "runs on CUDA only: not installed on the cpu tier" + (
            f" (`synth` uses {engine.fallback} instead)" if engine.fallback else "")
        return item
    accel = accel_for_tier(tier)
    if not force and python.exists():
        marker = read_json(env_dir / ENV_MARKER, default=None)
        wanted = env_spec(engine, accel)
        checkout = source_ready(engine)
        if marker and marker.get("spec") == wanted and checkout:
            if not import_check or check_imports(python, engine.imports)[0]:
                item.action, item.reason, item.size_gb = "skip", "installed", None
                return item
            item.reason = "venv exists but imports fail: reinstalling packages"
        elif marker is None and checkout and import_check and check_imports(python, engine.imports)[0]:
            item.action, item.reason, item.size_gb = "adopt", "existing venv imports fine; recording marker", None
            return item
        elif not checkout:
            item.reason = "source checkout missing or not at the pinned commit"
        elif marker is not None:
            item.reason = "catalog spec changed: updating venv"
    uv = find_tool("uv")
    if uv is None:
        item.action, item.reason = "blocked", "uv not found (https://docs.astral.sh/uv/)"
        return item
    if (engine.needs_git() or engine.git) and find_tool("git") is None:
        item.action, item.reason = "blocked", "git not found (needed for a git+ package)"
        return item
    item.commands = engine_commands(engine, accel, str(uv))
    return item


def _write_env_marker(engine: EngineSpec, tier: str, adopted: bool = False) -> None:
    env_dir = engine.env_dir()
    if env_dir is None:
        return
    write_json(
        env_dir / ENV_MARKER,
        {"spec": env_spec(engine, accel_for_tier(tier)), "installed": now_iso(), "adopted": adopted,
         "cli_version": __version__},
    )


def run_engine(item: PlanItem, engine: EngineSpec, tier: str) -> dict[str, Any]:
    log = logs_dir() / f"setup-{engine.name}.log"
    if item.action == "adopt":
        _write_env_marker(engine, tier, adopted=True)
        return {"name": engine.name, "kind": "engine", "status": "adopted"}
    for cmd in item.commands:
        code = run_step(cmd, log, ENGINE_STEP_TIMEOUT)
        if code != 0:
            return {"name": engine.name, "kind": "engine", "status": "failed", "error": f"exit code {code}",
                    "step": Path(cmd[0]).name + " " + " ".join(cmd[1:3]), "log": str(log), "tail": _tail(log, 5)}
    python = engine.python_path()
    if python is not None:
        ok, err = check_imports(python, engine.imports)
        if not ok:
            return {"name": engine.name, "kind": "engine", "status": "failed", "error": f"import check: {err}",
                    "log": str(log)}
    _write_env_marker(engine, tier)
    return {"name": engine.name, "kind": "engine", "status": "installed", "log": str(log)}


# ---- models -------------------------------------------------------------------------------------


def _hf_hub_available() -> bool:
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        return False
    return True


def model_command(spec: ModelSpec, dest: Path) -> list[str] | None:
    """`uvx --from huggingface_hub hf download <repo> [files...] --local-dir <dest>` (None without uvx)."""
    uvx = find_tool("uvx")
    if uvx is None:
        return None
    cmd = [str(uvx), "--from", "huggingface_hub", "hf", "download", spec.repo, *spec.include, "--local-dir", str(dest)]
    if spec.revision:
        cmd += ["--revision", spec.revision]
    return cmd


def plan_model(spec: ModelSpec, force: bool = False, accepted: set[str] | None = None) -> PlanItem:
    item = PlanItem(kind="model", name=spec.name, action="install", size_gb=spec.size_gb, license=spec.license,
                    commercial=spec.commercial, warning=spec.warning)
    if spec.opt_in and spec.name not in (accepted or set()):
        item.action = "blocked"
        item.reason = f"license {spec.license} needs explicit consent: --accept-license {spec.name}"
        return item
    dest = model_dir(spec)
    if not force and is_model_downloaded(spec, dest):
        item.action, item.reason, item.size_gb = "skip", "downloaded", None
        return item
    cmd = model_command(spec, dest)
    if cmd is not None:
        item.commands = [cmd]
    elif _hf_hub_available():
        item.reason = "uvx not found: using huggingface_hub in-process"
    else:
        item.action, item.reason = "blocked", "neither uvx nor huggingface_hub is available"
    return item


def _folder_size(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and ".cache" not in p.parts)


def run_model(item: PlanItem, spec: ModelSpec) -> dict[str, Any]:
    dest = model_dir(spec)
    log = logs_dir() / f"setup-model-{spec.name}.log"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / MODEL_MARKER).unlink(missing_ok=True)  # completeness is re-checked after the download
    if item.commands:
        code = run_logged(item.commands[0], log, MODEL_TIMEOUT)
        if code != 0:
            return {"name": spec.name, "kind": "model", "status": "failed", "error": f"exit code {code}",
                    "log": str(log), "tail": _tail(log, 5)}
    else:
        try:
            from huggingface_hub import snapshot_download

            os.environ.setdefault("HF_HOME", str(hf_home()))
            snapshot_download(repo_id=spec.repo, local_dir=str(dest), revision=spec.revision,
                              allow_patterns=spec.include or None)
        except Exception as exc:  # network/auth errors of any kind end up in the report
            return {"name": spec.name, "kind": "model", "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    if not is_model_downloaded(spec, dest):
        return {"name": spec.name, "kind": "model", "status": "failed", "error": "no weight files after download",
                "log": str(log)}
    write_json(
        dest / MODEL_MARKER,
        {"name": spec.name, "repo": spec.repo, "revision": spec.revision, "license": spec.license,
         "files": list(spec.include), "size_bytes": _folder_size(dest), "downloaded": now_iso()},
    )
    return {"name": spec.name, "kind": "model", "status": "installed", "dir": str(dest)}


# ---- vgmstream ----------------------------------------------------------------------------------


def vgmstream_dir() -> Path:
    return tools_dir() / "vgmstream"


def _http_get_json(url: str, timeout: float = 30) -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"babeldub/{__version__}"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: Path, timeout: float = 300) -> str:
    """Stream url to dest; returns sha256 hex."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": f"babeldub/{__version__}"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=timeout) as resp, tmp.open("wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            fh.write(chunk)
    os.replace(tmp, dest)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _platform_key(platform: str | None = None) -> str:
    platform = platform or sys.platform
    return "linux" if platform.startswith("linux") else platform


def pick_asset(release: dict[str, Any], platform: str | None = None) -> dict[str, Any]:
    platform = _platform_key(platform)
    wanted = VGMSTREAM_ASSETS.get(platform)
    if not wanted:
        raise BabelDubError(
            f"no prebuilt vgmstream-cli for platform '{platform}'",
            code=EXIT_ENV,
            hint="build vgmstream-cli from https://github.com/vgmstream/vgmstream and put it on PATH",
        )
    assets = {a.get("name"): a for a in release.get("assets", [])}
    for name in wanted:
        if name in assets:
            return assets[name]
    raise BabelDubError(
        f"release {release.get('tag_name')} has none of {wanted}",
        code=EXIT_ENV,
        hint="download vgmstream-cli manually from https://github.com/vgmstream/vgmstream/releases",
        assets=sorted(a for a in assets if a),
    )


def _inside(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def safe_extract(archive: Path, dest: Path) -> None:
    """Extract zip/tar.gz refusing absolute paths, `..` and links that escape dest."""
    dest.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                target = dest / info.filename
                if Path(info.filename).is_absolute() or not _inside(dest, target):
                    raise BabelDubError(f"unsafe path in archive: {info.filename}", code=EXIT_ENV)
            zf.extractall(dest)
        return
    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                target = dest / member.name
                if Path(member.name).is_absolute() or not _inside(dest, target):
                    raise BabelDubError(f"unsafe path in archive: {member.name}", code=EXIT_ENV)
                if (member.issym() or member.islnk()) and not _inside(dest, target.parent / member.linkname):
                    raise BabelDubError(f"unsafe link in archive: {member.name}", code=EXIT_ENV)
            if hasattr(tarfile, "data_filter"):
                tf.extractall(dest, filter="data")
            else:
                tf.extractall(dest)
        return
    raise BabelDubError(f"unknown archive format: {archive.name}", code=EXIT_ENV)


def installed_vgmstream() -> dict[str, Any] | None:
    info = read_json(vgmstream_dir() / "INSTALLED.json", default=None)
    if not info:
        return None
    exe = vgmstream_dir() / info.get("exe", "")
    return info if exe.is_file() else None


def plan_vgmstream(force: bool = False) -> PlanItem:
    item = PlanItem(kind="tool", name="vgmstream-cli", action="install", size_gb=0.01, license="ISC (vgmstream)")
    installed = installed_vgmstream()
    if installed and not force:
        item.action, item.reason, item.size_gb = "skip", f"installed {installed.get('version')}", None
        return item
    existing = find_tool("vgmstream-cli")
    if existing is not None and not force and not _inside(tools_dir(), existing):
        item.action, item.reason, item.size_gb = "skip", f"found on PATH: {existing}", None
        return item
    assets = VGMSTREAM_ASSETS.get(_platform_key())
    if not assets:
        item.action, item.reason = "blocked", f"no prebuilt vgmstream-cli for {sys.platform}: build it and put on PATH"
        return item
    item.reason = "latest GitHub release of vgmstream/vgmstream (" + " or ".join(assets) + ")"
    return item


def install_vgmstream(force: bool = False) -> dict[str, Any]:
    if not force and installed_vgmstream():
        return {"name": "vgmstream-cli", "kind": "tool", "status": "skipped"}
    release = _http_get_json(VGMSTREAM_API)
    asset = pick_asset(release)
    archive = downloads_dir() / asset["name"]
    sha = _download(asset["browser_download_url"], archive)
    final = vgmstream_dir()
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".vgmstream-", dir=final.parent))
    try:
        safe_extract(archive, staging)
        exes = sorted(staging.rglob("vgmstream-cli" + _EXE))
        if not exes:
            raise BabelDubError(f"vgmstream-cli{_EXE} not found in {asset['name']}", code=EXIT_ENV)
        exe = exes[0]
        if not IS_WINDOWS:
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        rel_exe = exe.relative_to(staging).as_posix()
        if final.exists():
            shutil.rmtree(final)
        os.replace(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    info = {
        "tool": "vgmstream-cli",
        "version": release.get("tag_name"),
        "published": release.get("published_at"),
        "asset": asset["name"],
        "url": asset["browser_download_url"],
        "sha256": sha,
        "size": archive.stat().st_size,
        "exe": rel_exe,
        "installed": now_iso(),
    }
    write_json(final / "INSTALLED.json", info)
    return {"name": "vgmstream-cli", "kind": "tool", "status": "installed", "version": info["version"],
            "sha256": sha, "exe": str(final / rel_exe)}


def run_tool(item: PlanItem, force: bool = False) -> dict[str, Any]:
    if item.name != "vgmstream-cli":
        raise BabelDubError(f"unknown tool '{item.name}'", code=EXIT_USAGE)
    try:
        return install_vgmstream(force=force)
    except BabelDubError as err:
        return {"name": item.name, "kind": "tool", "status": "failed", "error": str(err), "hint": err.hint}
    except OSError as exc:  # network errors (URLError is an OSError) and file errors
        return {"name": item.name, "kind": "tool", "status": "failed", "error": f"{type(exc).__name__}: {exc}"}


# ---- whole plan ---------------------------------------------------------------------------------


def build_plan(
    catalog: Catalog,
    engines: list[str],
    models: list[ModelSpec],
    tools: bool,
    tier: str,
    accepted: set[str] | None = None,
    force: bool = False,
    import_check: bool = True,
) -> list[PlanItem]:
    specs = [catalog.engine(name) for name in engines]
    items: list[PlanItem] = []
    if specs:  # import checks start a python per venv: run them side by side
        with ThreadPoolExecutor(max_workers=len(specs)) as pool:
            items = list(pool.map(lambda e: plan_engine(e, tier, force, import_check), specs))
    items += [plan_model(spec, force, accepted) for spec in models]
    if tools:
        items.append(plan_vgmstream(force))
    return items


def execute_plan(items: list[PlanItem], catalog: Catalog, tier: str, force: bool = False) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in items:
        if item.action in ("skip", "blocked"):
            continue
        if item.kind == "engine":
            results.append(run_engine(item, catalog.engine(item.name), tier))
        elif item.kind == "model":
            results.append(run_model(item, catalog.model(item.name)))
        else:
            results.append(run_tool(item, force))
    return results


def write_plan_file(items: list[PlanItem], meta: dict[str, Any]) -> Path:
    path = logs_dir() / "setup-plan.json"
    write_json(path, {**meta, "created": now_iso(), "items": [i.detail() for i in items]})
    return path
