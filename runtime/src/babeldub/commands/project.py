"""Project commands: init, status, set, query, sample, games.

Also hosts read helpers over the project manifests (lines + cast + translations + qa) that other
command modules reuse: load_lines, voiced_lines, merged_rows, latest_translations.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from babeldub.core import layout
from babeldub.core.games import active_project as registry_active_project
from babeldub.core.games import load_registry, register_game, set_active_project
from babeldub.core.ids import slugify
from babeldub.core.jsonl import latest_by, read_json, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_OK, EXIT_USAGE, BabelDubError, emit
from babeldub.core.paths import projects_dir
from babeldub.core.project import DEFAULT_SETTINGS, Project
from babeldub.core.takes import follower_map
from babeldub.envsetup.catalog import engine_for_language, load_catalog

Common = Callable[[argparse.ArgumentParser], None]

VOICED_KINDS = ("dialogue", "cutscene", "bark")
CAST_POLICIES = ("all", "main", "list")
TRANSLATION_MODES = ("auto", "translate", "adapt", "official")
REF_STRATEGIES = ("auto", "line", "char")
ENUMS: dict[str, tuple[str, ...]] = {
    "compute.device": ("auto", "cuda", "cpu"),
    "cast.policy": CAST_POLICIES,
    "translation.mode": TRANSLATION_MODES,
    "tts.ref_strategy": REF_STRATEGIES,
    "tts.engine": ("qwen3tts-fast", "qwen3tts", "chatterbox", "dummy"),
    "tts.tier": ("auto", "cpu", "8gb", "12gb", "24gb"),
}
PROTECTED_KEYS = {"_schema", "cli_version", "created", "slug", "game_dir", "target_lang"}
STAGE_COMMANDS: dict[str, str] = {
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
_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?$")
_WHERE_RE = re.compile(r"^\s*([\w.]+)\s*(!=|~|=)(.*)$")


def csv_list(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    return [part.strip() for item in items for part in item.split(",") if part.strip()]


# ---- manifest helpers (shared with other command modules) ---------------------------------------


def load_latest(project: Project, relpath: str, key: str = "id") -> dict[Any, dict[str, Any]]:
    return latest_by(read_jsonl(project.path(relpath)), key)


def load_lines(project: Project) -> list[dict[str, Any]]:
    """Canonical lines (latest revision of each id, in first-seen order)."""
    return list(load_latest(project, layout.LINES).values())


def latest_translations(project: Project) -> dict[str, dict[str, Any]]:
    return load_latest(project, layout.TRANSLATIONS)


def selected_char_ids(project: Project) -> set[str] | None:
    """Selected characters, or None when casting has not produced characters.jsonl yet."""
    path = project.path(layout.CHARACTERS)
    if not path.exists():
        return None
    return {cid for cid, row in latest_by(read_jsonl(path), "char_id").items() if row.get("selected")}


def is_voiced_kind(line: dict[str, Any]) -> bool:
    return line.get("kind") in VOICED_KINDS


def voiced_lines(project: Project, lines: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Lines that will be dubbed: voiced kinds, of selected characters once casting is done."""
    lines = load_lines(project) if lines is None else lines
    voiced = [ln for ln in lines if is_voiced_kind(ln)]
    selected = selected_char_ids(project)
    if selected is None:
        return voiced
    assign = load_latest(project, layout.ASSIGN)
    return [ln for ln in voiced if assign.get(ln["id"], {}).get("char_id") in selected]


def src_text(line: dict[str, Any]) -> str:
    return ((line.get("text") or {}).get("src") or "").strip()


def official_text(line: dict[str, Any]) -> str:
    return ((line.get("text") or {}).get("tgt_official") or "").strip()


def merged_rows(project: Project, lines: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Lines enriched with char/char_name (cast), translation, qa and decision (latest revisions)."""
    lines = load_lines(project) if lines is None else lines
    assign = load_latest(project, layout.ASSIGN)
    chars = load_latest(project, layout.CHARACTERS, "char_id")
    translations = latest_translations(project)
    qa = load_latest(project, layout.QA)
    decisions = load_latest(project, layout.DECISIONS)
    follows = follower_map(lines)
    rows = []
    for line in lines:
        row = dict(line)
        char_id = assign.get(line["id"], {}).get("char_id")
        row["char"] = char_id
        row["char_name"] = chars.get(char_id, {}).get("name") if char_id else None
        row["translation"] = translations.get(line["id"])
        row["qa"] = qa.get(line["id"])
        row["decision"] = decisions.get(line["id"])
        row["follows"] = follows.get(line["id"])  # leader id of an alt take / duplicate
        rows.append(row)
    return rows


def get_path(row: dict[str, Any], dotted: str) -> Any:
    value: Any = row
    for part in dotted.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


# ---- typed values for `set` ---------------------------------------------------------------------


def parse_scalar(text: str) -> Any:
    raw = text.strip()
    low = raw.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none"):
        return None
    if re.fullmatch(r"[-+]?\d+", raw):
        return int(raw)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", raw):
        return float(raw)
    return raw


def parse_value(text: str, current: Any) -> Any:
    """Type a `set` value: follow the type of the current value, else infer (bool/null/number/list/str)."""
    if isinstance(current, list):
        return [parse_scalar(p) for p in csv_list(text)]
    if isinstance(current, bool):
        value = parse_scalar(text)
        if not isinstance(value, bool):
            raise BabelDubError(f"expected true/false, got '{text}'", code=EXIT_USAGE)
        return value
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        value = parse_scalar(text)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise BabelDubError(f"expected a number, got '{text}'", code=EXIT_USAGE)
        return float(value) if isinstance(current, float) else value
    if isinstance(current, str):
        return text.strip()
    if "," in text:
        return [parse_scalar(p) for p in csv_list(text)]
    return parse_scalar(text)


# ---- init ---------------------------------------------------------------------------------------


def _default_engine(lang: str) -> tuple[str | None, list[str]]:
    catalog = load_catalog()
    engine = engine_for_language(lang, catalog)
    if engine is None:
        known = sorted({code for e in catalog.engines.values() if e.env for code in e.languages if code != "*"})
        return None, [f"no TTS engine in the catalog speaks '{lang}' (supported: {', '.join(known)})"]
    return engine, []


def cmd_init(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).expanduser()
    if not game_dir.is_dir():
        raise BabelDubError(f"game folder not found: {game_dir}", code=EXIT_USAGE)
    game_dir = game_dir.resolve()
    if not _LANG_RE.match(args.lang or ""):
        raise BabelDubError(f"bad language code '{args.lang}'", code=EXIT_USAGE, hint="use ISO 639-1, e.g. ru, pl, pt-BR")
    lang_parts = args.lang.replace("_", "-").split("-")
    target_lang = lang_parts[0].lower()

    chars = csv_list(args.chars)
    policy = args.cast or ("list" if chars else "main")
    if policy == "list" and not chars:
        raise BabelDubError("--cast list needs --chars A,B", code=EXIT_USAGE)

    slug = slugify(args.slug or game_dir.name)
    root = Path(args.project_dir).expanduser().resolve() if args.project_dir else projects_dir() / slug
    existing = read_json(root / layout.PROJECT_JSON, default=None)
    warnings: list[str] = []
    if existing:
        same_game = Path(existing.get("game_dir", "")).resolve() == game_dir
        if not same_game or existing.get("target_lang") != target_lang:
            raise BabelDubError(
                f"project folder {root} already holds {existing.get('game_name')} -> {existing.get('target_lang')}",
                code=EXIT_USAGE,
                hint="pass another --slug (one project = one game + one target language)",
            )
        project = Project(root=root, data=existing)
        created = False
    else:
        engine, warnings = _default_engine(target_lang)
        data: dict[str, Any] = {
            "slug": slug,
            "game_name": args.name or game_dir.name,
            "game_dir": game_dir.as_posix(),
            "target_lang": target_lang,
            "source_lang": None,
            "cast": {"policy": policy, "list": chars},
            "translation": {"mode": args.mode},
        }
        if len(lang_parts) > 1:
            data["target_locale"] = f"{target_lang}-{lang_parts[1].upper()}"
        if engine:
            data["tts"] = {**DEFAULT_SETTINGS["tts"], "engine": engine}
        project = Project.create(root, data)
        created = True
    register_game(game_dir, project.slug, root)
    set_active_project(root)
    if created:
        project.mark_stage("init", "done", target_lang=target_lang)
    payload: dict[str, Any] = {
        "project": str(root),
        "slug": project.slug,
        "game_dir": str(game_dir),
        "target_lang": target_lang,
        "created": created,
        "cast": project.data.get("cast"),
        "mode": project.setting("translation", "mode"),
        "engine": project.setting("tts", "engine"),
    }
    if warnings:
        payload["warnings"] = warnings
    payload["next"] = "babeldub scan"
    return emit(payload, EXIT_ATTENTION if warnings else EXIT_OK)


# ---- status -------------------------------------------------------------------------------------


def _count_latest(rows: Iterable[dict[str, Any]], key: str = "id") -> dict[Any, dict[str, Any]]:
    return latest_by(rows, key)


def project_counts(project: Project) -> dict[str, Any]:
    lines = load_lines(project)
    voiced = voiced_lines(project, lines)
    voiced_ids = {ln["id"] for ln in voiced}
    chars = load_latest(project, layout.CHARACTERS, "char_id")
    translations = latest_translations(project)
    synth = _count_latest(read_jsonl(project.path(layout.SYNTH_RESULTS)))
    post = _count_latest(read_jsonl(project.path(layout.POST)))
    qa = _count_latest(read_jsonl(project.path(layout.QA)))
    manifest = read_json(project.path(layout.BUILD_MANIFEST), default=None) or {}
    install = read_json(project.path(layout.INSTALL_LOG), default=None) or {}
    containers = manifest.get("containers") or []
    follows = follower_map(lines)
    return {
        "lines": len(lines),
        "voiced": len(voiced),
        # alt takes/duplicates: never synthesized, the build reuses their leader's dub
        "follows": sum(1 for i in voiced_ids if i in follows),
        "linked": sum(1 for ln in lines if src_text(ln)),
        "characters": len(chars),
        "selected": sum(1 for c in chars.values() if c.get("selected")),
        "translated": sum(1 for i in voiced_ids if i in translations),
        "synth_ok": sum(1 for i, r in synth.items() if r.get("status") == "ok" and i in voiced_ids),
        "post": sum(1 for i in post if i in voiced_ids),
        "qa_pass": sum(1 for r in qa.values() if r.get("verdict") == "pass"),
        "qa_fail": sum(1 for r in qa.values() if r.get("verdict") == "fail"),
        "built": len(containers),
        "replaced": sum(int(c.get("replaced") or 0) for c in containers),
        "installed": len(install.get("installed") or []) if install.get("state") == "installed" else 0,
    }


def cmd_status(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    progress = project.progress()
    stages = {stage: progress.get(stage, {}).get("status", "pending") for stage in layout.STAGES}
    # a stage in `attention` is finished with items left for a decision: the pipeline goes on
    next_stage = next((s for s in layout.STAGES if stages[s] not in ("done", "attention")), None)
    payload: dict[str, Any] = {
        "project": str(project.root),
        "slug": project.slug,
        "game": project.data.get("game_name"),
        "target_lang": project.target_lang,
        "stages": stages,
        "counts": project_counts(project),
    }
    attention = {
        stage: {k: v for k, v in progress.get(stage, {}).items() if k not in ("status", "updated")}
        for stage in layout.STAGES
        if stages[stage] in ("attention", "failed")
    }
    if attention:
        payload["attention"] = attention
    code = EXIT_ATTENTION if attention else EXIT_OK
    if next_stage is None:
        payload["next"] = None
        payload["done"] = True
        return emit(payload, code)
    payload["next_stage"] = next_stage
    payload["next"] = STAGE_COMMANDS[next_stage]
    return emit(payload, code)


# ---- set ----------------------------------------------------------------------------------------


def apply_setting(data: dict[str, Any], dotted: str, text: str) -> tuple[Any, Any, str | None]:
    """Set data[dotted] from a string; returns (old, new, warning)."""
    parts = dotted.split(".")
    if not all(parts) or parts[0] in PROTECTED_KEYS:
        raise BabelDubError(f"'{dotted}' cannot be changed with `set`", code=EXIT_USAGE,
                            hint="protected: " + ", ".join(sorted(PROTECTED_KEYS)) + " (another language = new project)")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if child is None:
            child = {}
            node[part] = child
        if not isinstance(child, dict):
            raise BabelDubError(f"'{part}' in '{dotted}' is not a section", code=EXIT_USAGE)
        node = child
    leaf = parts[-1]
    old = node.get(leaf)
    known_section = DEFAULT_SETTINGS.get(parts[0]) if len(parts) == 2 else None
    current = old if old is not None else (known_section or {}).get(leaf)
    new = parse_value(text, current)
    allowed = ENUMS.get(dotted)
    if allowed and new not in allowed:
        raise BabelDubError(f"{dotted} must be one of {list(allowed)}", code=EXIT_USAGE)
    warning = None
    if isinstance(DEFAULT_SETTINGS.get(parts[0]), dict) and len(parts) == 2 and leaf not in DEFAULT_SETTINGS[parts[0]]:
        warning = f"'{dotted}' is not a known setting"
    elif len(parts) == 1 and parts[0] not in data and parts[0] not in ("game_name", "source_lang"):
        warning = f"'{dotted}' is not a known setting"
    node[leaf] = new
    return old, new, warning


def cmd_set(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    if not args.assignments:
        raise BabelDubError("nothing to set", code=EXIT_USAGE, hint="babeldub set tts.best_of=2 qa.max_cer=0.2")
    changed: dict[str, Any] = {}
    warnings: list[str] = []
    for item in args.assignments:
        if "=" not in item:
            raise BabelDubError(f"expected section.key=value, got '{item}'", code=EXIT_USAGE)
        key, value = item.split("=", 1)
        old, new, warning = apply_setting(project.data, key.strip(), value)
        changed[key.strip()] = {"old": old, "new": new}
        if warning:
            warnings.append(warning)
    project.save()
    project.log_event("set", changed=changed)
    payload: dict[str, Any] = {"changed": changed}
    if warnings:
        payload["warnings"] = warnings
    return emit(payload, EXIT_OK)


# ---- query --------------------------------------------------------------------------------------


def parse_where(clauses: list[str]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for clause in csv_list(clauses):
        match = _WHERE_RE.match(clause)
        if not match:
            raise BabelDubError(f"bad --where clause '{clause}'", code=EXIT_USAGE,
                                hint="use field=value, field!=value or field~substring; comma separated")
        out.append((match.group(1), match.group(2), match.group(3).strip()))
    return out


_COMPUTED_FIELDS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "has_text": lambda r: bool(src_text(r)),
    "translated": lambda r: r.get("translation") is not None,
    "verdict": lambda r: (r.get("qa") or {}).get("verdict"),
    "decision": lambda r: (r.get("decision") or {}).get("decision"),
    "voiced": is_voiced_kind,
}


def _field_value(row: dict[str, Any], name: str) -> Any:
    """Value used for filtering; `char` matches the char_id or the character name."""
    if name == "char":
        return [row.get("char"), row.get("char_name")]
    if name in _COMPUTED_FIELDS:
        return _COMPUTED_FIELDS[name](row)
    return get_path(row, name)


def _output_value(row: dict[str, Any], name: str) -> Any:
    if name in _COMPUTED_FIELDS:
        return _COMPUTED_FIELDS[name](row)
    return get_path(row, name)


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _matches(value: Any, op: str, wanted: str) -> bool:
    """Lists (flags, char id/name) match when any element matches (`!=`: when none does)."""
    candidates = [_as_text(v).lower() for v in value] if isinstance(value, list) else [_as_text(value).lower()]
    w = wanted.lower()
    if op == "=":
        return w in candidates
    if op == "!=":
        return w not in candidates
    return any(w in c for c in candidates)


def _clip_value(value: Any, limit: int = 200) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


DEFAULT_FIELDS = ["id", "kind", "char", "scene", "text.src", "translation.dub_text"]


def cmd_query(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    where = parse_where(args.where or [])
    fields = csv_list(args.fields) or DEFAULT_FIELDS
    rows = merged_rows(project)
    hits = [r for r in rows if all(_matches(_field_value(r, f), op, v) for f, op, v in where)]
    selected = [{f: _output_value(r, f) for f in fields} for r in hits]
    shown = [{k: _clip_value(v) for k, v in row.items()} for row in selected[: max(0, args.limit)]]
    payload: dict[str, Any] = {"total": len(hits), "rows": shown}
    if args.out:
        out = Path(args.out).expanduser()
        write_jsonl(out, selected)
        payload["file"] = str(out)
    return emit(payload, EXIT_OK)


# ---- sample -------------------------------------------------------------------------------------


def _spread(items: list[Any], n: int) -> list[Any]:
    if n <= 0 or len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def cmd_sample(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    if not (args.scene or args.char or args.ids):
        raise BabelDubError("pick lines with --scene, --char or --ids", code=EXIT_USAGE)
    rows = merged_rows(project)
    by_id = {r["id"]: r for r in rows}
    if args.ids:
        ids = csv_list(args.ids)
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise BabelDubError("unknown line ids", code=EXIT_USAGE, ids=unknown[:10])
        picked = [by_id[i] for i in ids]
        label = "ids"
    elif args.scene:
        scene_rows = [r for r in rows if r.get("scene") == args.scene]
        scene_rows.sort(key=lambda r: (r.get("order") is None, r.get("order") or 0))
        picked = scene_rows[: args.n]
        label = f"scene-{slugify(args.scene)}"
    else:
        wanted = args.char.lower()
        char_rows = [r for r in rows if wanted in {str(r.get("char") or "").lower(), str(r.get("char_name") or "").lower(),
                                                   str(r.get("speaker_hint") or "").lower()}]
        picked = _spread(char_rows, args.n)
        label = f"char-{slugify(args.char)}"
    if not picked:
        raise BabelDubError("no lines match", code=EXIT_USAGE, hint="see `babeldub query --where ...`")
    out = Path(args.out).expanduser() if args.out else project.path(layout.LOGS_DIR, "samples", f"{label}.jsonl")
    records = []
    for r in picked:
        audio = r.get("audio") or {}
        translation = r.get("translation") or {}
        wav = audio.get("wav")
        records.append(
            {
                "id": r["id"],
                "scene": r.get("scene"),
                "order": r.get("order"),
                "kind": r.get("kind"),
                "speaker": r.get("char_name") or r.get("char") or r.get("speaker_hint"),
                "src": src_text(r) or None,
                "tgt_official": official_text(r) or None,
                "dub_text": translation.get("dub_text"),
                "dur_s": audio.get("dur_s"),
                "fixed_length": bool(r.get("fixed_length")),
                "flags": r.get("flags") or [],
                "wav": str(project.path(wav)) if wav else None,
                "verdict": (r.get("qa") or {}).get("verdict"),
            }
        )
    write_jsonl(out, records)
    return emit({"file": str(out), "n": len(records)}, EXIT_OK)


# ---- games --------------------------------------------------------------------------------------


def cmd_games(args: argparse.Namespace) -> int:
    registry = load_registry()
    active = registry_active_project()
    games = []
    for g in registry["games"]:
        project_dir = Path(g.get("project_dir", ""))
        games.append(
            {
                "slug": g.get("slug"),
                "game_dir": g.get("game_dir"),
                "project_dir": g.get("project_dir"),
                "game_exists": Path(g.get("game_dir", "")).is_dir(),
                "project_exists": (project_dir / layout.PROJECT_JSON).exists(),
                "active": active is not None and project_dir.resolve() == active.resolve(),
            }
        )
    return emit({"games": games, "active": str(active) if active else None}, EXIT_OK)


# ---- registration -------------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction, common: Common) -> None:
    p = sub.add_parser("init", help="create a localization project for a game folder and make it active")
    p.add_argument("game_dir", help="game installation folder (read-only until `babeldub install`)")
    p.add_argument("--lang", required=True, help="target language code, e.g. ru, pl, pt-BR")
    p.add_argument("--cast", choices=CAST_POLICIES, help="which characters to dub (default: main, or list with --chars)")
    p.add_argument("--chars", help="comma list of character names/ids for --cast list")
    p.add_argument("--mode", choices=TRANSLATION_MODES, default="auto", help="translation mode (default: auto)")
    p.add_argument("--slug", help="project name (default: from the folder name)")
    p.add_argument("--name", help="display name of the game (default: folder name)")
    p.add_argument("--project-dir", help="project folder (default: BABELDUB_HOME/projects/<slug>)")
    common(p)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="stages, counters and the next step")
    common(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("set", help="change project settings: section.key=value ...")
    p.add_argument("assignments", nargs="*", help="e.g. tts.best_of=2 cast.list=Anna,Bob qa.max_cer=0.2")
    common(p)
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("query", help="select lines (lines + cast + translations + qa + decisions)")
    p.add_argument("--where", action="append", help="kind=dialogue,char=Anna,has_text=true,text.src~hello,verdict=fail")
    p.add_argument("--fields", help="comma list of dotted fields (default: " + ",".join(DEFAULT_FIELDS) + ")")
    p.add_argument("--limit", type=int, default=20, help="rows shown in the output (default 20)")
    p.add_argument("--out", help="write all matching rows to this JSONL file")
    common(p)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("sample", help="write lines with context to a JSONL file for an agent")
    p.add_argument("--scene", help="all lines of a scene, in order")
    p.add_argument("--char", help="lines of a character (spread over the game)")
    p.add_argument("--ids", help="comma list of line ids")
    p.add_argument("--n", type=int, default=30, help="number of lines (default 30)")
    p.add_argument("--out", help="output file (default: <project>/logs/samples/<label>.jsonl)")
    common(p)
    p.set_defaults(func=cmd_sample)

    p = sub.add_parser("games", help="registry of game folders and their projects")
    common(p)
    p.set_defaults(func=cmd_games)
