#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML>=6.0"]
# ///
"""Repository hygiene and portability checks for BabelDub.

Skills (skills/*/SKILL.md) must stay inside the Agent Skills standard so that every host
(Claude Code, claude.ai, Codex/ChatGPT, Gemini, Cursor, Copilot, OpenCode) loads them:
  - frontmatter keys: name, description, license, compatibility, metadata only;
  - name == folder name, [a-z0-9-], at most 64 chars, no "claude"/"anthropic";
  - description 50..1024 chars, no XML tags; compatibility at most 500 chars;
  - metadata is a map of string -> string;
  - body at most 500 lines; relative links and references/... mentions point to real files;
  - strict YAML (PyYAML), no UTF-8 BOM.
Manifests and the tree:
  - every *.json is valid JSON without BOM; plugin manifests are consistent with the files;
  - no bin/ at the root (claude.ai and Cowork refuse such plugins), no CLAUDE.md at the root;
  - no files over 1 MB except assets/*.png; no game containers (*.bank, *.wem, *.bnk, *.pak ...).

Exit code 0 when clean, 1 when there are errors. Warnings never fail the run.
Usage: python scripts/validate_skills.py [--root DIR] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

ALLOWED_SKILL_KEYS = {"name", "description", "license", "compatibility", "metadata"}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
XML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9_-]*(?:\s[^<>]*)?/?>")
FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(?P<yaml>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
MD_LINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
MD_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REF_MENTION_RE = re.compile(r"(?<![\w./-])((?:\.\./[\w.-]+/)?references/[\w./-]*[\w-]\.(?:md|ya?ml|json|txt|csv|py))")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")

DESCRIPTION_MIN, DESCRIPTION_MAX = 50, 1024
COMPATIBILITY_MAX = 500
BODY_MAX_LINES = 500
NAME_MAX = 64
MAX_FILE_BYTES = 1024 * 1024

GAME_ASSET_EXT = {
    ".bank", ".wem", ".bnk", ".pak", ".fsb", ".pck", ".ucas", ".utoc",
    ".xwm", ".fuz", ".rpa", ".assets", ".ress", ".resource",
}
AUDIO_EXT = {".wav", ".ogg", ".mp3", ".flac", ".opus", ".m4a"}
TEXT_EXT = {
    ".md", ".json", ".py", ".toml", ".yml", ".yaml", ".txt", ".sh", ".ps1", ".cmd", ".bat",
    ".cfg", ".ini", ".svg", ".html", ".css", ".js", ".ts", ".csv", ".jsonl",
}
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", "dist", "build", ".omc", ".idea", ".vscode",
}
AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGINS_KEYS = {
    "$schema", "name", "version", "description", "author", "homepage", "repository", "license",
    "keywords", "extensions",
}
PLUGIN_NAME = "babeldub"


@dataclass
class Result:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")

    def count(self, what: str, n: int = 1) -> None:
        self.checked[what] = self.checked.get(what, 0) + n


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def list_files(root: Path) -> list[Path]:
    """Files that would be published: git tracked + untracked-not-ignored, else a filtered walk."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=True,
        )
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
        ).stdout.strip()
        if Path(top).resolve() == root.resolve():
            names = [n for n in proc.stdout.decode("utf-8").split("\0") if n]
            kept = [n for n in names if not any(part in SKIP_DIRS for part in Path(n).parts[:-1])]
            return sorted(p for p in (root / n for n in kept) if p.is_file() or p.is_symlink())
    except (OSError, subprocess.CalledProcessError):
        pass
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in parts[:-1]) or path.is_dir():
            continue
        out.append(path)
    return out


def read_utf8(path: Path, root: Path, res: Result) -> str | None:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        res.error(rel(path, root), "UTF-8 BOM (save without BOM: hosts like Gemini reject it)")
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        res.error(rel(path, root), f"not valid UTF-8 ({exc.reason} at byte {exc.start})")
        return None


def strip_code(text: str) -> str:
    """Drop fenced code blocks so example snippets are not treated as links."""
    out: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        m = FENCE_RE.match(line.strip())
        if m:
            marker = m.group(1)[0] * 3
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is None:
            out.append(line)
    return "\n".join(out)


def _load_yaml(text: str) -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment problem, not a repo problem
        raise SystemExit(
            "PyYAML is required: run `uv run scripts/validate_skills.py` or install PyYAML"
        ) from exc
    return yaml.safe_load(text)


def check_link_target(base: Path, target: str, root: Path, where: str, res: Result) -> Path | None:
    """Report a broken relative link; returns the resolved path of a checked link."""
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("${"):
        return None
    if any(ch in target for ch in "*{}<>$"):
        return None
    path = (base / target).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        res.error(where, f"link points outside the repository: {target}")
        return path
    if not path.exists():
        res.error(where, f"broken link: {target}")
    return path


def check_skill(skill_dir: Path, root: Path, res: Result) -> None:
    skill_md = skill_dir / "SKILL.md"
    where = rel(skill_md, root)
    if not skill_md.exists():
        res.error(rel(skill_dir, root), "folder without SKILL.md")
        return
    res.count("skills")
    text = read_utf8(skill_md, root, res)
    if text is None:
        return
    fm = FRONTMATTER_RE.match(text)
    if not fm:
        res.error(where, "missing YAML frontmatter (--- ... --- at the top)")
        return
    try:
        meta = _load_yaml(fm.group("yaml"))
    except Exception as exc:  # yaml.YAMLError and friends
        msg = str(exc).replace("\n", " ")
        res.error(where, f"frontmatter is not strict YAML ({msg}); quote values that contain ': '")
        return
    if not isinstance(meta, dict):
        res.error(where, "frontmatter must be a YAML mapping")
        return

    extra = sorted(set(meta) - ALLOWED_SKILL_KEYS)
    if extra:
        res.error(where, f"frontmatter keys outside the Agent Skills set: {', '.join(extra)} "
                         f"(allowed: {', '.join(sorted(ALLOWED_SKILL_KEYS))})")

    name = meta.get("name")
    if not isinstance(name, str) or not name:
        res.error(where, "name is required")
    else:
        if name != skill_dir.name:
            res.error(where, f"name '{name}' must equal the folder name '{skill_dir.name}'")
        if not NAME_RE.match(name):
            res.error(where, f"name '{name}' must match [a-z0-9]+(-[a-z0-9]+)*")
        if len(name) > NAME_MAX:
            res.error(where, f"name longer than {NAME_MAX} characters")
        if "claude" in name or "anthropic" in name:
            res.error(where, "name must not contain 'claude' or 'anthropic' (claude.ai rejects it)")

    desc = meta.get("description")
    if not isinstance(desc, str) or not desc.strip():
        res.error(where, "description is required")
    else:
        n = len(desc.strip())
        if not DESCRIPTION_MIN <= n <= DESCRIPTION_MAX:
            res.error(where, f"description is {n} characters, must be {DESCRIPTION_MIN}..{DESCRIPTION_MAX}")
        if XML_TAG_RE.search(desc):
            res.error(where, "description must not contain XML/HTML tags")

    if "license" in meta and not isinstance(meta["license"], str):
        res.error(where, "license must be a string")
    if "compatibility" in meta:
        comp = meta["compatibility"]
        if not isinstance(comp, str) or not comp.strip():
            res.error(where, "compatibility must be a non-empty string")
        elif len(comp) > COMPATIBILITY_MAX:
            res.error(where, f"compatibility is {len(comp)} characters, max {COMPATIBILITY_MAX}")
    if "metadata" in meta:
        md = meta["metadata"]
        if not isinstance(md, dict):
            res.error(where, "metadata must be a mapping")
        else:
            for key, value in md.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    res.error(where, f"metadata.{key} must be a string (quote it: \"{value}\")")

    body = text[fm.end():]
    body_lines = body.count("\n") + (0 if body.endswith("\n") or not body else 1)
    if body_lines > BODY_MAX_LINES:
        res.error(where, f"body has {body_lines} lines, max {BODY_MAX_LINES}; move details to references/")

    check_markdown_links(skill_md, body, root, res, mention_base=skill_dir)
    for md_file in sorted(skill_dir.rglob("*.md")):
        if md_file == skill_md or any(part in SKIP_DIRS for part in md_file.relative_to(skill_dir).parts):
            continue
        content = read_utf8(md_file, root, res)
        if content is not None:
            check_markdown_links(md_file, content, root, res, mention_base=skill_dir)


def check_markdown_links(md_file: Path, text: str, root: Path, res: Result, mention_base: Path) -> None:
    where = rel(md_file, root)
    prose = strip_code(text)
    seen: set[Path] = set()
    for target in dict.fromkeys(MD_LINK_RE.findall(prose) + MD_IMAGE_RE.findall(prose)):
        checked = check_link_target(md_file.parent, target, root, where, res)
        if checked is not None:
            seen.add(checked)
    # bare mentions such as `references/engines/fmod.md` are relative to the skill folder, or to a
    # sibling skill named on the same line ("the `babeldub-localize` skill, `references/pipeline.md`")
    siblings = sorted(p.name for p in mention_base.parent.iterdir() if p.is_dir() and p != mention_base)
    reported: set[str] = set()
    for line in text.splitlines():
        named = [mention_base.parent / s for s in siblings if s in line]
        for mention in REF_MENTION_RE.findall(line):
            candidates = [(base / mention).resolve() for base in (mention_base, md_file.parent, *named)]
            if seen.intersection(candidates) or any(c.exists() for c in candidates) or mention in reported:
                continue
            reported.add(mention)
            res.error(where, f"mentions missing file: {mention}")


def check_skills(root: Path, res: Result) -> list[str]:
    skills_root = root / "skills"
    names: list[str] = []
    if not skills_root.is_dir():
        res.warn("skills/", "no skills folder")
        return names
    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        if skill_dir.name in SKIP_DIRS:
            continue
        names.append(skill_dir.name)
        check_skill(skill_dir, root, res)
    stray = sorted(p.name for p in skills_root.iterdir() if p.is_file())
    if stray:
        res.warn("skills/", f"loose files next to skill folders: {', '.join(stray)}")
    return names


def load_json(path: Path, root: Path, res: Result) -> Any:
    text = read_utf8(path, root, res)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        res.error(rel(path, root), f"invalid JSON: {exc}")
        return None


def _check_rel_path(root: Path, value: Any, where: str, field_name: str, res: Result, suffix: str = "") -> None:
    if not isinstance(value, str):
        res.error(where, f"{field_name} must be a string path")
        return
    if not value.startswith("./"):
        res.error(where, f"{field_name} '{value}' must start with ./")
    if "\\" in value or ".." in Path(value).parts:
        res.error(where, f"{field_name} '{value}' must use / and stay inside the plugin")
    if suffix and not value.endswith(suffix):
        res.error(where, f"{field_name} '{value}' must end with {suffix}")
    if not (root / value).exists():
        res.error(where, f"{field_name} '{value}' does not exist")


def check_claude_manifest(root: Path, res: Result) -> None:
    path = root / ".claude-plugin" / "plugin.json"
    where = rel(path, root)
    if not path.exists():
        res.error(where, "missing")
        return
    data = load_json(path, root, res)
    if not isinstance(data, dict):
        return
    if data.get("name") != PLUGIN_NAME:
        res.error(where, f"name must be '{PLUGIN_NAME}'")
    homepage = data.get("homepage")
    if homepage is not None and not re.match(r"^https?://", str(homepage)):
        res.error(where, "homepage must be a URL (Claude Code refuses to load otherwise)")
    agents = data.get("agents", [])
    if isinstance(agents, str):
        agents = [agents]
    listed: set[str] = set()
    for item in agents:
        _check_rel_path(root, item, where, "agents[]", res, suffix=".md")
        if isinstance(item, str):
            listed.add(Path(item).as_posix().removeprefix("./"))
    agents_dir = root / "claude" / "agents"
    on_disk = {rel(p, root) for p in agents_dir.glob("*.md")} if agents_dir.is_dir() else set()
    for missing in sorted(on_disk - listed):
        res.error(where, f"agent file not listed in 'agents' (it will not load): ./{missing}")
    if (root / "agents").is_dir():
        res.error("agents/", "Claude agents must live in claude/agents/ (Gemini fails on agents/ in Claude format)")
    if "hooks" in data:
        hooks = data["hooks"]
        for item in hooks if isinstance(hooks, list) else [hooks]:
            if isinstance(item, str):
                _check_rel_path(root, item, where, "hooks", res, suffix=".json")
                hooks_data = load_json(root / item, root, res) if (root / item).exists() else None
                if hooks_data is not None and not isinstance(hooks_data.get("hooks") if isinstance(hooks_data, dict) else None, dict):
                    res.error(rel(root / item, root), "hooks file must be {\"hooks\": {<Event>: [...]}}")
    for key in ("skills", "commands"):
        if key in data:
            values = data[key] if isinstance(data[key], list) else [data[key]]
            for item in values:
                if isinstance(item, str):
                    _check_rel_path(root, item, where, key, res)
    res.count("manifests")


def check_marketplace(root: Path, res: Result) -> None:
    path = root / ".claude-plugin" / "marketplace.json"
    where = rel(path, root)
    if not path.exists():
        res.error(where, "missing")
        return
    data = load_json(path, root, res)
    if not isinstance(data, dict):
        return
    if not isinstance(data.get("owner"), dict) or not data["owner"].get("name"):
        res.error(where, "owner.name is required")
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        res.error(where, "plugins must be a non-empty list")
        return
    for i, entry in enumerate(plugins):
        if not isinstance(entry, dict):
            res.error(where, f"plugins[{i}] must be an object")
            continue
        if "version" in entry:
            res.error(where, f"plugins[{i}] must not set version (it would pin users; plugin.json owns it)")
        source = entry.get("source")
        if isinstance(source, str):
            target = root / source
            if not (target / ".claude-plugin" / "plugin.json").exists():
                res.error(where, f"plugins[{i}].source '{source}' has no .claude-plugin/plugin.json")
    res.count("manifests")


def check_agent_plugins_manifest(root: Path, res: Result) -> None:
    path = root / "plugin.json"
    where = rel(path, root)
    if not path.exists():
        res.error(where, "missing (Agent Plugins 1.0 entry point for Codex, Copilot, Cursor)")
        return
    data = load_json(path, root, res)
    if not isinstance(data, dict):
        return
    if data.get("$schema") != AGENT_PLUGINS_SCHEMA:
        res.error(where, f"$schema must be {AGENT_PLUGINS_SCHEMA}")
    extra = sorted(set(data) - AGENT_PLUGINS_KEYS)
    if extra:
        res.error(where, f"fields outside the closed Agent Plugins schema: {', '.join(extra)}")
    name = data.get("name")
    if not isinstance(name, str) or not re.match(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$", name) \
            or "--" in name or ".." in name:
        res.error(where, "name must be 1-64 chars of a-z 0-9 - . (alphanumeric at both ends)")
    author = data.get("author")
    if author is not None and (not isinstance(author, dict) or set(author) - {"name", "email", "url"}):
        res.error(where, "author may only contain name, email, url")
    if (root / "mcp.json").exists():
        mcp = load_json(root / "mcp.json", root, res)
        if isinstance(mcp, dict) and not str(mcp.get("$schema", "")).startswith("https://agent-plugins.org/schemas/1.0.0/"):
            res.error("mcp.json", "$schema must target the same Agent Plugins version as plugin.json")
    res.count("manifests")


def check_codex_manifest(root: Path, res: Result) -> None:
    path = root / ".codex-plugin" / "plugin.json"
    where = rel(path, root)
    if not path.exists():
        res.error(where, "missing")
        return
    data = load_json(path, root, res)
    if not isinstance(data, dict):
        return
    if data.get("name") != PLUGIN_NAME:
        res.error(where, f"name must be '{PLUGIN_NAME}'")
    if "skills" in data:
        _check_rel_path(root, data["skills"], where, "skills", res)
    if "hooks" in data:
        res.warn(where, "hooks in the Codex manifest: Claude hook events are not portable to Codex")
    iface = data.get("interface", {})
    if isinstance(iface, dict):
        for key in ("logo", "logoDark", "composerIcon"):
            if key in iface:
                _check_rel_path(root, iface[key], where, f"interface.{key}", res)
        prompts = iface.get("defaultPrompt", [])
        if not isinstance(prompts, list) or not all(isinstance(p, str) for p in prompts):
            res.error(where, "interface.defaultPrompt must be a list of strings")
    res.count("manifests")


def check_gemini_manifest(root: Path, res: Result) -> None:
    path = root / "gemini-extension.json"
    where = rel(path, root)
    if not path.exists():
        res.error(where, "missing")
        return
    data = load_json(path, root, res)
    if not isinstance(data, dict):
        return
    if data.get("name") != PLUGIN_NAME:
        res.error(where, f"name must be '{PLUGIN_NAME}'")
    ctx = data.get("contextFileName", "GEMINI.md")
    for name in ctx if isinstance(ctx, list) else [ctx]:
        if not (root / str(name)).exists():
            res.error(where, f"contextFileName '{name}' does not exist")
    for i, setting in enumerate(data.get("settings", []) or []):
        if not isinstance(setting, dict) or not all(setting.get(k) for k in ("name", "description", "envVar")):
            res.error(where, f"settings[{i}] needs name, description, envVar")
    if (root / "hooks" / "hooks.json").exists():
        res.warn("hooks/hooks.json", "Gemini loads hooks/hooks.json from extensions; keep Claude hooks in claude/")
    res.count("manifests")


def check_agent_files(root: Path, res: Result) -> None:
    agents_dir = root / "claude" / "agents"
    if not agents_dir.is_dir():
        return
    for path in sorted(agents_dir.glob("*.md")):
        where = rel(path, root)
        text = read_utf8(path, root, res)
        if text is None:
            continue
        res.count("agents")
        fm = FRONTMATTER_RE.match(text)
        if not fm:
            res.error(where, "missing YAML frontmatter")
            continue
        try:
            meta = _load_yaml(fm.group("yaml"))
        except Exception as exc:
            res.error(where, f"frontmatter is not strict YAML ({str(exc).splitlines()[0]})")
            continue
        if not isinstance(meta, dict):
            res.error(where, "frontmatter must be a mapping")
            continue
        if meta.get("name") != path.stem:
            res.error(where, f"name must equal the file name '{path.stem}'")
        if not isinstance(meta.get("description"), str) or not meta["description"].strip():
            res.error(where, "description is required")


def check_tree(root: Path, files: list[Path], res: Result) -> None:
    if (root / "bin").exists():
        res.error("bin/", "a bin/ folder at the root blocks plugin install in claude.ai and Cowork")
    if (root / "CLAUDE.md").exists():
        res.error("CLAUDE.md", "not loaded from a plugin root; put instructions in a skill or AGENTS.md")
    for path in files:
        if path.is_symlink():
            res.warn(rel(path, root), "symlink: release archives skip symlinks")
            continue
        r = rel(path, root)
        suffix = path.suffix.lower()
        size = path.stat().st_size
        res.count("files")
        if suffix in GAME_ASSET_EXT:
            res.error(r, "game asset container in the repository (never commit game files)")
        elif suffix in AUDIO_EXT:
            res.warn(r, "audio file in the repository: make sure it is not a game or actor recording")
        is_asset_png = r.startswith("assets/") and suffix == ".png"
        if size > MAX_FILE_BYTES and not is_asset_png:
            res.error(r, f"{size / 1048576:.1f} MB file (limit 1 MB, except assets/*.png)")
        if suffix in TEXT_EXT:
            data = path.read_bytes()
            if data.startswith(b"\xef\xbb\xbf"):
                res.error(r, "UTF-8 BOM (save without BOM)")
            if suffix == ".json" and not path.name.endswith(".jsonl"):
                try:
                    json.loads(data.decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    res.error(r, f"invalid JSON: {exc}")
                res.count("json")


def validate(root: Path) -> tuple[Result, list[str]]:
    res = Result()
    root = root.resolve()
    files = list_files(root)
    skills = check_skills(root, res)
    check_claude_manifest(root, res)
    check_marketplace(root, res)
    check_agent_plugins_manifest(root, res)
    check_codex_manifest(root, res)
    check_gemini_manifest(root, res)
    check_agent_files(root, res)
    check_tree(root, files, res)
    return res, skills


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate BabelDub skills, manifests and repository hygiene.")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this repo)")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    res, skills = validate(args.root)
    if args.json:
        print(json.dumps({"ok": not res.errors, "skills": skills, "checked": res.checked,
                          "errors": res.errors, "warnings": res.warnings}, ensure_ascii=False, indent=2))
    else:
        for w in res.warnings:
            print(f"warning: {w}")
        for e in res.errors:
            print(f"error: {e}")
        summary = ", ".join(f"{k}={v}" for k, v in sorted(res.checked.items()))
        status = "FAILED" if res.errors else "ok"
        print(f"{status}: {len(res.errors)} error(s), {len(res.warnings)} warning(s); checked {summary}")
    return 1 if res.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
