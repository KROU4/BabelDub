"""Adapter interface: one adapter per container format / middleware.

An adapter knows how to find its assets in a game folder, list the voice lines inside,
decode them to WAV, read text tables, rebuild the container with replacement audio and
verify the result. Commands (scan, extract, link, build, verify) only talk to adapters
through this interface, so adding an engine never touches the pipeline.

Support levels (shown to the user by `babeldub scan`):
  A - fully automatic extract + native rebuild without proprietary tools
  B - semi-automatic: needs a user-installed tool or a mod loader
  C - read-only here; repacking follows the playbook in skills/babeldub-pack/references
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

from babeldub.core.output import EXIT_USAGE, BabelDubError


@dataclass
class Asset:
    asset_id: str  # "<adapter>:<relpath>"
    adapter: str
    relpath: str  # POSIX path relative to the game folder
    role: str  # "voice" | "text" | "audio" (music/sfx-only container) | "other"
    size: int = 0
    sha1: str | None = None
    locale: str | None = None  # e.g. "en-US" for Dialogue_en-US.bank, "ru" for a string table
    level: str = "C"  # support level, see module docstring
    info: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Asset:
        known = {k: row[k] for k in cls.__dataclass_fields__ if k in row}
        return cls(**known)


class Adapter:
    """Base class. Methods that a format cannot support raise BabelDubError(EXIT_USAGE)."""

    name = "base"
    level = "C"
    description = ""

    def discover(self, game_dir: Path) -> list[Asset]:
        """Cheap scan: find assets this adapter can handle (no decoding)."""
        return []

    def list_lines(self, game_dir: Path, asset: Asset) -> list[dict[str, Any]]:
        """Canonical line records for a voice/audio asset (see docs/dev/CONTRACT.md, lines.jsonl).

        Must fill: id, uid, adapter, asset_id, entry{key,sub,index,...}, kind, locale,
        audio{sr,ch,dur_s,codec}. Must not decode audio.
        """
        raise BabelDubError(f"{self.name}: listing lines is not supported", code=EXIT_USAGE)

    def extract_audio(
        self, game_dir: Path, asset: Asset, lines: list[dict[str, Any]], out_dir: Path
    ) -> list[dict[str, Any]]:
        """Decode the given lines to out_dir/<uid>.wav. Returns [{"id", "wav", "sr", "ch", "dur_s"}]."""
        raise BabelDubError(f"{self.name}: audio extraction is not supported", code=EXIT_USAGE)

    def read_texts(self, game_dir: Path, asset: Asset) -> list[dict[str, Any]]:
        """Text rows {key, table, locale, lang, text, source} for a text asset."""
        return []

    def build(
        self,
        game_dir: Path,
        asset: Asset,
        replacements: dict[str, Path],
        out_path: Path,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """Write a rebuilt copy of the asset to out_path, replacing audio of the given line ids.

        Lines not in `replacements` keep their original audio. Returns a summary with at least
        {"out": str, "replaced": int, "kept": int, "codec": str}.
        """
        raise BabelDubError(
            f"{self.name}: native rebuild is not supported (level {self.level})",
            code=EXIT_USAGE,
            hint="follow skills/babeldub-pack/references/formats for a manual or semi-automatic path",
        )

    def verify(self, game_dir: Path, asset: Asset, built: Path, options: dict[str, Any]) -> dict[str, Any]:
        """Check a rebuilt container. Returns {"ok": bool, "checks": [...], "problems": [...]}."""
        return {"ok": built.exists(), "checks": ["exists"], "problems": [] if built.exists() else ["missing"]}


# name -> "module:Class"; imported lazily so a broken optional dependency never breaks the CLI
REGISTRY: dict[str, str] = {
    "fmod": "babeldub.adapters.fmod.adapter:FmodAdapter",
    "unity-localization": "babeldub.adapters.unity.adapter:UnityLocalizationAdapter",
    "wwise": "babeldub.adapters.wwise.adapter:WwiseAdapter",
    "loose": "babeldub.adapters.loose.adapter:LooseAudioAdapter",
}


def get_adapter(name: str) -> Adapter:
    target = REGISTRY.get(name)
    if not target:
        raise BabelDubError(f"unknown adapter '{name}'", code=EXIT_USAGE, known=sorted(REGISTRY))
    module_name, class_name = target.split(":")
    cls = getattr(import_module(module_name), class_name)
    return cls()


def all_adapters() -> list[Adapter]:
    return [get_adapter(name) for name in REGISTRY]
