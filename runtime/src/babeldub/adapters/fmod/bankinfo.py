"""One parsed view of a bank (container + FSB5 headers + audio table + event names) and line addressing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from babeldub.adapters.fmod.bank import BankError, Container, open_container
from babeldub.adapters.fmod.fsb5 import Fsb5, Fsb5Error, Subsound, read_fsb5
from babeldub.adapters.fmod.meta import audio_table_fsb, programmer_keys
from babeldub.adapters.fmod.strings import read_strings_bank
from babeldub.core.output import EXIT_USAGE, BabelDubError

ADAPTER = "fmod"
_LINE_RE = re.compile(r"#fsb(\d+):(\d+)$")


@dataclass
class BankInfo:
    container: Container
    fsbs: list[Fsb5]
    audio_table: int | None = None  # index of the FSB behind the STBL audio table

    @property
    def path(self) -> Path:
        return self.container.path

    def subsong_base(self, k: int) -> int:
        """vgmstream numbering: subsounds of all FSBs before #k."""
        return sum(len(f.subsounds) for f in self.fsbs[:k])

    def subsong(self, k: int, index: int) -> int:
        return self.subsong_base(k) + index + 1

    def subsound(self, k: int, index: int) -> Subsound:
        return self.fsbs[k].subsounds[index]

    def read_data(self, k: int, sub: Subsound) -> bytes:
        loc = self.container.fsbs[k]
        return self.container.read(loc.offset + self.fsbs[k].data_start + sub.data_offset, sub.data_size)


def load_bank(path: Path) -> BankInfo:
    """Open a .bank/.fsb and parse every FSB5 header; raises BabelDubError for unusable files."""
    try:
        cont = open_container(path)
    except (BankError, OSError) as exc:
        raise BabelDubError(f"{Path(path).name}: {exc}", code=EXIT_USAGE) from exc
    if cont.encrypted:
        raise BabelDubError(
            f"{Path(path).name}: FMOD sample data is encrypted",
            code=EXIT_USAGE,
            hint="the game sets an FMOD bank encryption key (FMODStudioSettings in Unity); "
            "this bank is read-only for BabelDub v0.1 (support level C)",
        )
    fsbs: list[Fsb5] = []
    try:
        with cont.path.open("rb") as fh:
            for loc in cont.fsbs:
                fsb = read_fsb5(fh, loc.offset)
                if fsb.total_size != loc.size:
                    raise Fsb5Error(f"FSB #{loc.index}: header size {fsb.total_size} != SNDH size {loc.size}")
                fsbs.append(fsb)
    except Fsb5Error as exc:
        raise BabelDubError(f"{Path(path).name}: {exc}", code=EXIT_USAGE) from exc
    info = BankInfo(cont, fsbs)
    if cont.kind == "bank":
        info.audio_table = audio_table_fsb(cont, [len(f.subsounds) for f in fsbs])
    return info


def parse_line_id(line_id: str, relpath: str) -> tuple[int, int]:
    """'fmod:<relpath>#fsb1:0042' -> (1, 42); the relpath must match the asset."""
    prefix = f"{ADAPTER}:{relpath}#"
    m = _LINE_RE.search(line_id)
    if not line_id.startswith(prefix) or not m:
        raise BabelDubError(f"line id '{line_id}' does not belong to {relpath}", code=EXIT_USAGE)
    return int(m.group(1)), int(m.group(2))


@dataclass
class EventIndex:
    """Subsound name -> event path / audio-table key, from string banks and programmer instruments."""

    by_base: dict[str, list[str]] = field(default_factory=dict)
    keys_by_base: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, event_paths: list[str], table_keys: list[str]) -> EventIndex:
        idx = cls()
        for path in event_paths:
            idx.by_base.setdefault(path.rsplit("/", 1)[-1], []).append(path)
        for key in table_keys:
            idx.keys_by_base.setdefault(key.rsplit("/", 1)[-1], []).append(key)
        return idx

    def resolve(self, name: str) -> tuple[str | None, str | None, str | None]:
        """(event_path, table_key, source) for a subsound name."""
        keys = self.keys_by_base.get(name, [])
        paths = self.by_base.get(name, [])
        table_key = keys[0] if keys else None
        if paths:
            for p in paths:
                if p.split(":/", 1)[-1] in keys:
                    return p, p.split(":/", 1)[-1], "strings"
            return sorted(paths)[0], table_key, "strings"
        if table_key and "/" in table_key:
            return "event:/" + table_key, table_key, "programmer"
        return None, table_key, None


def strings_banks_near(game_dir: Path, relpath: str, listed: list[str] | None) -> list[Path]:
    if listed:
        return [game_dir / r for r in listed if (game_dir / r).exists()]
    folder = (game_dir / relpath).parent
    return sorted(folder.glob("*.strings.bank"))


def event_index(game_dir: Path, relpath: str, info: BankInfo, listed: list[str] | None) -> EventIndex:
    paths: list[str] = []
    for sb in strings_banks_near(game_dir, relpath, listed):
        try:
            paths.extend(e.path for e in read_strings_bank(sb) if e.path.startswith("event:/"))
        except BankError:
            continue
    keys = programmer_keys(info.container) if info.container.kind == "bank" else []
    return EventIndex.build(paths, keys)
