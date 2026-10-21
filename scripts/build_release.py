#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML>=6.0"]
# ///
"""Build the BabelDub release archives.

dist/
  babeldub-plugin-<ver>.zip   the whole plugin in one top folder `babeldub/`:
                              claude.ai / Cowork "Upload plugin", `claude --plugin-dir <zip>`,
                              manual install for Codex / Gemini after unpacking
  babeldub-skills-<ver>.zip   `babeldub-skills-<ver>/skills/<name>/` plain skill folders and
                              `babeldub-skills-<ver>/claude-ai/<name>.zip`, one zip per skill,
                              because claude.ai / Claude Desktop / the Skills API take exactly one
                              skill per upload (`<name>/SKILL.md` inside the zip)
  babeldub-kit-<ver>.zip      `BabelDub-Workspace/`: a folder to unpack next to a game and open
                              with any agent (AGENTS.md, .agents/skills, .claude/skills, runtime/)
  SHA256SUMS                  checksums of the three zips
  RELEASE_NOTES.md            the CHANGELOG section of this version plus asset descriptions

Archives are deterministic: sorted entries, fixed timestamps (SOURCE_DATE_EPOCH, else the last
commit time), normalised permissions, no symlinks, no dev junk.

Usage: python scripts/build_release.py [--out DIR] [--no-checks]
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import io
import os
import re
import stat
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bump_version  # noqa: E402  (sibling script)

PLUGIN_NAME = "babeldub"
KIT_DIR = "BabelDub-Workspace"

# directories excluded anywhere in the tree
JUNK_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "node_modules", ".idea", ".vscode", ".omc", ".tox", ".eggs",
}
# directories excluded only at the repository root (dev-only or build output)
ROOT_ONLY_DIRS = {"dist", "build", ".github", "evals"}
JUNK_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
JUNK_SUFFIXES = {".pyc", ".pyo", ".zip", ".log"}

CLAUDE_AI_MAX_FILES = 5000
CLAUDE_AI_MAX_BYTES = 200 * 1024 * 1024
SKILL_API_MAX_BYTES = 30 * 1024 * 1024  # Skills API: total uncompressed upload size
GEMINI_PLATFORM_PREFIXES = ("darwin", "linux", "win32")


class ReleaseError(Exception):
    pass


@dataclass
class Entry:
    arcname: str
    source: Path | None = None
    data: bytes | None = None
    executable: bool = False


# ---------------------------------------------------------------- file selection


def _git_files(root: Path) -> list[str] | None:
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        ).stdout.strip()
        if Path(top).resolve() != root.resolve():
            return None
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        return None
    return [name for name in out.split("\0") if name]


def is_junk(relpath: str) -> bool:
    parts = Path(relpath).parts
    if not parts:
        return True
    if parts[0] in ROOT_ONLY_DIRS and len(parts) > 1:
        return True
    if any(part in JUNK_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    return name in JUNK_FILES or Path(name).suffix.lower() in JUNK_SUFFIXES


def repo_files(root: Path, warnings: list[str]) -> list[str]:
    """POSIX paths (relative to root) that belong to the published plugin."""
    names = _git_files(root)
    if names is None:
        names = [p.relative_to(root).as_posix() for p in root.rglob("*") if not p.is_dir()]
    result: list[str] = []
    for name in sorted(set(names)):
        if is_junk(name):
            continue
        path = root / name
        if path.is_symlink():
            warnings.append(f"skipped symlink: {name}")
            continue
        if not path.is_file():
            continue  # deleted in the working tree but still in the index
        if any(parent.is_symlink() for parent in path.relative_to(root).parents if str(parent) != "."):
            warnings.append(f"skipped file under a symlinked folder: {name}")
            continue
        result.append(name)
    return result


def under(files: list[str], prefix: str) -> list[str]:
    prefix = prefix.rstrip("/") + "/"
    return [f for f in files if f.startswith(prefix)]


# ---------------------------------------------------------------- zip writing


def source_epoch(root: Path) -> int:
    env = os.environ.get("SOURCE_DATE_EPOCH")
    if env and env.isdigit():
        return int(env)
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%ct"], capture_output=True, text=True, check=True
        ).stdout.strip()
        if out.isdigit():
            return int(out)
    except (OSError, subprocess.CalledProcessError):
        pass
    now = time.gmtime()  # no commits yet: today's date, so rebuilds on the same day are identical
    return calendar.timegm((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0))


def _zip_time(epoch: int) -> tuple[int, int, int, int, int, int]:
    t = time.gmtime(max(epoch, 315532800))  # zip cannot store dates before 1980
    return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


def _write_entries(zf: zipfile.ZipFile, entries: list[Entry], epoch: int, label: str) -> None:
    seen: set[str] = set()
    date_time = _zip_time(epoch)
    for entry in sorted(entries, key=lambda e: e.arcname):
        if entry.arcname in seen:
            raise ReleaseError(f"{label}: duplicate entry {entry.arcname}")
        seen.add(entry.arcname)
        if entry.data is not None:
            data = entry.data
        elif entry.source is not None:
            data = entry.source.read_bytes()
        else:
            raise ReleaseError(f"{label}: entry {entry.arcname} has no content")
        info = zipfile.ZipInfo(entry.arcname, date_time=date_time)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3  # unix, so the permission bits below are honoured
        info.external_attr = (stat.S_IFREG | (0o755 if entry.executable else 0o644)) << 16
        zf.writestr(info, data, compresslevel=9)


def write_zip(path: Path, entries: list[Entry], epoch: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        _write_entries(zf, entries, epoch, path.name)
    tmp.replace(path)


def zip_bytes(entries: list[Entry], epoch: int, label: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        _write_entries(zf, entries, epoch, label)
    return buf.getvalue()


def file_entries(root: Path, files: list[str], strip: str, prefix: str) -> list[Entry]:
    entries = []
    for name in files:
        tail = name[len(strip):] if strip and name.startswith(strip) else name
        entries.append(Entry(arcname=f"{prefix}{tail}", source=root / name, executable=name.endswith(".sh")))
    return entries


# ---------------------------------------------------------------- content


def skill_names(files: list[str]) -> list[str]:
    names = sorted({f.split("/")[1] for f in under(files, "skills") if f.count("/") == 2 and f.endswith("/SKILL.md")})
    if not names:
        raise ReleaseError("no skills/*/SKILL.md found")
    return names


def changelog_section(root: Path, version: str) -> str:
    path = root / "CHANGELOG.md"
    if not path.exists():
        raise ReleaseError("CHANGELOG.md not found")
    text = path.read_text(encoding="utf-8")
    heading = re.compile(r"^##\s+\[?v?" + re.escape(version) + r"\]?(?:\s|$).*$", re.M)
    m = heading.search(text)
    if not m:
        raise ReleaseError(f"CHANGELOG.md has no '## [{version}]' section")
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    body = rest[: nxt.start()] if nxt else rest
    body = re.sub(r"^\[[^\]]+\]:\s*\S+\s*$", "", body, flags=re.M)  # drop link reference definitions
    return body.strip()


def release_notes(version: str, changes: str) -> str:
    return f"""# BabelDub {version}

{changes}

## Release files

| File | What it is for |
|---|---|
| `babeldub-plugin-{version}.zip` | The whole plugin in one `babeldub/` folder. claude.ai and Claude Desktop: Customize → Plugins → Upload plugin. Claude Code without the marketplace: `claude --plugin-dir babeldub-plugin-{version}.zip`. Codex and Gemini CLI: unpack and install from the folder |
| `babeldub-skills-{version}.zip` | Skills only. `skills/` holds folders to copy into `~/.agents/skills`, `~/.claude/skills` and similar. `claude-ai/` holds **one zip per skill**: claude.ai, Claude Desktop and the Skills API accept exactly one skill per upload (`<name>/SKILL.md` inside the archive). Start with `babeldub-localize.zip` |
| `babeldub-kit-{version}.zip` | A ready `BabelDub-Workspace/` folder: unpack it **next to** the game (not inside it) and open any agent in it. Contains `AGENTS.md`, skills for every host, the CLI source and `README-KIT.md` |
| `SHA256SUMS` | Archive checksums: `sha256sum -c SHA256SUMS` |

Models and environments are not in the archives: on first run `babeldub setup` downloads them into `BABELDUB_HOME` (default `~/.babeldub`) and shows their licenses. When an NVIDIA GPU is present, all neural compute runs on it.

## Install

- Claude Code: `/plugin marketplace add KROU4/BabelDub`, then `/plugin install babeldub@babeldub`
- Codex CLI: `codex plugin marketplace add KROU4/BabelDub --ref v{version}`, then `codex plugin add babeldub@babeldub`
- Gemini CLI: `gemini extensions install https://github.com/KROU4/BabelDub --ref v{version}`
- Other hosts and web chats: [docs/HOSTS.md](https://github.com/KROU4/BabelDub/blob/v{version}/docs/HOSTS.md)
- Supported engines: [docs/ENGINES.md](https://github.com/KROU4/BabelDub/blob/v{version}/docs/ENGINES.md); models and hardware: [docs/MODELS.md](https://github.com/KROU4/BabelDub/blob/v{version}/docs/MODELS.md)
"""


KIT_AGENTS_MD = """# BabelDub workspace

This folder is a BabelDub workspace: AI localization and dubbing of a video game **in the voices of
its original characters**. It is meant to sit next to the game folder (not inside it), for example
`D:\\Games\\MyGame` and `D:\\Games\\BabelDub-Workspace`.

Always talk to the user in the user's language.

## What to do

When the user asks to translate, localize, dub or re-voice a game, or to roll a localization back
(for example "localize this game", "dub the voices", "переведи игру", "сделай русскую озвучку",
"doblar el juego"), use the `babeldub-localize` skill and follow it step by step. Stage skills: {skills}.
Skills live in `.agents/skills/` (Codex, Gemini CLI, Antigravity, Cursor, Copilot, OpenCode) and
`.claude/skills/` (Claude Code); both copies are identical.

Ask the user only what the skill asks for (target language, which characters to re-voice) and pick
the documented defaults for everything else.

## The main rule: dubbing, not text-to-speech

Every translated line must sound like the original character: same timbre, emotion, pace and
delivery, only in the target language (lip movement is not rebuilt). By default the voice prompt for
each line is the original line itself with its source text (`tts.ref_strategy=line`); short, noisy or
untranscribed lines use the character's best clean reference (`char`). QA compares the synthesized
voice with the original character and sends drifted voices back for regeneration.

## The `babeldub` CLI

The skills drive the `babeldub` CLI. Its source is in `./runtime` of this workspace. If
`babeldub --version` fails, install it from here (uv is required):

- Windows: `powershell -ExecutionPolicy Bypass -File .\\install.ps1`
- Linux: `bash ./install.sh`

When the `babeldub-doctor` skill installs the CLI, pass `--source <this workspace>/runtime` to its
bootstrap script so it installs from this folder instead of GitHub.

Every command prints exactly one JSON object. Exit codes: 0 done, 1 error, 2 bad arguments or a
missing previous stage, 3 done but needs a decision, 4 environment missing (`babeldub doctor`, then
`babeldub setup`). Environments, model weights and projects live in `BABELDUB_HOME` (default
`~/.babeldub`, which is `C:\\Users\\<user>\\.babeldub` on Windows), never in this folder.

GPU-first: when an NVIDIA GPU is present, all neural compute (TTS, ASR, speaker embeddings) runs on
it (`compute.device=auto`).

## Safety rules

1. The game folder is read-only until `babeldub install`. Never edit, overwrite, move or delete game
   files yourself; rollback is `babeldub uninstall`.
2. Never run the game's executables or load its libraries.
3. Do not edit the canonical manifests of a project; write only the agent files a skill names
   (`*.agent.jsonl`, `translate/batches/<id>.out.jsonl`) and let the CLI merge them.
4. The result is for personal use. See `LEGAL.md` before sharing anything.

## Hosts

- Claude Code: subagents for the pipeline roles are in `.claude/agents/`.
- Other hosts have no Claude subagents: perform each role in the main session using
  `babeldub-localize/references/roles/*.md`.
"""

KIT_README = """# BabelDub workspace (kit) {version}

BabelDub translates and re-voices games **in the voices of their original characters**: the same
timbre, emotion and delivery, only in your language. This folder is a ready workspace for any AI
agent (Claude Code, Codex, Gemini CLI, Antigravity, Cursor, Copilot, OpenCode).

## What is inside

| Path | Purpose |
|---|---|
| `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` | instructions for the agent (the same content for different hosts) |
| `.agents/skills/` | skills for Codex, Gemini CLI, Antigravity, Cursor, Copilot, OpenCode |
| `.claude/skills/`, `.claude/agents/` | skills and subagents for Claude Code |
| `runtime/` | source of the `babeldub` CLI |
| `install.ps1`, `install.sh` | install the CLI from `runtime/` and check the environment |
| `LEGAL.md`, `THIRD_PARTY_NOTICES.md`, `LICENSE` | terms of use and licenses |

## Getting started

1. Unpack the archive **next to** the game folder, not inside it. For example: `D:\\Games\\MyGame` and
   `D:\\Games\\BabelDub-Workspace`. This keeps the game files clean, and the Steam integrity check
   does not touch the workspace.
2. Install [uv](https://docs.astral.sh/uv/) if you do not have it yet:
   - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. Install the CLI from this folder:
   - Windows: `powershell -ExecutionPolicy Bypass -File .\\install.ps1`
   - Linux: `bash ./install.sh`

   The script installs `babeldub` with `uv tool install` and runs `babeldub doctor`. Exit code 4
   from doctor means the environment is not ready yet: the agent will offer `babeldub setup` itself
   (it downloads the engines and models and shows their licenses).
4. Open your agent in this folder (`claude`, `codex`, `gemini`, `agy`, Cursor, VS Code with Copilot)
   and ask in any language, for example: "Dub the game in ..\\MyGame into Russian" or
   "Переозвучь игру из ..\\MyGame на русский". The agent asks two questions (the language and which
   characters to re-voice) and then works on its own.

## Good to know

- The voice work needs an NVIDIA GPU with 8+ GB of VRAM. When a GPU is present, all neural compute
  runs on it; the CPU works, but very slowly.
- Models, environments and projects take tens of gigabytes and live in `BABELDUB_HOME`
  (default `~/.babeldub`, which is `C:\\Users\\<user>\\.babeldub` on Windows).
  To move them to another drive, set the `BABELDUB_HOME` environment variable.
- Game files change only through the install command, with a backup. Rollback: ask the agent to
  "roll back the localization" or run `babeldub uninstall`.
- Cursor, Copilot and OpenCode read both `.agents/skills` and `.claude/skills`, so the skills may
  appear twice. They are the same files; this does not affect the work.
- Claude Code picks up the subagents from `.claude/agents/` and the skills from `.claude/skills/`
  after you trust the folder. If you prefer the plugin, install it from the `KROU4/BabelDub`
  marketplace (see https://github.com/KROU4/BabelDub/blob/main/docs/HOSTS.md).

## Responsible use

Use BabelDub only with legally purchased games and only for personal use or non-commercial fan
translations. Do not distribute game files or synthesized actor voices. Details in `LEGAL.md`.
"""


def build_plugin_entries(root: Path, files: list[str]) -> list[Entry]:
    return file_entries(root, files, "", f"{PLUGIN_NAME}/")


def build_skills_entries(root: Path, files: list[str], skills: list[str], version: str, epoch: int) -> list[Entry]:
    top = f"babeldub-skills-{version}/"
    entries = file_entries(root, under(files, "skills"), "", top)
    for name in skills:
        # one skill per zip, the skill folder at the zip root: `<name>/SKILL.md`
        single = file_entries(root, under(files, f"skills/{name}"), "skills/", "")
        entries.append(Entry(arcname=f"{top}claude-ai/{name}.zip", data=zip_bytes(single, epoch, f"{name}.zip")))
    readme = f"""# BabelDub skills {version}

- `skills/<name>/`: skill folders following the Agent Skills standard. Copy them into your agent's
  skills folder: `~/.agents/skills/` (Codex, Gemini CLI, Antigravity, Cursor, Copilot, OpenCode),
  `~/.claude/skills/` (Claude Code), `.github/skills/` (Copilot in a repository).
- `claude-ai/<name>.zip`: one archive per skill to upload to claude.ai and Claude Desktop
  (Customize → Skills) or to the Skills API. Each archive contains a `<name>/` folder with `SKILL.md`.
  Start with `babeldub-localize.zip`, then upload the rest.

Web chats have no access to the game folder or to a GPU, so only translation of exported line
batches is available there. The full pipeline (extraction, voice cloning, build) works only with a
local agent. More: https://github.com/KROU4/BabelDub/blob/v{version}/docs/HOSTS.md
"""
    entries.append(Entry(arcname=f"{top}README.md", data=readme.encode("utf-8")))
    return entries


def build_kit_entries(root: Path, files: list[str], skills: list[str], version: str) -> list[Entry]:
    top = f"{KIT_DIR}/"
    entries: list[Entry] = []
    skill_files = under(files, "skills")
    entries += file_entries(root, skill_files, "skills/", f"{top}.agents/skills/")
    entries += file_entries(root, skill_files, "skills/", f"{top}.claude/skills/")
    entries += file_entries(root, under(files, "claude/agents"), "claude/agents/", f"{top}.claude/agents/")
    runtime = [f for f in under(files, "runtime") if not f.startswith("runtime/tests/")]
    entries += file_entries(root, runtime, "", top)
    for src, dst in (("scripts/install.ps1", "install.ps1"), ("scripts/install.sh", "install.sh"),
                     ("LICENSE", "LICENSE"), ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
                     ("docs/LEGAL.md", "LEGAL.md")):
        if src not in files:
            raise ReleaseError(f"kit needs {src}, which is missing")
        entries.append(Entry(arcname=top + dst, source=root / src, executable=dst.endswith(".sh")))
    agents_md = KIT_AGENTS_MD.format(skills=", ".join(f"`{s}`" for s in skills if s != "babeldub-localize"))
    entries.append(Entry(arcname=f"{top}AGENTS.md", data=agents_md.encode("utf-8")))
    entries.append(Entry(arcname=f"{top}GEMINI.md", data=agents_md.encode("utf-8")))
    entries.append(Entry(arcname=f"{top}CLAUDE.md", data=b"@AGENTS.md\n"))
    entries.append(Entry(arcname=f"{top}README-KIT.md", data=KIT_README.format(version=version).encode("utf-8")))
    return entries


# ---------------------------------------------------------------- verification


def _names(zf: zipfile.ZipFile) -> list[str]:
    names = zf.namelist()
    for info in zf.infolist():
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ReleaseError(f"symlink entry {info.filename}")
        if info.filename.startswith("/") or ".." in Path(info.filename).parts or "\\" in info.filename:
            raise ReleaseError(f"unsafe path {info.filename}")
    return names


def verify_archives(out: Path, version: str, skills: list[str]) -> list[str]:
    report: list[str] = []
    plugin = out / f"babeldub-plugin-{version}.zip"
    with zipfile.ZipFile(plugin) as zf:
        names = _names(zf)
        if any(not n.startswith(f"{PLUGIN_NAME}/") for n in names):
            raise ReleaseError("plugin zip must have exactly one top folder babeldub/")
        required = [".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", ".codex-plugin/plugin.json",
                    "plugin.json", "gemini-extension.json", "GEMINI.md", "runtime/pyproject.toml"]
        required += [f"skills/{s}/SKILL.md" for s in skills]
        for req in required:
            if f"{PLUGIN_NAME}/{req}" not in names:
                raise ReleaseError(f"plugin zip lacks {req}")
        bad = [n for n in names if n.startswith(f"{PLUGIN_NAME}/bin/")
               or any(p in JUNK_DIRS for p in Path(n).parts)]
        if bad:
            raise ReleaseError(f"plugin zip contains forbidden paths: {bad[:5]}")
        if len(names) > CLAUDE_AI_MAX_FILES:
            raise ReleaseError(f"plugin zip has {len(names)} files (claude.ai limit {CLAUDE_AI_MAX_FILES})")
        if plugin.stat().st_size > CLAUDE_AI_MAX_BYTES:
            raise ReleaseError("plugin zip is over 200 MB (claude.ai limit)")
        report.append(f"{plugin.name}: {len(names)} files, single top folder {PLUGIN_NAME}/")

    skills_zip = out / f"babeldub-skills-{version}.zip"
    top = f"babeldub-skills-{version}/"
    with zipfile.ZipFile(skills_zip) as zf:
        names = _names(zf)
        for s in skills:
            if f"{top}skills/{s}/SKILL.md" not in names:
                raise ReleaseError(f"skills zip lacks skills/{s}/SKILL.md")
            inner_name = f"{top}claude-ai/{s}.zip"
            if inner_name not in names:
                raise ReleaseError(f"skills zip lacks claude-ai/{s}.zip")
            with zipfile.ZipFile(io.BytesIO(zf.read(inner_name))) as inner:
                inner_names = _names(inner)
                if f"{s}/SKILL.md" not in inner_names or any(not n.startswith(f"{s}/") for n in inner_names):
                    raise ReleaseError(f"claude-ai/{s}.zip must contain only the folder {s}/ with SKILL.md")
                total = sum(i.file_size for i in inner.infolist())
                if total > SKILL_API_MAX_BYTES:
                    raise ReleaseError(f"claude-ai/{s}.zip unpacks to {total} bytes (Skills API limit 30 MB)")
        report.append(f"{skills_zip.name}: {len(skills)} skill folders + {len(skills)} single-skill zips")

    kit = out / f"babeldub-kit-{version}.zip"
    with zipfile.ZipFile(kit) as zf:
        names = _names(zf)
        ktop = f"{KIT_DIR}/"
        required = ["AGENTS.md", "CLAUDE.md", "GEMINI.md", "README-KIT.md", "install.ps1", "install.sh",
                    "runtime/pyproject.toml", "LEGAL.md", "LICENSE"]
        required += [f".agents/skills/{s}/SKILL.md" for s in skills]
        required += [f".claude/skills/{s}/SKILL.md" for s in skills]
        for req in required:
            if ktop + req not in names:
                raise ReleaseError(f"kit zip lacks {req}")
        if any(not n.startswith(ktop) for n in names):
            raise ReleaseError(f"kit zip must have exactly one top folder {KIT_DIR}/")
        report.append(f"{kit.name}: {len(names)} files in {KIT_DIR}/")

    assets = [plugin.name, skills_zip.name, kit.name, "SHA256SUMS"]
    if len(assets) < 2 or any(a.lower().startswith(GEMINI_PLATFORM_PREFIXES) for a in assets):
        raise ReleaseError("Gemini CLI needs 2+ release assets without darwin/linux/win32 prefixes")
    return report


# ---------------------------------------------------------------- main


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_checks(root: Path) -> None:
    report = bump_version.collect(root)
    errors = bump_version.check(report)
    if errors:
        raise ReleaseError("version check failed: " + "; ".join(errors))
    import validate_skills  # sibling script; needs PyYAML

    res, _ = validate_skills.validate(root)
    if res.errors:
        shown = "\n  ".join(res.errors[:20])
        raise ReleaseError(f"validate_skills found {len(res.errors)} error(s):\n  {shown}")


def build(root: Path, out: Path, checks: bool = True) -> dict[str, object]:
    root = root.resolve()
    if checks:
        run_checks(root)
    version = bump_version.collect(root).version
    if not version:
        raise ReleaseError("cannot determine the version (.claude-plugin/plugin.json)")
    warnings: list[str] = []
    files = repo_files(root, warnings)
    skills = skill_names(files)
    notes = release_notes(version, changelog_section(root, version))
    epoch = source_epoch(root)

    out.mkdir(parents=True, exist_ok=True)
    artifacts = {
        f"babeldub-plugin-{version}.zip": build_plugin_entries(root, files),
        f"babeldub-skills-{version}.zip": build_skills_entries(root, files, skills, version, epoch),
        f"babeldub-kit-{version}.zip": build_kit_entries(root, files, skills, version),
    }
    for name, entries in artifacts.items():
        write_zip(out / name, entries, epoch)
    sums = "".join(f"{sha256(out / name)}  {name}\n" for name in artifacts)
    (out / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
    (out / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8", newline="\n")
    report = verify_archives(out, version, skills)
    return {"version": version, "out": str(out), "skills": skills, "artifacts": list(artifacts),
            "checks": report, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build BabelDub release archives into dist/.")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output folder (default: dist/)")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--no-checks", action="store_true", help="skip version and skill validation")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        result = build(args.root, args.out.resolve(), checks=not args.no_checks)
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out = Path(str(result["out"]))
    print(f"BabelDub {result['version']} -> {out}")
    for name in result["artifacts"]:  # type: ignore[union-attr]
        path = out / name
        print(f"  {name}  {path.stat().st_size / 1024:.0f} KB")
    for line in result["checks"]:  # type: ignore[union-attr]
        print(f"  ok: {line}")
    for warning in result["warnings"]:  # type: ignore[union-attr]
        print(f"  warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
