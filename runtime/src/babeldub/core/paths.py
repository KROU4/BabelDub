"""Where BabelDub keeps its data.

Everything heavy (engine venvs, model weights, external tools, projects) lives under
BABELDUB_HOME, never inside the plugin folder (it is replaced on update) and never
inside the game folder (read-only until `babeldub install`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"


def babeldub_home() -> Path:
    """~/.babeldub unless BABELDUB_HOME is set.

    Not %LOCALAPPDATA%: packaged (MSIX) hosts such as the Claude desktop app silently
    redirect writes there into a private per-app folder, so models downloaded from an
    agent session would be invisible to the user's other terminals and agent hosts.
    """
    env = os.environ.get("BABELDUB_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".babeldub"


def legacy_homes() -> list[Path]:
    """Default locations used before v0.1.0 final; doctor offers to migrate them."""
    homes = []
    if IS_WINDOWS and os.environ.get("LOCALAPPDATA"):
        homes.append(Path(os.environ["LOCALAPPDATA"]) / "BabelDub")
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    homes.append(Path(xdg) / "babeldub")
    return homes


def envs_dir() -> Path:
    return babeldub_home() / "envs"


def models_dir() -> Path:
    return babeldub_home() / "models"


def tools_dir() -> Path:
    return babeldub_home() / "tools"


def projects_dir() -> Path:
    return babeldub_home() / "projects"


def hf_home() -> Path:
    return babeldub_home() / "hf-cache"


def env_python(env_name: str) -> Path:
    """Interpreter of an engine venv created by `babeldub setup`."""
    root = envs_dir() / env_name
    return root / "Scripts" / "python.exe" if IS_WINDOWS else root / "bin" / "python"


def package_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def workers_dir() -> Path:
    """Standalone worker scripts executed inside engine venvs (they never import babeldub)."""
    return package_dir() / "workers"


def data_dir() -> Path:
    """Read-only data shipped with the package (model catalog, engine signatures)."""
    return package_dir() / "data"
