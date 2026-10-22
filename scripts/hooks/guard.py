#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""BabelDub hook handler for Claude Code (stdlib only, see claude/hooks.json).

    guard.py pre      PreToolUse: block writes into a game folder registered in BABELDUB_HOME/games.json
    guard.py session  SessionStart: print a short summary of the active project (BABELDUB_HOME/active.json)

`pre` reads the hook event JSON from stdin ({"tool_name", "tool_input", "cwd", ...}). A Write/Edit/
MultiEdit/NotebookEdit into a registered game folder, or a Bash/PowerShell command that writes, deletes or
moves something there, exits with code 2 and the reason on stderr (Claude Code blocks the tool call and
shows the reason to the model). `babeldub install|uninstall|reapply` are the only allowed writers.

File-tool targets are resolved through symlinks, junctions and Windows 8.3 short names. The shell check
is only a heuristic (commands are tokenized, not executed, so variables, aliases or generated scripts can
slip past it); the real protection is the CLI,
which never writes into the game folder outside install/uninstall/reapply. Any internal error exits 0
(fail-open), so a broken hook never blocks unrelated work. Set BABELDUB_GUARD=off to disable the check.
"""

from __future__ import annotations

import base64
import binascii
import json
import ntpath
import os
import posixpath
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXIT_ALLOW = 0
EXIT_BLOCK = 2
MAX_COMMAND_CHARS = 200_000
MAX_DEPTH = 4

FILE_TOOLS = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
SHELL_TOOLS = {"Bash": "posix", "PowerShell": "ps"}

# ---------------------------------------------------------------- BABELDUB_HOME (mirrors core/paths.py)


def babeldub_home() -> Path:
    env = os.environ.get("BABELDUB_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".babeldub"


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    return json.loads(text)


def load_game_dirs(home: Path) -> list[str]:
    data = _read_json(home / "games.json")
    games = data.get("games") if isinstance(data, dict) else None
    if not isinstance(games, list):
        return []
    return [str(g["game_dir"]) for g in games if isinstance(g, dict) and g.get("game_dir")]


# ---------------------------------------------------------------- paths


_MSYS_DRIVE = re.compile(r"^/(?:mnt/|cygdrive/)?([a-zA-Z])(?=/|$)")
_SLASHES = re.compile(r"/{2,}")


def expand_short_names(path: str) -> str:
    """Windows: expand 8.3 short names (PROGRA~1) in the nearest existing parent of a path; else unchanged."""
    if os.name != "nt" or not path:
        return path
    import ctypes

    head, tail = os.path.abspath(path), []
    while not os.path.exists(head):
        parent = os.path.dirname(head)
        if parent == head:
            return path
        tail.append(os.path.basename(head))
        head = parent
    buf = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetLongPathNameW(head, buf, len(buf))
    if not size or size >= len(buf):
        return path
    return os.path.join(buf.value, *reversed(tail))


class PathMatcher:
    """Decides whether a path (or a path-like token from a command) lies inside a registered game folder."""

    def __init__(self, game_dirs: list[str], cwd: str | None, windows: bool | None = None) -> None:
        self.windows = (os.name == "nt") if windows is None else windows
        self.mod = ntpath if self.windows else posixpath
        self.sep = "\\" if self.windows else "/"
        self.cwd = self._absolute(cwd) if cwd else None
        games: list[str] = []
        self.display: dict[str, str] = {}  # normalized -> spelling from the registry
        for raw in game_dirs:
            for variant in (raw, self._realpath(raw)):
                norm = self._absolute(variant) if variant else None
                if norm and norm not in games:
                    games.append(norm)
                    self.display[norm] = raw
        self.games = games
        self.needles = sorted({n for g in games for n in self._needles(g)}, key=len, reverse=True)
        self.cwd_in_game = bool(self.cwd and self.game_of(self.cwd))

    def _realpath(self, raw: str) -> str | None:
        if self.windows != (os.name == "nt"):
            return None  # cross-platform unit tests: no filesystem lookups
        try:
            return os.path.realpath(expand_short_names(raw))
        except (OSError, ValueError):
            return None

    def inside_file(self, target: str) -> str | None:
        """Game folder containing a file-tool target, also through symlinks, junctions and 8.3 short names."""
        absolute = self._absolute(target)
        if not absolute:
            return None
        return self.game_of(absolute) or self.game_of(self._absolute(self._realpath(absolute)))

    def _absolute(self, raw: str | None) -> str | None:
        """Normalized absolute form of a path, or None if it cannot be resolved."""
        if not raw:
            return None
        path = raw.strip().strip("\"'")
        if not path or "\x00" in path:
            return None
        if path.startswith("~"):
            path = self.mod.expanduser(path)
        if self.windows:
            if path.startswith(("\\\\?\\", "//?/")):
                path = path[4:]
            path = path.replace("\\", "/")
            match = _MSYS_DRIVE.match(path)
            if match and not re.match(r"^[a-zA-Z]:", path):
                path = f"{match.group(1)}:" + path[match.end() :]
                if len(path) == 2:
                    path += "/"
            if not re.match(r"^[a-zA-Z]:/", path) and not path.startswith("//"):
                if not self.cwd:
                    return None
                path = self.mod.join(self.cwd, path)
            return self.mod.normcase(self.mod.normpath(path))
        if not path.startswith("/"):
            if not self.cwd:
                return None
            path = self.mod.join(self.cwd, path)
        return self.mod.normpath(path)

    def _needles(self, game: str) -> list[str]:
        """Text forms of a game folder as they may appear in a command line (see _flatten)."""
        fwd = _SLASHES.sub("/", game.replace("\\", "/")) if self.windows else _SLASHES.sub("/", game)
        out = [fwd]
        if self.windows:
            match = re.match(r"^([a-z]):/(.*)$", fwd)
            if match:
                drive, rest = match.groups()
                out += [f"/{drive}/{rest}", f"/mnt/{drive}/{rest}", f"/cygdrive/{drive}/{rest}"]
        home = self._absolute(self.mod.expanduser("~"))
        if home:
            home_fwd = home.replace("\\", "/").rstrip("/") + "/"
            if fwd.startswith(home_fwd):
                out.append("~/" + fwd[len(home_fwd) :])
        return [n.rstrip("/") for n in out if n.rstrip("/")]

    def game_of(self, path: str | None) -> str | None:
        """Registered game folder (as spelled in the registry) that contains the already-normalized path."""
        if not path:
            return None
        for game in self.games:
            prefix = game if game.endswith(self.sep) else game + self.sep
            if path == game or path.startswith(prefix):
                return self.display.get(game, game)
        return None

    @property
    def first_game(self) -> str:
        return self.display.get(self.games[0], self.games[0]) if self.games else ""

    def inside(self, token: str | None) -> str | None:
        """Game folder containing a raw path or command-line token, else None."""
        if not token or token.startswith("-"):
            return None
        return self.game_of(self._absolute(token))

    def resolve(self, token: str) -> str | None:
        """Normalized absolute path of a token (relative to the current working directory)."""
        return self._absolute(token)

    def _flatten(self, text: str) -> str:
        flat = text.replace("\\ ", " ").replace("\\", "/").replace('"', "").replace("'", "")
        flat = _SLASHES.sub("/", flat)
        return flat.lower() if self.windows else flat

    def mentions(self, text: str) -> bool:
        """Cheap pre-check: does the text contain a game folder in any common spelling?"""
        flat = self._flatten(text)
        return any(n in flat for n in self.needles)


# ---------------------------------------------------------------- shell commands

_SEP_CHARS = set(";&|()\n")
_PUNCT = "();<>|&\n"

WRAPPERS = {"sudo", "doas", "env", "command", "builtin", "exec", "nohup", "time", "nice", "ionice", "."}
CHDIR_COMMANDS = {"cd", "pushd", "chdir", "set-location", "sl", "push-location"}
LAUNCHERS = {"babeldub", "uv", "uvx", "pipx", "py", "python", "python3", "pythonw"}
SAFE_SUBCOMMANDS = {"install", "uninstall", "reapply"}

# Every path argument is written, deleted, moved or re-attributed.
MUTATE_ANY = {
    "rm",
    "rmdir",
    "unlink",
    "shred",
    "truncate",
    "touch",
    "mkdir",
    "chmod",
    "chown",
    "chgrp",
    "mv",
    "rename",
    "del",
    "erase",
    "rd",
    "md",
    "move",
    "ren",
    "attrib",
    "icacls",
    "takeown",
    "mklink",
    "remove-item",
    "ri",
    "move-item",
    "mi",
    "rename-item",
    "rni",
    "clear-content",
    "clc",
    "set-content",
    "sc",
    "add-content",
    "ac",
    "out-file",
    "tee",
    "tee-object",
    "new-item",
    "ni",
    "set-itemproperty",
    "sp",
    "clear-item",
    "cli",
    "remove-itemproperty",
    "rp",
}
# Source is only read; the destination is written.
COPY_DEST = {
    "cp",
    "copy",
    "copy-item",
    "cpi",
    "install",
    "rsync",
    "scp",
    "ln",
    "xcopy",
    "robocopy",
    "expand-archive",
    "compress-archive",
}
# PowerShell cmdlets (and their aliases) that take the paths to mutate from the pipeline.
PIPE_MUTATORS = {
    "remove-item",
    "ri",
    "rm",
    "del",
    "erase",
    "rd",
    "rmdir",
    "move-item",
    "mi",
    "mv",
    "move",
    "rename-item",
    "rni",
    "ren",
    "clear-content",
    "clc",
}
INTERPRETERS = {
    "python",
    "python3",
    "py",
    "pythonw",
    "node",
    "deno",
    "bun",
    "ruby",
    "perl",
    "php",
    "uv",
    "uvx",
    "rscript",
}
POSIX_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
PS_SHELLS = {"powershell", "pwsh", "powershell_ise"}
WIN_SLASH_TOOLS = {
    "xcopy",
    "robocopy",
    "copy",
    "move",
    "del",
    "erase",
    "rd",
    "rmdir",
    "md",
    "attrib",
    "icacls",
    "takeown",
    "mklink",
    "ren",
    "rename",
}
# Options of any command whose value is an output path.
OUTPUT_OPTS = {
    "o",
    "out",
    "output",
    "outfile",
    "output-file",
    "outputfile",
    "output-dir",
    "outdir",
    "out-dir",
    "output-directory",
    "outputdirectory",
    "dest",
    "destination",
    "destinationpath",
    "target",
    "target-dir",
    "target-directory",
    "save-to",
}
VALUE_OPTS = {
    "cp": {"t", "target-directory", "S", "suffix"},
    "install": {"t", "target-directory", "m", "mode", "o", "owner", "g", "group", "S", "suffix"},
    "ln": {"t", "target-directory", "S", "suffix"},
    "rsync": {"e", "rsh", "exclude", "include", "filter", "f"},
    "scp": {"i", "o", "P", "F", "c", "l", "S"},
    "tar": {"C", "directory", "f", "file", "T", "files-from", "X", "exclude-from"},
    "unzip": {"d", "x"},
    "xargs": {"n", "I", "P", "d", "L", "s", "E", "a", "i"},
}
PS_VALUE_PARAMS = {
    "path",
    "literalpath",
    "destination",
    "filepath",
    "destinationpath",
    "outfile",
    "value",
    "itemtype",
    "name",
    "newname",
    "encoding",
    "include",
    "exclude",
    "filter",
    "inputobject",
    "target",
    "type",
    "delimiter",
    "credential",
    "stream",
    "width",
    "depth",
}

# Inline code of an interpreter (python -c, heredoc, node -e ...) that writes files.
_CODE_WRITES = [
    re.compile(r"""\bopen\s*\([^()]*?['"](?:[wax][bt+]*|r\+[bt]*|r[bt]\+)['"]"""),
    re.compile(r"\.(?:write_text|write_bytes|unlink|rmdir|rename|touch|mkdir|symlink_to|hardlink_to)\s*\("),
    re.compile(r"\bshutil\.(?:rmtree|move)\b"),
    re.compile(
        r"\bos\.(?:remove|unlink|rename|replace|rmdir|removedirs|makedirs|mkdir|truncate|symlink|link)\b"
    ),
    re.compile(r"\bfs(?:\.promises)?\.(?:write|append|unlink|rm|rmdir|rename|mkdir|truncate)\w*"),
    re.compile(r"\bFile\.(?:write|delete|unlink|rename)", re.IGNORECASE),
]
# .NET file APIs called from PowerShell.
_DOTNET_WRITES = re.compile(
    r"\[(?:system\.)?io\.(?:file|directory)\]::(?:write\w*|append\w*|delete|move|copy|replace|create\w*|set\w*)",
    re.IGNORECASE,
)
_ENCODED_PS = re.compile(r"(?i)\b(?:powershell|pwsh)(?:\.exe)?\b.*\s-e(?:c|n\w*)?\s+[A-Za-z0-9+/=]{8,}")
_CODE_COPY = re.compile(
    r"\b(?:shutil\.copy\w*|fs(?:\.promises)?\.(?:copyFile\w*|cp\w*))\s*\(([^,()]*),([^()]*)\)"
)


@dataclass
class Segment:
    words: list[str] = field(default_factory=list)
    redirects: list[str] = field(default_factory=list)
    piped: bool = False
    upstream: list[str] = field(default_factory=list)  # words of earlier commands in the same pipeline


def cmd_name(word: str) -> str:
    base = re.split(r"[\\/]", word)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def tokenize(text: str, dialect: str) -> list[str]:
    if dialect == "posix":
        text = text.replace("\\\r\n", "").replace("\\\n", "")
    else:
        text = text.replace("`\r\n", "").replace("`\n", "")
    text = text.replace("\r\n", "\n")
    lexer = shlex.shlex(text, posix=(dialect == "posix"), punctuation_chars=_PUNCT)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:  # unbalanced quotes: fall back to a tolerant split
        tokens = re.findall(r'"[^"]*"?|\'[^\']*\'?|[();<>|&\n]+|[^\s();<>|&]+', text)
    if dialect != "posix":
        tokens = [_unquote(t) for t in tokens]
    return tokens


def _is_punct(token: str) -> bool:
    return bool(token) and all(c in _PUNCT for c in token)


def split_segments(tokens: list[str]) -> list[Segment]:
    segments: list[Segment] = []
    current = Segment()
    pipeline: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _is_punct(tok):
            if ">" in tok:
                if i + 1 < len(tokens) and not _is_punct(tokens[i + 1]):
                    current.redirects.append(tokens[i + 1])
                    i += 1
            elif "<" in tok:
                if i + 1 < len(tokens) and not _is_punct(tokens[i + 1]):
                    i += 1  # input redirection only reads
            if any(c in _SEP_CHARS for c in tok) and not (">" in tok or "<" in tok):
                if current.words or current.redirects:
                    segments.append(current)
                piped = tok in ("|", "|&")
                pipeline = (pipeline + current.words) if piped else []
                current = Segment(piped=piped, upstream=list(pipeline))
        else:
            current.words.append(tok)
        i += 1
    if current.words or current.redirects:
        segments.append(current)
    return segments


def _strip_wrappers(words: list[str]) -> list[str]:
    i = 0
    while i < len(words):
        word = words[i]
        name = cmd_name(word)
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            i += 1  # VAR=value prefix
        elif word.startswith("$") and i + 1 < len(words) and words[i + 1] in ("=", "+="):
            i += 2  # PowerShell assignment: $x = <command>
        elif name in WRAPPERS:
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 1
        else:
            break
    return words[i:]


def is_babeldub_mutation(words: list[str]) -> bool:
    """`babeldub install|uninstall|reapply`, also via `uv run`, `uvx`, `pipx run` or `python -m`."""
    names = [cmd_name(w) for w in words]
    if not names or names[0] not in LAUNCHERS or "babeldub" not in names:
        return False
    at = names.index("babeldub")
    if names[0] in ("py", "python", "python3", "pythonw") and (at == 0 or words[at - 1] != "-m"):
        return False
    j = at + 1
    while j < len(words):
        if words[j] == "--project":
            j += 2
        elif words[j].startswith("-"):
            j += 1
        else:
            break
    return j < len(words) and words[j].lower() in SAFE_SUBCOMMANDS


def parse_args(name: str, args: list[str], dialect: str) -> tuple[list[str], list[tuple[str, str | None]]]:
    """Split arguments into positionals and (option, value) pairs, knowing a few value-taking options."""
    positionals: list[str] = []
    options: list[tuple[str, str | None]] = []
    value_opts = VALUE_OPTS.get(name, set())
    slash_opts = name in WIN_SLASH_TOOLS
    ps_like = dialect != "posix"
    i = 0
    only_positionals = False
    while i < len(args):
        arg = args[i]
        if only_positionals:
            positionals.append(arg)
        elif arg == "--" and not ps_like:
            only_positionals = True
        elif slash_opts and re.match(r"^/[A-Za-z?][A-Za-z0-9]*(?::.*)?$", arg) and (ps_like or len(arg) > 2):
            key, _, value = arg[1:].partition(":")
            options.append((key.lower(), value or None))
        elif arg.startswith("-") and len(arg) > 1 and not re.match(r"^-\d", arg):
            body = arg.lstrip("-")
            if ps_like and ":" in body and not re.match(r"^[A-Za-z]:[\\/]", body):
                key, _, value = body.partition(":")
                options.append((key.lower(), value))
            elif "=" in body:
                key, _, value = body.partition("=")
                options.append((key.lower(), value))
            else:
                key = body if len(body) == 1 else body.lower()
                takes = (
                    key.lower() in PS_VALUE_PARAMS
                    if ps_like
                    else (key in value_opts or key.lower() in OUTPUT_OPTS)
                )
                if takes and i + 1 < len(args):
                    options.append((key.lower(), args[i + 1]))
                    i += 1
                else:
                    options.append((key.lower(), None))
        else:
            positionals.append(arg)
        i += 1
    return positionals, options


def _block(what: str, path: str, game: str) -> str:
    return f"{what}: {path} (game folder {game})"


def _rejoin(args: list[str], dialect: str) -> str:
    """Rebuild a nested command line from already-unquoted arguments (a single argument is the script)."""
    if len(args) == 1:
        return args[0]
    if dialect == "posix":
        return shlex.join(args)
    return " ".join(f'"{a}"' if any(c.isspace() for c in a) and a[:1] not in "\"'" else a for a in args)


class ShellAnalyzer:
    def __init__(self, matcher: PathMatcher) -> None:
        self.m = matcher

    def analyze(self, command: str, dialect: str, depth: int = 0) -> str | None:
        if depth > MAX_DEPTH or not command:
            return None
        command = command[:MAX_COMMAND_CHARS]
        if not self.m.mentions(command) and not self.m.cwd_in_game and not _ENCODED_PS.search(command):
            return None
        saved = (self.m.cwd, self.m.cwd_in_game)
        try:
            for seg in split_segments(tokenize(command, dialect)):
                if self._change_dir(seg):
                    continue
                reason = self._segment(seg, command, dialect, depth)
                if reason:
                    return reason
            return None
        finally:
            self.m.cwd, self.m.cwd_in_game = saved

    def _change_dir(self, seg: Segment) -> bool:
        """Follow `cd <dir>` / `Set-Location <dir>` so later relative paths resolve against it."""
        words = _strip_wrappers(seg.words)
        if not words or cmd_name(words[0]) not in CHDIR_COMMANDS:
            return False
        targets = [w for w in words[1:] if not w.startswith("-")]
        if targets:
            new_cwd = self.m.resolve(targets[-1])
            if new_cwd:
                self.m.cwd = new_cwd
                self.m.cwd_in_game = bool(self.m.game_of(new_cwd))
        return True

    def _first_inside(self, candidates: list[str | None]) -> tuple[str, str] | None:
        for cand in candidates:
            game = self.m.inside(cand)
            if game and cand:
                return cand, game
        return None

    def _segment(self, seg: Segment, command: str, dialect: str, depth: int) -> str | None:
        hit = self._first_inside(list(seg.redirects))
        if hit:
            return _block("output redirection to a file", *hit)
        words = _strip_wrappers(seg.words)
        if not words or is_babeldub_mutation(words):
            return None
        name, args = cmd_name(words[0]), words[1:]
        if name in POSIX_SHELLS:
            return self._nested_posix(args, depth)
        if name in PS_SHELLS:
            return self._nested_ps(args, depth)
        if name == "cmd":
            return self._nested_cmd(args, depth)
        if name == "wsl":
            rest = [a for a in args if a not in ("-e", "--exec", "--")]
            return self.analyze(_rejoin(rest, "posix"), "posix", depth + 1) if rest else None
        if name == "xargs":
            return self._xargs(seg, args, command, dialect, depth)
        positionals, options = parse_args(name, args, dialect)
        option_values = [v for o, v in options if v and o not in ("value", "inputobject")]

        if name in PIPE_MUTATORS and dialect == "ps" and seg.piped and not positionals:
            upstream = self._first_inside(seg.upstream)
            if upstream or self.m.mentions(" ".join(seg.upstream)):
                game = upstream[1] if upstream else self.m.first_game
                return _block(f"{words[0]} via the pipeline", upstream[0] if upstream else "<pipeline>", game)
        if name in ("sed", "perl"):
            in_place = any(
                o == "in-place" or (name == "sed" and o.startswith("i")) or (name == "perl" and "i" in o)
                for o, _ in options
            )
            hit = self._first_inside(positionals + option_values) if in_place else None
            if hit:
                return _block(f"{name} -i (in-place edit)", *hit)
        if name in MUTATE_ANY:
            hit = self._first_inside(positionals + option_values)
            if hit:
                return _block(f"{words[0]} (write, delete or move)", *hit)
        if name in COPY_DEST:
            reason = self._copy(name, words[0], positionals, options)
            if reason:
                return reason
        if name == "find":
            hit = self._find(args)
            if hit:
                return _block("find -delete/-exec", *hit)
        if name == "dd":
            hit = self._first_inside([a[3:] for a in args if a.startswith("of=")])
            if hit:
                return _block("dd of=", *hit)
        if name == "tar":
            hit = self._tar(args)
            if hit:
                return _block("tar", *hit)
        if name == "unzip":
            hit = self._first_inside([v for o, v in options if o == "d"])
            if hit:
                return _block("unzip -d", *hit)
        if name in ("7z", "7za", "7zr"):
            outs = [a[2:] for a in args if a.startswith("-o")]
            if positionals[:1] == ["a"] and len(positionals) > 1:
                outs.append(positionals[1])
            hit = self._first_inside(outs)
            if hit:
                return _block("7z", *hit)
        hit = self._first_inside([v for o, v in options if o in OUTPUT_OPTS])
        if hit:
            return _block(f"{words[0]}: output path", *hit)
        if name in INTERPRETERS:
            return self._code(command)
        if dialect == "ps":
            return self._dotnet(command)
        return None

    def _find(self, args: list[str]) -> tuple[str, str] | None:
        if "-delete" in args:
            return self._first_inside(args)
        for i, arg in enumerate(args):
            if arg not in ("-exec", "-execdir", "-ok", "-okdir") or i + 1 >= len(args):
                continue
            sub = [a for a in args[i + 1 :] if a not in (";", "\\;", "+", "{}")]
            if cmd_name(args[i + 1]) in MUTATE_ANY:
                return self._first_inside(args)
            if cmd_name(args[i + 1]) in COPY_DEST and len(sub) >= 2:
                return self._first_inside(sub[-1:])
        return None

    def _copy(
        self, name: str, shown: str, positionals: list[str], options: list[tuple[str, str | None]]
    ) -> str | None:
        explicit = [
            v for o, v in options if v and o in ("t", "target-directory", "destination", "destinationpath")
        ]
        if explicit:
            dests: list[str | None] = list(explicit)
        elif name in ("xcopy", "robocopy", "expand-archive", "compress-archive", "copy-item", "cpi"):
            dests = positionals[1:2]
        elif len(positionals) >= 2:
            dests = positionals[-1:]
        elif name == "ln" and len(positionals) == 1 and self.m.cwd_in_game:
            return _block("ln in the current folder", self.m.cwd or "", self.m.first_game)
        else:
            dests = []
        hit = self._first_inside(dests)
        if hit:
            return _block(f"{shown} (destination)", *hit)
        if name == "robocopy" and any(o in ("mov", "move") for o, _ in options):
            hit = self._first_inside(positionals[:1])
            if hit:
                return _block("robocopy /MOV (moves files out of the folder)", *hit)
        return None

    def _tar(self, args: list[str]) -> tuple[str, str] | None:
        """tar writes into -C/--directory (extract) and into -f/--file (create, append, update)."""
        directories: list[str | None] = []
        archives: list[str | None] = []
        modes = ""
        i = 0
        while i < len(args):
            arg = args[i]
            nxt = args[i + 1] if i + 1 < len(args) else None
            if arg.startswith("--"):
                key, eq, value = arg[2:].partition("=")
                if key in ("create", "append", "update"):
                    modes += "c"
                elif key in ("directory", "file"):
                    target = value if eq else nxt
                    (directories if key == "directory" else archives).append(target)
                    i += 0 if eq else 1
            elif arg.startswith("-") or (i == 0 and re.match(r"^[A-Za-z]+$", arg)):
                letters = arg.lstrip("-")
                modes += letters
                if letters.endswith("C"):
                    directories.append(nxt)
                    i += 1
                elif letters.endswith("f"):
                    archives.append(nxt)
                    i += 1
            i += 1
        hit = self._first_inside(directories)
        if hit:
            return hit
        if any(ch in modes for ch in "cru"):
            return self._first_inside(archives)
        return None

    def _xargs(self, seg: Segment, args: list[str], command: str, dialect: str, depth: int) -> str | None:
        i = 0
        while i < len(args) and args[i].startswith("-"):
            key = args[i].lstrip("-")
            i += 2 if key in VALUE_OPTS["xargs"] and len(args[i].lstrip("-")) == 1 else 1
        sub = args[i:]
        if not sub:
            return None
        name = cmd_name(sub[0])
        if name in MUTATE_ANY and (
            self.m.mentions(" ".join(seg.upstream)) or self._first_inside(seg.upstream)
        ):
            return _block(f"xargs {sub[0]} (paths from the pipeline)", "<pipeline>", self.m.first_game)
        inner = Segment(words=sub, piped=seg.piped, upstream=seg.upstream)
        return self._segment(inner, command, dialect, depth + 1)

    def _nested_posix(self, args: list[str], depth: int) -> str | None:
        for i, arg in enumerate(args):
            if arg.startswith("-") and not arg.startswith("--") and "c" in arg and i + 1 < len(args):
                return self.analyze(args[i + 1], "posix", depth + 1)
        return None

    def _nested_ps(self, args: list[str], depth: int) -> str | None:
        for i, arg in enumerate(args):
            low = arg.lower()
            if not low.startswith("-") or i + 1 >= len(args):
                continue
            if low in ("-e", "-ec") or (len(low) >= 3 and "-encodedcommand".startswith(low)):
                try:
                    decoded = base64.b64decode(args[i + 1]).decode("utf-16-le")
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    return None
                return self.analyze(decoded, "ps", depth + 1)
            if low == "-c" or (len(low) >= 4 and "-command".startswith(low)):
                return self.analyze(_rejoin(args[i + 1 :], "ps"), "ps", depth + 1)
        return None

    def _nested_cmd(self, args: list[str], depth: int) -> str | None:
        for i, arg in enumerate(args):
            if arg.lower() in ("/c", "//c", "/k", "//k") and i + 1 < len(args):
                return self.analyze(_rejoin(args[i + 1 :], "cmd"), "cmd", depth + 1)
        return None

    def _code(self, command: str) -> str | None:
        """Inline interpreter code (python -c, heredoc, node -e) that writes files and mentions the game folder."""
        if not self.m.mentions(command):
            return None
        for pattern in [*_CODE_WRITES, _DOTNET_WRITES]:
            match = pattern.search(command)
            if match:
                return _block(
                    f"code that writes files ({match.group(0)[:40]})", "<inline code>", self.m.first_game
                )
        for match in _CODE_COPY.finditer(command):
            if self.m.mentions(match.group(2)):
                return _block("copy into the game folder", match.group(2).strip()[:80], self.m.first_game)
        return None

    def _dotnet(self, command: str) -> str | None:
        """PowerShell calling [IO.File]::Write*/Delete/... while mentioning the game folder."""
        match = _DOTNET_WRITES.search(command) if self.m.mentions(command) else None
        if match:
            return _block(f".NET call ({match.group(0)[:40]})", "<inline code>", self.m.first_game)
        return None


# ---------------------------------------------------------------- PreToolUse


def check_event(event: dict[str, Any], game_dirs: list[str], windows: bool | None = None) -> str | None:
    """Reason to block the tool call, or None to allow it."""
    tool = event.get("tool_name")
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict) or not game_dirs:
        return None
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else None
    matcher = PathMatcher(game_dirs, cwd, windows)
    if not matcher.games:
        return None
    if tool in FILE_TOOLS:
        target = tool_input.get(FILE_TOOLS[tool]) or tool_input.get("file_path")
        if isinstance(target, str):
            game = matcher.inside_file(target)
            if game:
                return _block(f"{tool}", target, game)
        return None
    if tool in SHELL_TOOLS:
        command = tool_input.get("command")
        if isinstance(command, str):
            return ShellAnalyzer(matcher).analyze(command, SHELL_TOOLS[tool])
    return None


BLOCK_MESSAGE = (
    "BabelDub: write into the game folder blocked — {reason}.\n"
    "Until install, the game folder is read-only. Only "
    "`babeldub install` (with a backup), `babeldub uninstall` and `babeldub reapply` may change it; "
    "put working files in the project folder (`babeldub status` shows it).\n"
    "If the command only reads from the game folder, rewrite it so that the game path is not the destination."
)


def run_pre(stdin_text: str) -> int:
    game_dirs = load_game_dirs(babeldub_home())
    if not game_dirs:
        return EXIT_ALLOW
    event = json.loads(stdin_text) if stdin_text.strip() else {}
    if not isinstance(event, dict):
        return EXIT_ALLOW
    reason = check_event(event, game_dirs)
    if reason:
        _write(sys.stderr, BLOCK_MESSAGE.format(reason=reason) + "\n")
        return EXIT_BLOCK
    return EXIT_ALLOW


# ---------------------------------------------------------------- SessionStart

STAGES = [
    "init",
    "scan",
    "extract",
    "link",
    "cast",
    "translate",
    "synth",
    "post",
    "qa",
    "build",
    "verify",
    "install",
]
STAGE_COMMAND = {
    "init": "babeldub init <game_dir> --lang <code>",
    "scan": "babeldub scan",
    "extract": "babeldub extract",
    "link": "babeldub link",
    "cast": "babeldub cast",
    "translate": "babeldub translate plan",
    "synth": "babeldub synth",
    "post": "babeldub post",
    "qa": "babeldub qa",
    "build": "babeldub build",
    "verify": "babeldub verify",
    "install": "babeldub install",
}
STAGE_SKILL = {
    "init": "babeldub-localize",
    "scan": "babeldub-recon",
    "extract": "babeldub-recon",
    "link": "babeldub-recon",
    "cast": "babeldub-cast",
    "translate": "babeldub-translate",
    "synth": "babeldub-voice",
    "post": "babeldub-voice",
    "qa": "babeldub-qa",
    "build": "babeldub-pack",
    "verify": "babeldub-pack",
    "install": "babeldub-pack",
}
STATUS_MARK = {"done": "✓", "attention": "!", "running": "…", "failed": "✗"}


def next_step(progress: dict[str, Any]) -> str:
    install = progress.get("install")
    if isinstance(install, dict) and install.get("state") == "uninstalled":
        # `babeldub uninstall` records the install stage with state=uninstalled
        return "dub uninstalled (`babeldub uninstall`); to put it back, run `babeldub install`"
    # a stage in `attention` is finished with items left to decide; the pipeline goes on (as `babeldub status`)
    waiting: list[str] = []
    for stage in STAGES:
        entry = progress.get(stage)
        status = entry.get("status") if isinstance(entry, dict) else None
        if status == "done":
            continue
        cmd, skill = STAGE_COMMAND[stage], STAGE_SKILL[stage]
        if status == "attention":
            waiting.append(f"stage {stage} is waiting for decisions — follow skill {skill}, then run `{cmd}` again")
            continue
        if status == "running":
            step = (
                f"stage {stage} is not finished (running or interrupted) — check `babeldub status`; "
                f"rerunning `{cmd}` is safe"
            )
        elif status == "failed":
            step = f"stage {stage} failed — run `babeldub status`, fix the cause with skill {skill}, then `{cmd}`"
        else:
            step = f"`{cmd}` (skill {skill})"
        return "; ".join([step, *waiting])
    if waiting:
        return "; ".join(waiting)
    return "dub installed; after a game update run `babeldub reapply`, to roll back run `babeldub uninstall`"


def session_summary(home: Path) -> list[str]:
    active = _read_json(home / "active.json")
    if not isinstance(active, dict) or not active.get("project"):
        return []
    root = Path(str(active["project"]))
    project = _read_json(root / "project.json")
    if not isinstance(project, dict):
        return []
    progress = _read_json(root / "state" / "progress.json")
    progress = progress if isinstance(progress, dict) else {}
    tts = project.get("tts") if isinstance(project.get("tts"), dict) else {}
    marks = []
    for stage in STAGES:
        entry = progress.get(stage)
        status = entry.get("status") if isinstance(entry, dict) else None
        marks.append(f"{stage} {STATUS_MARK.get(str(status), '·')}")
    game = project.get("game_name") or project.get("slug") or root.name
    lang = project.get("target_lang") or "?"
    return [
        f"[BabelDub] Active project: {game} → {lang} (slug {project.get('slug', root.name)})",
        f"Project folder: {root}",
        f"Game folder (read-only until `babeldub install`): {project.get('game_dir', '?')}",
        "Stages: " + " · ".join(marks),
        f"Voice: {tts.get('engine', 'qwen3tts-fast')}, ref_strategy={tts.get('ref_strategy', 'auto')} — dubbed in the "
        "original hero's voice (the original line is the voice prompt).",
        f"Next step: {next_step(progress)}.",
        "Continue with the babeldub-localize skill and talk to the user in their language; "
        "summary: `babeldub status`.",
    ]


def run_session() -> int:
    lines = session_summary(babeldub_home())
    if lines:
        _write(sys.stdout, "\n".join(lines) + "\n")
    return EXIT_ALLOW


# ---------------------------------------------------------------- entry point


def _write(stream: Any, text: str) -> None:
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:
        stream.write(text)
        stream.flush()


def _read_stdin() -> str:
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    data = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode("utf-8")
    return data.decode("utf-8-sig", errors="replace")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    mode = args[0] if args else ""
    try:
        if os.environ.get("BABELDUB_GUARD", "").strip().lower() in ("0", "off", "false", "no"):
            return EXIT_ALLOW
        if mode == "pre":
            stdin_text = _read_stdin()
            return run_pre(stdin_text)
        if mode == "session":
            return run_session()
        return EXIT_ALLOW
    except Exception:  # fail-open: a broken guard must never block the user's work
        return EXIT_ALLOW


if __name__ == "__main__":
    sys.exit(main())
