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
    # merge_threshold: a voice cluster this similar (centroid cosine) to a named character is that character
    "cast": {"policy": "main", "list": [], "main_share": 0.7, "merge_threshold": 0.75},
    "translation": {"mode": "auto"},
    # engine: auto = voxcpm on an NVIDIA GPU when its env exists, else qwen3tts-fast (CUDA) / qwen3tts / chatterbox
    # ref_strategy: auto = tags for voxcpm (long original timbre reference plus a delivery control text
    # "(shouting, fast pace)" from the agent's `delivery` or the tags measured on the original), else
    # line/char: "line" = the original line itself is the voice prompt (timbre AND delivery), "char" = best
    # clean reference of the character; bootstrap (explicit) = long reference, then a target-language prompt
    # built from the character's first accepted dubs.
    # tags_reference: main = the calm/main long reference, bucket = the nearest arousal bucket's.
    # delivery_tags: on = agent delivery > measured tags, measured = ignore the agent's, off = no control text.
    # best_of: candidates per line (different seeds); best_of_mode auto = score when the qc env exists (every
    # candidate is ranked by prosody, MOS, length and voice similarity), else the closest speech length wins.
    # stress: on = homographs get a stress mark the engine reads (RUAccent in the qc env), off = plain text
    "tts": {
        "engine": "auto",
        "model": None,
        "tier": "auto",
        "ref_strategy": "auto",
        "line_ref_min_s": 1.6,
        "best_of": 3,
        "max_attempts": 3,
        "bootstrap_lines": 4,
        "bootstrap_min_accepted": 3,
        "best_of_mode": "auto",
        "stress": "off",
        "tags_reference": "main",
        "delivery_tags": "on",
    },
    # never slower (tempo_min 1.0): a short dub keeps its tempo, the rest is silence; a long one loses part of
    # its pauses, then speeds up to tempo_max (pitch kept); exact original length
    "post": {"tempo_min": 1.0, "tempo_max": 1.15, "tolerance": 0.03, "fixed_length_tolerance": 0.03, "true_peak_db": -1.0},
    # RVC timbre transfer (stage vc) for characters with a model trained by `babeldub vc train`
    "vc": {"epochs": 20, "minutes": 30.0, "index_rate": 0.5, "protect": 0.33, "f0_method": "rmvpe"},
    # min_sim_ratio: same-language dubs; min_sim_ratio_xlang: dub language != source audio language
    "qa": {
        "max_cer": 0.15,
        "min_sim_ratio": 0.75,
        "min_sim_ratio_xlang": 0.45,
        "min_sim_abs": 0.25,
        "min_identity_margin": -0.05,
        "max_duration_ratio": 1.35,
        "mos_drop": 1.0,
        "min_f0_range_ratio": 0.5,
    },
    "build": {"codec": "auto"},
    "install": {"backup": True},
}

# Settings whose v0.1 default was written into every project.json by `init`: an untouched old default is
# replaced with the current one once on load (the id is recorded in project.json `migrations`).
# (id, section, key, v0.1 default, current default)
SETTING_MIGRATIONS: tuple[tuple[str, str, str, Any, Any], ...] = (
    ("post.tempo_min.v0.2", "post", "tempo_min", 0.93, 1.0),  # v0.1 slowed short dubs down
    ("post.tempo_max.v0.2", "post", "tempo_max", 1.2, 1.15),
)


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
        project = cls(root=root, data=data)
        project.migrate()
        return project

    def migrate(self) -> list[dict[str, Any]]:
        """Replace untouched v0.1 defaults (SETTING_MIGRATIONS) with the current ones, once per project.
        Each change is recorded in project.json `migrations` and logged to events.jsonl (stage `project`)."""
        if not self.data:
            return []
        done = {m.get("id") for m in self.data.get("migrations") or [] if isinstance(m, dict)}
        changed: list[dict[str, Any]] = []
        for mig_id, section, key, old, new in SETTING_MIGRATIONS:
            node = self.data.get(section)
            if mig_id in done or not isinstance(node, dict) or key not in node:
                continue
            value = node[key]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(float(value) - old) < 1e-9:
                node[key] = new
                changed.append({"id": mig_id, "setting": f"{section}.{key}", "old": old, "new": new, "ts": now_iso()})
        if changed:
            self.data["migrations"] = [*(self.data.get("migrations") or []), *changed]
            self.save()
            self.log_event("project", status="migrated", migrations=[
                {k: m[k] for k in ("setting", "old", "new")} for m in changed])
        return changed

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
