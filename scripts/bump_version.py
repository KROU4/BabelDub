#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Keep the BabelDub version identical in every manifest.

The list of places lives in .version-bump.json at the repository root. Each entry is one of:
  {"path": "x.json", "kind": "json", "key": "version"}               top-level (dotted) JSON key
  {"path": "x.toml", "kind": "regex", "pattern": "...(?P<version>...)"}  first match of a regex
  {"path": "skills/*/SKILL.md", "kind": "frontmatter", "key": "metadata.babeldub-cli"}
"optional": true lets a file be missing or lack the field (it is reported as skipped).
Paths may be globs. Files are edited in place: only the version characters change, so
formatting, key order and line endings stay as they were. The optional "review" list of globs
names prose files (skills, docs) whose remaining mentions of the old version are printed after
a bump for a human to check; they are never rewritten automatically.

Usage:
  python scripts/bump_version.py                 show the version of every file
  python scripts/bump_version.py --check         exit 1 if files disagree or a field is missing
  python scripts/bump_version.py --check --expect 0.2.0
  python scripts/bump_version.py 0.2.0           set an explicit version
  python scripts/bump_version.py patch|minor|major [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_NAME = ".version-bump.json"
BOM = chr(0xFEFF)  # UTF-8 byte order mark as a character
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$")
FRONTMATTER_RE = re.compile(r"\A(?:" + BOM + r")?---[ \t]*\r?\n(?P<body>.*?\r?\n)---[ \t]*(?:\r?\n|\Z)", re.S)


class BumpError(Exception):
    pass


@dataclass
class Location:
    path: Path
    rel: str
    kind: str
    version: str | None = None
    span: tuple[int, int] | None = None
    problem: str | None = None
    skipped: bool = False


@dataclass
class Report:
    locations: list[Location] = field(default_factory=list)

    @property
    def found(self) -> list[Location]:
        return [loc for loc in self.locations if loc.version is not None]

    @property
    def problems(self) -> list[Location]:
        return [loc for loc in self.locations if loc.problem and not loc.skipped]

    @property
    def versions(self) -> set[str]:
        return {loc.version for loc in self.found if loc.version}

    @property
    def version(self) -> str | None:
        """Canonical version: the first file of the config (the Claude plugin manifest)."""
        found = self.found
        return found[0].version if found else None


def read_text(path: Path) -> str:
    # newline="" keeps CRLF/LF exactly as on disk so spans map back to the same characters
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def load_config(root: Path) -> list[dict[str, Any]]:
    cfg_path = root / CONFIG_NAME
    if not cfg_path.exists():
        raise BumpError(f"{CONFIG_NAME} not found in {root}")
    data = json.loads(read_text(cfg_path))
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise BumpError(f"{CONFIG_NAME}: 'files' must be a non-empty list")
    for entry in files:
        if entry.get("kind") not in ("json", "regex", "frontmatter") or not entry.get("path"):
            raise BumpError(f"{CONFIG_NAME}: bad entry {entry!r}")
    return files


def _expand(root: Path, pattern: str) -> list[Path]:
    if any(ch in pattern for ch in "*?["):
        return sorted(p for p in root.glob(pattern) if p.is_file())
    path = root / pattern
    return [path] if path.exists() else []


def _json_location(loc: Location, text: str, key: str) -> None:
    try:
        data = json.loads(text.lstrip(BOM))
    except json.JSONDecodeError as exc:
        loc.problem = f"invalid JSON: {exc}"
        return
    node: Any = data
    parts = key.split(".")
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            loc.problem = f"key '{key}' not found"
            return
        node = node[part]
    if not isinstance(node, str):
        loc.problem = f"key '{key}' is not a string"
        return
    loc.version = node
    # first textual occurrence of "<last key>": "<value>"; verified after editing
    match = re.search('"' + re.escape(parts[-1]) + r'"\s*:\s*"(' + re.escape(node) + ')"', text)
    if match:
        loc.span = match.span(1)
    else:
        loc.problem = f"cannot locate '{key}' in the text"


def _regex_location(loc: Location, text: str, pattern: str) -> None:
    matches = list(re.finditer(pattern, text))
    if not matches:
        loc.problem = "pattern did not match"
        return
    match = matches[0]
    loc.version = match.group("version")
    loc.span = match.span("version")


def _frontmatter_location(loc: Location, text: str, key: str) -> None:
    fm = FRONTMATTER_RE.match(text)
    if not fm:
        loc.problem = "no YAML frontmatter"
        return
    parent, _, child = key.partition(".")
    if not child or "." in child:
        loc.problem = f"frontmatter key must be 'parent.child', got '{key}'"
        return
    parent_re = re.compile(re.escape(parent) + r"\s*:\s*(#.*)?$")
    child_re = re.compile(r"([ \t]+)" + re.escape(child) + r"\s*:\s*([\"']?)(?P<version>[^\"'\s#]+)\2")
    in_parent = False
    pos = fm.start("body")
    for line in fm.group("body").splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped and not stripped[0].isspace():
            in_parent = parent_re.match(stripped) is not None
        elif in_parent:
            m = child_re.match(stripped)
            if m:
                loc.version = m.group("version")
                loc.span = (pos + m.start("version"), pos + m.end("version"))
                return
        pos += len(line)
    loc.problem = f"frontmatter key '{key}' not found"


def collect(root: Path = ROOT) -> Report:
    report = Report()
    for entry in load_config(root):
        optional = bool(entry.get("optional"))
        paths = _expand(root, entry["path"])
        if not paths:
            report.locations.append(
                Location(root / entry["path"], entry["path"], entry["kind"], problem="file not found", skipped=optional)
            )
            continue
        for path in paths:
            rel = path.relative_to(root).as_posix()
            loc = Location(path, rel, entry["kind"])
            text = read_text(path)
            if entry["kind"] == "json":
                _json_location(loc, text, entry.get("key", "version"))
            elif entry["kind"] == "regex":
                _regex_location(loc, text, entry["pattern"])
            else:
                _frontmatter_location(loc, text, entry["key"])
            if loc.problem and optional and loc.version is None:
                loc.skipped = True
            report.locations.append(loc)
    return report


def next_version(current: str, part: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", current)
    if not m:
        raise BumpError(f"cannot parse current version '{current}'")
    major, minor, patch = (int(x) for x in m.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def apply_version(report: Report, new: str, dry_run: bool = False) -> list[str]:
    """Rewrite every found location to `new`. Returns the list of changed files."""
    if not VERSION_RE.match(new):
        raise BumpError(f"'{new}' is not a supported version (X.Y.Z or X.Y.ZrcN / aN / bN)")
    by_file: dict[Path, list[Location]] = {}
    for loc in report.found:
        if loc.span is None:
            raise BumpError(f"{loc.rel}: cannot edit ({loc.problem})")
        by_file.setdefault(loc.path, []).append(loc)
    changed: list[str] = []
    for path, locs in by_file.items():
        text = read_text(path)
        new_text = text
        for loc in sorted(locs, key=lambda item: item.span[0] if item.span else 0, reverse=True):
            assert loc.span is not None
            start, end = loc.span
            new_text = new_text[:start] + new + new_text[end:]
        if new_text == text:
            continue
        if locs[0].kind == "json":
            json.loads(new_text.lstrip(BOM))  # must still be valid JSON
        if not dry_run:
            write_text(path, new_text)
        changed.append(locs[0].rel)
    return changed


def managed_lines(report: Report) -> set[str]:
    """`file:line` of every version field this script edits itself."""
    places: set[str] = set()
    for loc in report.found:
        if loc.span is not None:
            line = read_text(loc.path)[: loc.span[0]].count("\n") + 1
            places.add(f"{loc.rel}:{line}")
    return places


def stale_mentions(root: Path, old: str, skip: set[str] | None = None) -> list[str]:
    """`file:line` places from the config's "review" globs that still mention `old` in prose.

    These are not rewritten automatically (examples and history may mention old versions on
    purpose); the bump prints them so a human can decide. `skip` excludes managed fields.
    """
    skip = skip or set()
    data = json.loads(read_text(root / CONFIG_NAME))
    globs = data.get("review", [])
    if not isinstance(globs, list):
        raise BumpError(f"{CONFIG_NAME}: 'review' must be a list of globs")
    needle = re.compile(r"(?<![\w.])" + re.escape(old) + r"(?![\w.])")
    seen: set[Path] = set()
    hits: list[str] = []
    for pattern in globs:
        for path in sorted(root.glob(pattern)):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            rel = path.relative_to(root).as_posix()
            for number, line in enumerate(read_text(path).splitlines(), 1):
                place = f"{rel}:{number}"
                if place not in skip and needle.search(line):
                    hits.append(place)
    return hits


def check(report: Report, expect: str | None = None) -> list[str]:
    errors = [f"{loc.rel}: {loc.problem}" for loc in report.problems]
    versions = report.versions
    if len(versions) > 1:
        detail = ", ".join(f"{loc.rel}={loc.version}" for loc in report.found)
        errors.append(f"versions differ: {detail}")
    if not versions:
        errors.append("no version found in any file")
    if expect is not None and versions and versions != {expect}:
        errors.append(f"expected {expect}, found {', '.join(sorted(versions))}")
    return errors


def print_table(report: Report) -> None:
    width = max((len(loc.rel) for loc in report.locations), default=10)
    for loc in report.locations:
        if loc.version is not None:
            status = loc.version
        elif loc.skipped:
            status = f"skipped ({loc.problem})"
        else:
            status = f"ERROR: {loc.problem}"
        print(f"  {loc.rel.ljust(width)}  {status}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synchronise the BabelDub version across all manifests.")
    parser.add_argument("version", nargs="?", help="new version X.Y.Z, or patch | minor | major")
    parser.add_argument("--check", action="store_true", help="only verify that all files agree")
    parser.add_argument("--expect", help="with --check: also require this exact version (e.g. from a git tag)")
    parser.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        report = collect(args.root)
        if args.check or args.version is None:
            expect = args.expect.lstrip("v") if args.expect else None
            errors = check(report, expect)
            print_table(report)
            if errors:
                for err in errors:
                    print(f"error: {err}", file=sys.stderr)
                return 1
            print(f"ok: version {report.version} in {len(report.found)} places")
            return 0

        current = report.version
        if current is None:
            raise BumpError("no current version found")
        new = next_version(current, args.version) if args.version in ("patch", "minor", "major") else args.version
        new = new.lstrip("v")
        if report.problems:
            raise BumpError("; ".join(f"{loc.rel}: {loc.problem}" for loc in report.problems))
        changed = apply_version(report, new, dry_run=args.dry_run)
        verb = "would change" if args.dry_run else "changed"
        print(f"{current} -> {new}: {verb} {len(changed)} file(s)")
        for rel in changed:
            print(f"  {rel}")
        if not args.dry_run:
            after = check(collect(args.root), new)
            if after:
                for err in after:
                    print(f"error after bump: {err}", file=sys.stderr)
                return 1
        stale = stale_mentions(args.root, current, managed_lines(report)) if current != new else []
        if stale:
            print(f"review {len(stale)} prose mention(s) of {current} (not changed automatically):")
            for place in stale[:30]:
                print(f"  {place}")
            if len(stale) > 30:
                print(f"  ... +{len(stale) - 30} more")
        if not args.dry_run:
            print(f"next: add a '## [{new}]' section to CHANGELOG.md, commit, tag v{new}")
        return 0
    except BumpError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
