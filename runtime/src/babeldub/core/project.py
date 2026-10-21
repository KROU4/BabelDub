"""A localization project: one game + one target language, stored under BABELDUB_HOME/projects/<slug>."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from babeldub import __version__
from babeldub.core import layout
from babeldub.core.games import active_project
from babeldub.core.jsonl import append_jsonl, now_iso, read_json, write_json
from babeldub.core.output import EXIT_USAGE, BabelDubError
from babeldub.core.paths import projects_dir

PROJECT_SCHEMA = "babeldub.project/1"

DEFAULT_SETTINGS: dict[str, Any] = {
    # GPU-first: "auto" runs every neural workload on the NVIDIA GPU when one is present
    "compute": {"device": "auto"},
    "cast": {"policy": "main", "list": [], "main_share": 0.7},
    "translation": {"mode": "auto"},
    # ref_strategy: "line" = the original line itself is the voice prompt (carries timbre AND the
    # emotion/delivery of that exact line); "char" = best clean reference of the character;
    # "auto" = line when the original is long enough and has a source transcript, else char.
    "tts": {
        "engine": "qwen3tts",
        "model": None,
        "tier": "auto",
        "ref_strategy": "auto",
        "line_ref_min_s": 1.6,
        "best_of": 1,
        "max_attempts": 3,
    },
    "post": {"max_stretch": 0.12, "fixed_length_tolerance": 0.03, "true_peak_db": -1.0},
    "qa": {"max_cer": 0.15, "min_sim_ratio": 0.75, "max_duration_ratio": 1.35},
    "build": {"codec": "auto"},
    "install": {"backup": True},
}


@dataclass
class Project:
    root: Path
    data: dict[str, Any] = field(default_factory=dict)

    # ---- loading -------------------------------------------------------
    @classmethod
    def resolve_dir(cls, project_arg: str | None = None) -> Path:
        candidates: list[Path] = []
        if project_arg:
            p = Path(project_arg).expanduser()
            candidates.append(p if p.is_absolute() or p.exists() else projects_dir() / project_arg)
        elif os.environ.get("BABELDUB_PROJECT"):
            candidates.append(Path(os.environ["BABELDUB_PROJECT"]).expanduser())
        else:
            active = active_project()
            if active:
                candidates.append(active)
            candidates.append(Path.cwd())
        for cand in candidates:
            if (cand / layout.PROJECT_JSON).exists():
                return cand
        raise BabelDubError(
            "no BabelDub project found",
            code=EXIT_USAGE,
            hint="run `babeldub init <game_dir> --lang <code>` or pass --project <dir|slug>",
        )

    @classmethod
    def load(cls, project_arg: str | None = None) -> Project:
        root = cls.resolve_dir(project_arg)
        data = read_json(root / layout.PROJECT_JSON, default={})
        return cls(root=root, data=data)

    @classmethod
    def create(cls, root: Path, data: dict[str, Any]) -> Project:
        merged = {"_schema": PROJECT_SCHEMA, "cli_version": __version__, "created": now_iso()}
        for key, value in DEFAULT_SETTINGS.items():
            merged[key] = {**value, **data.get(key, {})} if isinstance(value, dict) else data.get(key, value)
        for key, value in data.items():
            merged.setdefault(key, value)
        project = cls(root=root, data=merged)
        project.save()
        return project

    # ---- state ---------------------------------------------------------
    def save(self) -> None:
        write_json(self.root / layout.PROJECT_JSON, self.data)

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    @property
    def slug(self) -> str:
        return self.data["slug"]

    @property
    def game_dir(self) -> Path:
        return Path(self.data["game_dir"])

    @property
    def target_lang(self) -> str:
        return self.data["target_lang"]

    def setting(self, section: str, key: str, default: Any = None) -> Any:
        return self.data.get(section, {}).get(key, default)

    def progress(self) -> dict[str, Any]:
        return read_json(self.path(layout.PROGRESS), default={}) or {}

    def mark_stage(self, stage: str, status: str, **summary: Any) -> None:
        """status: running | done | attention | failed"""
        prog = self.progress()
        prog[stage] = {"status": status, "updated": now_iso(), **summary}
        write_json(self.path(layout.PROGRESS), prog)
        self.log_event(stage, status=status, **summary)

    def require_stage(self, stage: str) -> None:
        state = self.progress().get(stage, {}).get("status")
        if state not in ("done", "attention"):
            raise BabelDubError(
                f"stage '{stage}' has not been completed yet",
                code=EXIT_USAGE,
                hint=f"run `babeldub {stage}` first (see `babeldub status`)",
            )

    def log_event(self, stage: str, **fields: Any) -> None:
        append_jsonl(self.path(layout.EVENTS), [{"ts": now_iso(), "stage": stage, **fields}])

    def rel(self, path: Path) -> str:
        """Project-relative POSIX path for storing in manifests."""
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(path)
