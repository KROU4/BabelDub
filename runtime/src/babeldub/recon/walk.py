"""Bounded, cached walk of a game folder.

Games can hold hundreds of thousands of files; recon must stay fast. The walk is breadth-first
(shallow files first), stops at `max_depth` and `max_entries`, never follows symlinks and keeps
extension statistics. Adapters reuse the same index within one CLI run via `walk()`'s cache.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

MAX_DEPTH = 10
MAX_ENTRIES = 300_000
SKIP_DIRS = frozenset({".git", "__pycache__", "$recycle.bin", "system volume information"})


@dataclass
class FileEntry:
    rel: str  # POSIX relpath from the game folder
    size: int
    depth: int  # number of path components (1 = file in the game root)
    mtime_ns: int = 0

    @property
    def name(self) -> str:
        return self.rel.rsplit("/", 1)[-1]

    @property
    def ext(self) -> str:
        name = self.name
        return name[name.rfind(".") :].lower() if "." in name else ""

    @property
    def parent(self) -> str:
        return self.rel.rsplit("/", 1)[0] if "/" in self.rel else ""


@dataclass
class GameIndex:
    root: Path
    files: list[FileEntry] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    truncated: bool = False
    max_depth_hit: bool = False
    by_ext: dict[str, list[int]] = field(default_factory=dict)  # ext -> [count, bytes]
    _by_name: dict[str, list[FileEntry]] = field(default_factory=dict, repr=False)
    _dir_names: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def finalize(self) -> None:
        self._by_name.clear()
        self._dir_names.clear()
        for entry in self.files:
            self._by_name.setdefault(entry.name.lower(), []).append(entry)
        for rel in self.dirs:
            self._dir_names.setdefault(rel.rsplit("/", 1)[-1].lower(), []).append(rel)

    @property
    def total_bytes(self) -> int:
        return sum(v[1] for v in self.by_ext.values())

    def files_named(self, name: str) -> list[FileEntry]:
        return list(self._by_name.get(name.lower(), []))

    def files_matching_name(self, pattern: str) -> list[FileEntry]:
        pat = pattern.lower()
        if not any(ch in pat for ch in "*?["):
            return self.files_named(pat)
        out: list[FileEntry] = []
        for name, entries in self._by_name.items():
            if fnmatchcase(name, pat):
                out.extend(entries)
        return out

    def files_matching_path(self, pattern: str) -> list[FileEntry]:
        """Glob on the whole relpath; '*' does not cross '/', depth must match the pattern."""
        pat = pattern.lower().strip("/")
        depth = pat.count("/") + 1
        parts = pat.split("/")
        out: list[FileEntry] = []
        for entry in self.files:
            if entry.depth != depth:
                continue
            rel_parts = entry.rel.lower().split("/")
            if all(fnmatchcase(r, p) for r, p in zip(rel_parts, parts, strict=True)):
                out.append(entry)
        return out

    def dirs_named(self, name: str) -> list[str]:
        pat = name.lower()
        if not any(ch in pat for ch in "*?["):
            return list(self._dir_names.get(pat, []))
        out: list[str] = []
        for dname, rels in self._dir_names.items():
            if fnmatchcase(dname, pat):
                out.extend(rels)
        return out

    def dirs_matching_path(self, pattern: str) -> list[str]:
        pat = pattern.lower().strip("/")
        parts = pat.split("/")
        out: list[str] = []
        for rel in self.dirs:
            rel_parts = rel.lower().split("/")
            if len(rel_parts) == len(parts) and all(
                fnmatchcase(r, p) for r, p in zip(rel_parts, parts, strict=True)
            ):
                out.append(rel)
        return out

    def files_with_ext(self, *exts: str) -> list[FileEntry]:
        wanted = {e.lower() if e.startswith(".") else "." + e.lower() for e in exts}
        return [f for f in self.files if f.ext in wanted]

    def ext_stats(self, top: int = 25) -> list[dict[str, int | str]]:
        rows = sorted(self.by_ext.items(), key=lambda kv: (-kv[1][1], kv[0]))
        return [{"ext": ext or "(none)", "files": cnt, "bytes": size} for ext, (cnt, size) in rows[:top]]


def build_index(root: Path, max_depth: int = MAX_DEPTH, max_entries: int = MAX_ENTRIES) -> GameIndex:
    index = GameIndex(root=root)
    queue: deque[tuple[Path, str, int]] = deque([(root, "", 0)])
    entries = 0
    while queue:
        path, rel, depth = queue.popleft()
        try:
            with os.scandir(path) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for child in children:
            child_rel = f"{rel}/{child.name}" if rel else child.name
            try:
                if child.is_symlink():
                    continue
                if child.is_dir(follow_symlinks=False):
                    if child.name.lower() in SKIP_DIRS:
                        continue
                    index.dirs.append(child_rel)
                    if depth + 1 < max_depth:
                        queue.append((Path(child.path), child_rel, depth + 1))
                    else:
                        index.max_depth_hit = True
                    continue
                stat = child.stat(follow_symlinks=False)
            except OSError:
                continue
            entry = FileEntry(rel=child_rel, size=stat.st_size, depth=depth + 1, mtime_ns=stat.st_mtime_ns)
            index.files.append(entry)
            bucket = index.by_ext.setdefault(entry.ext, [0, 0])
            bucket[0] += 1
            bucket[1] += entry.size
            entries += 1
            if entries >= max_entries:
                index.truncated = True
                index.finalize()
                return index
    index.finalize()
    return index


_CACHE: dict[tuple[str, int, int], GameIndex] = {}


def walk(game_dir: Path, max_depth: int = MAX_DEPTH, max_entries: int = MAX_ENTRIES) -> GameIndex:
    """Cached bounded index of a game folder (call clear_cache() when the folder may have changed)."""
    key = (str(Path(game_dir).resolve()), max_depth, max_entries)
    index = _CACHE.get(key)
    if index is None:
        index = build_index(Path(game_dir), max_depth=max_depth, max_entries=max_entries)
        _CACHE[key] = index
    return index


def clear_cache() -> None:
    _CACHE.clear()
