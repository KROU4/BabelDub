"""Registry of game folders known to BabelDub (BABELDUB_HOME/games.json).

The PreToolUse guard hook reads this file to block writes into a registered game folder
that do not go through `babeldub install|uninstall|reapply`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from babeldub.core.jsonl import now_iso, read_json, write_json
from babeldub.core.paths import babeldub_home


def registry_path() -> Path:
    return babeldub_home() / "games.json"


def load_registry() -> dict[str, Any]:
    data = read_json(registry_path(), default=None) or {}
    data.setdefault("games", [])
    return data


def register_game(game_dir: Path, slug: str, project_dir: Path) -> None:
    data = load_registry()
    key = str(game_dir.resolve())
    games = [g for g in data["games"] if g.get("game_dir") != key]
    games.append({"game_dir": key, "slug": slug, "project_dir": str(project_dir.resolve()), "registered": now_iso()})
    data["games"] = games
    write_json(registry_path(), data)


def set_active_project(project_dir: Path) -> None:
    write_json(babeldub_home() / "active.json", {"project": str(project_dir.resolve()), "ts": now_iso()})


def active_project() -> Path | None:
    data = read_json(babeldub_home() / "active.json", default=None)
    if data and data.get("project"):
        path = Path(data["project"])
        if (path / "project.json").exists():
            return path
    return None
