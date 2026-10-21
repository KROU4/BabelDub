"""Translation commands: translate plan|use-official, batch make|next|submit, glossary check|apply.

The CLI never translates. It decides the mode, cuts the voiced lines into batches with everything an
agent needs (speaker, neighbours, duration budget in syllables, placeholders, glossary, style bible),
validates what the agent wrote and merges it into translate/translations.jsonl (single writer).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Callable
from itertools import groupby
from pathlib import Path
from typing import Any

from babeldub.commands.project import (
    csv_list,
    latest_translations,
    load_latest,
    load_lines,
    official_text,
    src_text,
    voiced_lines,
)
from babeldub.core import layout
from babeldub.core.jsonl import (
    append_jsonl,
    latest_by,
    now_iso,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from babeldub.core.output import EXIT_ATTENTION, EXIT_OK, EXIT_USAGE, BabelDubError, clip, emit
from babeldub.core.project import Project
from babeldub.text.placeholders import check_placeholders, describe, extract_placeholders, has_errors
from babeldub.text.syllables import budget_syllables, count_syllables, fit_status

Common = Callable[[argparse.ArgumentParser], None]

OFFICIAL_SHARE = 0.95
OVER_FACTOR = 1.15
TRANSLATIONS_SCHEMA = "babeldub.translations/1"
GLOSSARY_SCHEMA = "babeldub.glossary/1"
BATCH_INDEX = f"{layout.BATCHES_DIR}/index.json"
GLOSSARY_VIOLATIONS = "translate/glossary_violations.jsonl"
MISSING_OFFICIAL = "translate/missing_official.txt"
STYLE_LIMIT = 8000
MAX_ERRORS_SHOWN = 20
_BATCH_ID_RE = re.compile(r"^b\d{4,}$")

RULES = (
    "Write {out}: one JSON object per line {{id, dub_text, sub_text?, variants?, notes?}} for EVERY id below. "
    "dub_text is what the original character says in {lang}: natural spoken {lang} with the same meaning, emotion, "
    "register and delivery as src (the voice is cloned from the original line, so the performance must match). "
    "Keep every token from `placeholders` exactly (tags, {{vars}}, %s); line breaks may move. Aim at budget_syll "
    "syllables: more than 115% is flagged, fixed_length lines must fit. variants = shorter alternatives, best first. "
    "sub_text only if the subtitle must differ from dub_text."
)


# ---- shared helpers -----------------------------------------------------------------------------


def configured_mode(project: Project) -> str:
    return str(project.setting("translation", "mode", "auto") or "auto")


def decide_mode(project: Project, voiced: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """auto -> official when >=95% of the linked voiced lines have an official target text, else translate."""
    configured = configured_mode(project)
    linked = [ln for ln in voiced if src_text(ln) or official_text(ln)]
    with_official = sum(1 for ln in linked if official_text(ln))
    share = round(with_official / len(linked), 4) if linked else 0.0
    if configured == "auto":
        mode = "official" if linked and share >= OFFICIAL_SHARE else "translate"
    else:
        mode = configured
    stats = {
        "lines": len(voiced),
        "linked": len(linked),
        "with_official": with_official,
        "official_share": share,
        "no_text": len(voiced) - len(linked),
    }
    return mode, stats


def current_mode(project: Project, voiced: list[dict[str, Any]] | None = None) -> str:
    configured = configured_mode(project)
    if configured != "auto":
        return configured
    resolved = project.setting("translation", "resolved_mode")
    if resolved:
        return str(resolved)
    return decide_mode(project, voiced if voiced is not None else voiced_lines(project))[0]


def needs_translation(line: dict[str, Any], mode: str, current: dict[str, Any] | None) -> bool:
    """Line still lacks the translation the mode asks for (lines without any text are never pending)."""
    if not (src_text(line) or official_text(line)):
        return False
    if mode == "adapt":
        return current is None or current.get("by") == "official"
    return current is None


def needs_agent(line: dict[str, Any], mode: str, current: dict[str, Any] | None) -> bool:
    if not needs_translation(line, mode, current) or not src_text(line):
        return False
    if mode == "official":
        return not official_text(line)
    return True


def line_dur(line: dict[str, Any]) -> float | None:
    dur = (line.get("audio") or {}).get("dur_s")
    return float(dur) if isinstance(dur, (int, float)) else None


def make_translation(
    line: dict[str, Any], dub_text: str, sub_text: str | None, lang: str, by: str, rev: int, **extra: Any
) -> dict[str, Any]:
    est = count_syllables(dub_text, lang)
    budget = budget_syllables(line_dur(line), lang)
    row = {
        "id": line["id"],
        "dub_text": dub_text,
        "sub_text": sub_text if sub_text is not None else dub_text,
        "lang": lang,
        "by": by,
        "rev": rev,
        "ts": now_iso(),
        "est_syll": est,
        "budget_syll": budget,
        "fit": fit_status(est, budget, over=OVER_FACTOR),
    }
    row.update({k: v for k, v in extra.items() if v is not None})
    return row


def append_translations(project: Project, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    path = project.path(layout.TRANSLATIONS)
    if not path.exists():
        return write_jsonl(path, rows, schema=TRANSLATIONS_SCHEMA)
    return append_jsonl(path, rows)


def refresh_stage(project: Project, mode: str | None = None) -> dict[str, Any]:
    """Recompute translation coverage and record it in progress.json."""
    voiced = voiced_lines(project)
    mode = mode or current_mode(project, voiced)
    translations = latest_translations(project)
    remaining = [ln for ln in voiced if needs_translation(ln, mode, translations.get(ln["id"]))]
    no_text = sum(1 for ln in voiced if not (src_text(ln) or official_text(ln)))
    summary = {
        "mode": mode,
        "lines": len(voiced),
        "translated": sum(1 for ln in voiced if ln["id"] in translations),
        "remaining": len(remaining),
        "no_text": no_text,
    }
    status = "attention" if not voiced else ("done" if not remaining else "running")
    project.mark_stage("translate", status, **summary)
    return summary


def load_glossary(project: Project) -> list[dict[str, Any]]:
    rows = latest_by(read_jsonl(project.path(layout.GLOSSARY)), "term")
    return [r for r in rows.values() if r.get("term") and r.get("translation")]


def _term_regex(term: str) -> re.Pattern[str]:
    """Whole-word match that tolerates short endings (Repair -> Repairs), not other words (Ann -> Annual)."""
    folded = _fold(term)
    ending = r"\w{0,2}" if len(folded) > 3 else r"s?"
    return re.compile(r"(?<!\w)" + re.escape(folded) + ending + r"(?!\w)", re.IGNORECASE)


def _fold(text: str) -> str:
    return text.lower().replace("ё", "е")


def term_in_source(term: str, text: str) -> bool:
    return bool(text) and bool(_term_regex(term).search(_fold(text)))


def _stem(word: str) -> str:
    """Crude stem for inflected languages: Збышек -> Збыш(ека), ёлка -> ёлк(ой)."""
    if len(word) > 4:
        return word[: max(3, len(word) - 2)]
    if len(word) == 4:
        return word[:3]
    return word


def translation_present(translation: str, text: str) -> bool:
    """Every word of the glossary translation appears in the text, allowing inflected endings."""
    folded = _fold(text or "")
    for word in re.findall(r"\w+", _fold(translation)):
        if not re.search(r"(?<!\w)" + re.escape(_stem(word)) + r"\w{0,4}(?!\w)", folded):
            return False
    return True


# ---- translate plan / use-official --------------------------------------------------------------


def _require_lines(project: Project) -> list[dict[str, Any]]:
    lines = load_lines(project)
    if not lines:
        raise BabelDubError("no lines yet", code=EXIT_USAGE, hint="run `babeldub extract` and `babeldub link` first")
    return lines


def cmd_translate_plan(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    lines = _require_lines(project)
    voiced = voiced_lines(project, lines)
    mode, stats = decide_mode(project, voiced)
    translations = latest_translations(project)
    need_agent = [ln for ln in voiced if needs_agent(ln, mode, translations.get(ln["id"]))]
    pending_official = [
        ln for ln in voiced if mode == "official" and official_text(ln) and ln["id"] not in translations
    ]
    project.data.setdefault("translation", {})["resolved_mode"] = mode
    project.save()
    summary = refresh_stage(project, mode)
    payload: dict[str, Any] = {
        "mode": mode,
        "configured": configured_mode(project),
        **stats,
        "translated": summary["translated"],
        "need_agent": len(need_agent),
        "glossary": len(load_glossary(project)),
        "style_bible": project.path(layout.STYLE_BIBLE).exists(),
    }
    if not voiced:
        payload["next"] = "nothing to dub: check `babeldub cast` selection and line kinds"
        return emit(payload, EXIT_ATTENTION)
    if stats["no_text"]:
        payload["warning"] = f"{stats['no_text']} voiced lines have no text (run `babeldub link`; they cannot be dubbed)"
    if pending_official:
        payload["next"] = "babeldub translate use-official"
    elif need_agent:
        payload["next"] = "babeldub batch make"
    else:
        payload["next"] = "babeldub glossary check"
    return emit(payload, EXIT_OK)


def cmd_use_official(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    lines = _require_lines(project)
    voiced = voiced_lines(project, lines)
    lang = project.target_lang
    translations = latest_translations(project)
    rows: list[dict[str, Any]] = []
    unchanged = kept = 0
    missing: list[str] = []
    for line in voiced:
        official = official_text(line)
        current = translations.get(line["id"])
        if not official:
            if current is None and src_text(line):
                missing.append(line["id"])
            continue
        if current and current.get("by") in ("agent", "human") and not args.force:
            kept += 1
            continue
        if current and current.get("by") == "official" and current.get("dub_text") == official:
            unchanged += 1
            continue
        rev = int(current.get("rev") or 0) + 1 if current else 1
        rows.append(make_translation(line, official, official, lang, "official", rev))
    append_translations(project, rows)
    if missing:
        path = project.path(MISSING_OFFICIAL)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(missing) + "\n", encoding="utf-8", newline="\n")
    summary = refresh_stage(project)
    payload: dict[str, Any] = {
        "copied": len(rows),
        "unchanged": unchanged,
        "kept_agent": kept,
        "missing": len(missing),
        "over_budget": sum(1 for r in rows if r["fit"] == "over"),
        "remaining": summary["remaining"],
    }
    if missing:
        payload["missing_ids"] = clip(missing, 5)
        payload["missing_file"] = str(project.path(MISSING_OFFICIAL))
        payload["next"] = "babeldub batch make"
        return emit(payload, EXIT_ATTENTION)
    payload["next"] = "babeldub glossary check"
    return emit(payload, EXIT_OK)


# ---- batches ------------------------------------------------------------------------------------


def load_index(project: Project) -> dict[str, Any]:
    data = read_json(project.path(BATCH_INDEX), default=None) or {}
    data.setdefault("batches", {})
    return data


def save_index(project: Project, index: dict[str, Any]) -> None:
    write_json(project.path(BATCH_INDEX), index)


def batch_paths(project: Project, bid: str) -> tuple[Path, Path, Path]:
    base = project.path(layout.BATCHES_DIR)
    return base / f"{bid}.in.jsonl", base / f"{bid}.out.jsonl", base / f"{bid}.errors.json"


def _speaker(line: dict[str, Any], assign: dict[str, Any], chars: dict[str, Any]) -> str | None:
    char_id = assign.get(line["id"], {}).get("char_id")
    if char_id:
        return chars.get(char_id, {}).get("name") or char_id
    return line.get("speaker_hint")


def _scene_ranks(lines: list[dict[str, Any]]) -> dict[str, int]:
    """Scenes in the order the game lists them (first appearance), not alphabetically."""
    ranks: dict[str, int] = {}
    for line in lines:
        ranks.setdefault(str(line.get("scene") or ""), len(ranks))
    return ranks


def _order_key(line: dict[str, Any], pos: int, ranks: dict[str, int]) -> tuple[Any, ...]:
    order = line.get("order")
    numeric = order if isinstance(order, (int, float)) else 0
    return (ranks.get(str(line.get("scene") or ""), len(ranks)), order is None, numeric, pos)


def _context(lines: list[dict[str, Any]], assign: dict[str, Any], chars: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """id -> (previous, next) line text of the same scene, as "Speaker: text"."""
    ranks = _scene_ranks(lines)
    with_text = [(i, ln) for i, ln in enumerate(lines) if src_text(ln)]
    with_text.sort(key=lambda p: _order_key(p[1], p[0], ranks))
    ctx: dict[str, tuple[Any, Any]] = {}

    def fmt(line: dict[str, Any] | None) -> str | None:
        if line is None:
            return None
        speaker = _speaker(line, assign, chars)
        return f"{speaker}: {src_text(line)}" if speaker else src_text(line)

    for k, (_, line) in enumerate(with_text):
        scene = line.get("scene")
        prev = with_text[k - 1][1] if k > 0 and with_text[k - 1][1].get("scene") == scene else None
        nxt = with_text[k + 1][1] if k + 1 < len(with_text) and with_text[k + 1][1].get("scene") == scene else None
        ctx[line["id"]] = (fmt(prev), fmt(nxt))
    return ctx


def _chunk(candidates: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    """Cut lines (sorted by scene/order) into batches of <= size, keeping small scenes together."""
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for _, group in groupby(candidates, key=lambda ln: str(ln.get("scene") or "")):
        items = list(group)
        if current and len(current) + len(items) > size:
            batches.append(current)
            current = []
        while len(items) > size:
            batches.append(items[:size])
            items = items[size:]
        current.extend(items)
    if current:
        batches.append(current)
    return batches


def _decision_ids(project: Project) -> dict[str, str]:
    decisions = load_latest(project, layout.DECISIONS)
    return {i: d["decision"] for i, d in decisions.items() if d.get("decision") in ("retranslate", "shorten")}


def _next_batch_number(index: dict[str, Any]) -> int:
    numbers = [int(b[1:]) for b in index["batches"] if _BATCH_ID_RE.match(b)]
    return max(numbers, default=0) + 1


def cmd_batch_make(args: argparse.Namespace) -> int:
    if args.size < 1:
        raise BabelDubError("--size must be positive", code=EXIT_USAGE)
    project = Project.load(args.project)
    lines = _require_lines(project)
    voiced = voiced_lines(project, lines)
    mode = current_mode(project, voiced)
    lang = project.target_lang
    translations = latest_translations(project)
    assign = load_latest(project, layout.ASSIGN)
    chars = load_latest(project, layout.CHARACTERS, "char_id")
    index = load_index(project)

    removed: list[str] = []
    if args.force:
        for bid, meta in list(index["batches"].items()):
            if meta.get("status") != "merged":
                for path in batch_paths(project, bid):
                    path.unlink(missing_ok=True)
                del index["batches"][bid]
                removed.append(bid)
    open_ids = {i for meta in index["batches"].values() if meta.get("status") != "merged" for i in meta.get("ids", [])}

    tasks: dict[str, str] = {}
    skipped_open: list[str] = []
    if args.ids or args.from_decisions:
        by_id = {ln["id"]: ln for ln in lines}
        wanted = {i: "retranslate" for i in csv_list(args.ids)}
        if args.from_decisions:
            wanted.update(_decision_ids(project))
        unknown = [i for i in wanted if i not in by_id]
        if unknown:
            raise BabelDubError("unknown line ids", code=EXIT_USAGE, ids=clip(unknown, 10))
        no_src = [i for i in wanted if not src_text(by_id[i])]
        if no_src:
            raise BabelDubError("lines without source text cannot be translated", code=EXIT_USAGE, ids=clip(no_src, 10))
        skipped_open = [i for i in wanted if i in open_ids]
        candidates = [by_id[i] for i in wanted if i not in open_ids]
        tasks = dict(wanted)
    else:
        candidates = [ln for ln in voiced if ln["id"] not in open_ids and needs_agent(ln, mode, translations.get(ln["id"]))]
        task = "adapt" if mode == "adapt" else "translate"
        tasks = {ln["id"]: task for ln in candidates}

    positions = {ln["id"]: i for i, ln in enumerate(lines)}
    ranks = _scene_ranks(lines)
    candidates.sort(key=lambda ln: _order_key(ln, positions.get(ln["id"], 0), ranks))
    context = _context(lines, assign, chars)
    glossary = load_glossary(project)
    style_path = project.path(layout.STYLE_BIBLE)
    style = style_path.read_text(encoding="utf-8") if style_path.exists() else None
    if style and len(style) > STYLE_LIMIT:
        style = style[:STYLE_LIMIT] + f"\n[... truncated, full text in {layout.STYLE_BIBLE}]"

    made: list[str] = []
    number = _next_batch_number(index)
    for chunk in _chunk(candidates, args.size):
        bid = f"b{number:04d}"
        number += 1
        in_path, out_path, _ = batch_paths(project, bid)
        scenes = list(dict.fromkeys(str(ln.get("scene")) for ln in chunk if ln.get("scene")))
        hits = [g for g in glossary if any(term_in_source(g["term"], src_text(ln)) for ln in chunk)]
        header = {
            "_batch": bid,
            "scene": scenes[0] if len(scenes) == 1 else None,
            "scenes": scenes,
            "target_lang": lang,
            "source_lang": project.data.get("source_lang"),
            "mode": mode,
            "n": len(chunk),
            "glossary": [{k: g[k] for k in ("term", "translation", "pronounce", "note") if g.get(k)} for g in hits],
            "style": style,
            "out": out_path.name,
            "rules": RULES.format(out=out_path.name, lang=lang),
        }
        rows = [header]
        for ln in chunk:
            dur = line_dur(ln)
            prev, nxt = context.get(ln["id"], (None, None))
            row: dict[str, Any] = {
                "id": ln["id"],
                "key": (ln.get("text") or {}).get("key") or (ln.get("entry") or {}).get("key"),
                "speaker": _speaker(ln, assign, chars),
                "src": src_text(ln),
                "tgt_official": official_text(ln) or None,
                "dur_s": dur,
                "budget_syll": budget_syllables(dur, lang),
                "fixed_length": bool(ln.get("fixed_length")),
                "placeholders": extract_placeholders(src_text(ln)),
                "context_prev": prev,
                "context_next": nxt,
            }
            task = tasks.get(ln["id"], "translate")
            if task != "translate":
                row["task"] = task
                current = translations.get(ln["id"])
                if current:
                    row["current"] = current.get("dub_text")
            rows.append(row)
        write_jsonl(in_path, rows)
        index["batches"][bid] = {
            "n": len(chunk),
            "ids": [ln["id"] for ln in chunk],
            "scenes": scenes,
            "mode": mode,
            "status": "open",
            "created": now_iso(),
        }
        made.append(bid)
    save_index(project, index)
    if made:
        project.mark_stage("translate", "running", mode=mode, batches=len(index["batches"]))
    open_batches = [b for b, m in index["batches"].items() if m.get("status") != "merged"]
    payload: dict[str, Any] = {
        "mode": mode,
        "batches": clip(made, 20),
        "lines": len(candidates),
        "open": len(open_batches),
        "dir": str(project.path(layout.BATCHES_DIR)),
    }
    if removed:
        payload["removed"] = clip(removed, 10)
    if skipped_open:
        payload["skipped_in_open_batches"] = clip(skipped_open, 10)
    payload["next"] = "babeldub batch next" if open_batches else "babeldub glossary check"
    return emit(payload, EXIT_OK)


def _batch_state(project: Project, bid: str, meta: dict[str, Any]) -> str:
    _, out_path, _ = batch_paths(project, bid)
    if meta.get("status") == "error":
        return "error"
    return "ready" if out_path.exists() else "new"


def cmd_batch_next(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    index = load_index(project)
    pending = [(b, m) for b, m in sorted(index["batches"].items()) if m.get("status") != "merged"]
    if not pending:
        payload: dict[str, Any] = {"batch": None, "done": True, "total": len(index["batches"])}
        payload["next"] = "babeldub glossary check" if index["batches"] else "babeldub batch make"
        return emit(payload, EXIT_OK)
    bid, meta = pending[0]
    in_path, out_path, err_path = batch_paths(project, bid)
    state = _batch_state(project, bid, meta)
    payload = {
        "batch": bid,
        "state": state,
        "n": meta.get("n"),
        "scenes": clip(meta.get("scenes") or [], 5),
        "in": str(in_path),
        "out": str(out_path),
        "remaining": len(pending),
    }
    if state == "error":
        payload["errors_file"] = str(err_path)
        payload["next"] = f"fix {out_path.name} (see errors_file), then `babeldub batch submit {bid}`"
    elif state == "ready":
        payload["next"] = f"babeldub batch submit {bid}"
    else:
        payload["next"] = f"translate {in_path.name} into {out_path.name}, then `babeldub batch submit {bid}`"
    return emit(payload, EXIT_OK)


def _read_batch_in(path: Path) -> list[dict[str, Any]]:
    return [r for r in read_jsonl(path) if "_batch" not in r]


def _read_out(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append({"line": lineno, "error": f"invalid JSON: {exc.msg}"})
                continue
            if not isinstance(row, dict):
                errors.append({"line": lineno, "error": "not a JSON object"})
                continue
            if "_batch" in row or "_schema" in row:
                continue
            row["_line"] = lineno
            rows.append(row)
    return rows, errors


def validate_out(in_rows: list[dict[str, Any]], out_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], int]:
    """Returns (accepted rows by id, errors, warnings count). Later duplicates of an id win."""
    errors: list[dict[str, Any]] = []
    warnings = 0
    expected = {r["id"]: r for r in in_rows}
    accepted: dict[str, dict[str, Any]] = {}
    for row in out_rows:
        rid = row.get("id")
        where = {"line": row.get("_line"), "id": rid}
        if not isinstance(rid, str) or not rid:
            errors.append({**where, "error": "missing id"})
            continue
        if rid not in expected:
            errors.append({**where, "error": "id is not in this batch"})
            continue
        dub = row.get("dub_text")
        if not isinstance(dub, str) or not dub.strip():
            errors.append({**where, "error": "dub_text is empty"})
            continue
        sub = row.get("sub_text")
        if sub is not None and not isinstance(sub, str):
            errors.append({**where, "error": "sub_text must be a string"})
            continue
        variants = row.get("variants")
        if variants is not None and (not isinstance(variants, list) or not all(isinstance(v, str) and v.strip() for v in variants)):
            errors.append({**where, "error": "variants must be a list of non-empty strings"})
            continue
        src = expected[rid].get("src") or ""
        bad = False
        for label, text in (("dub_text", dub), ("sub_text", sub), *[(f"variants[{i}]", v) for i, v in enumerate(variants or [])]):
            if text is None:
                continue
            problems = check_placeholders(src, text)
            if has_errors(problems):
                errors.append({**where, "error": f"{label}: placeholders differ from src: {describe([p for p in problems if p['severity'] == 'error'])}"})
                bad = True
                break
            warnings += len(problems)
        if not bad:
            accepted[rid] = row
    for rid in expected:
        if rid not in accepted and not any(e.get("id") == rid for e in errors):
            errors.append({"id": rid, "error": "missing in .out"})
    return accepted, errors, warnings


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def cmd_batch_submit(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    bid = args.batch
    index = load_index(project)
    meta = index["batches"].get(bid)
    if meta is None:
        raise BabelDubError(f"unknown batch '{bid}'", code=EXIT_USAGE, hint="see `babeldub batch next`")
    in_path, out_path, err_path = batch_paths(project, bid)
    if not out_path.exists():
        raise BabelDubError(f"{out_path.name} not found", code=EXIT_USAGE, hint=f"write the translations to {out_path}")
    out_sha = _sha1(out_path)
    if meta.get("status") == "merged" and meta.get("out_sha1") == out_sha and not args.force:
        return emit({"batch": bid, "merged": 0, "already_merged": True, "next": "babeldub batch next"}, EXIT_OK)

    in_rows = _read_batch_in(in_path)
    out_rows, parse_errors = _read_out(out_path)
    accepted, errors, warnings = validate_out(in_rows, out_rows)
    errors = parse_errors + errors
    if errors:
        write_json(err_path, {"batch": bid, "checked": now_iso(), "errors": errors})
        meta.update({"status": "error", "errors": len(errors), "checked": now_iso()})
        save_index(project, index)
        return emit(
            {
                "batch": bid,
                "merged": 0,
                "errors": clip([f"{e.get('id') or 'line ' + str(e.get('line'))}: {e['error']}" for e in errors], MAX_ERRORS_SHOWN),
                "errors_total": len(errors),
                "errors_file": str(err_path),
                "next": f"fix {out_path.name}, then `babeldub batch submit {bid}`",
            },
            EXIT_USAGE,
        )

    lang = project.target_lang
    lines = {ln["id"]: ln for ln in load_lines(project)}
    translations = latest_translations(project)
    rows: list[dict[str, Any]] = []
    over: list[str] = []
    over_fixed: list[str] = []
    picked_variants = 0
    for in_row in in_rows:
        out = accepted[in_row["id"]]
        line = lines.get(in_row["id"]) or {"id": in_row["id"], "audio": {"dur_s": in_row.get("dur_s")}}
        dub = out["dub_text"].strip()
        variants = [v.strip() for v in out.get("variants") or []]
        picked = None
        budget = budget_syllables(line_dur(line), lang)
        if fit_status(count_syllables(dub, lang), budget, over=OVER_FACTOR) == "over":
            for i, variant in enumerate(variants):
                if fit_status(count_syllables(variant, lang), budget, over=OVER_FACTOR) != "over":
                    dub, picked = variant, i
                    picked_variants += 1
                    break
        current = translations.get(in_row["id"])
        rev = int(current.get("rev") or 0) + 1 if current else 1
        sub = out.get("sub_text")
        row = make_translation(
            line, dub, sub.strip() if isinstance(sub, str) and sub.strip() else None, lang, "agent", rev,
            batch=bid, variants=variants or None, picked_variant=picked, notes=out.get("notes") or None,
        )
        if picked is not None:
            row["agent_text"] = out["dub_text"].strip()
        if row["fit"] == "over":
            over.append(row["id"])
            if line.get("fixed_length"):
                over_fixed.append(row["id"])
        rows.append(row)
    append_translations(project, rows)
    err_path.unlink(missing_ok=True)
    meta.update({"status": "merged", "out_sha1": out_sha, "submitted": now_iso(), "over_budget": len(over)})
    meta.pop("errors", None)
    save_index(project, index)
    summary = refresh_stage(project)
    open_batches = sum(1 for m in index["batches"].values() if m.get("status") != "merged")
    payload: dict[str, Any] = {
        "batch": bid,
        "merged": len(rows),
        "over_budget": clip(over, MAX_ERRORS_SHOWN),
        "over_fixed": clip(over_fixed, MAX_ERRORS_SHOWN),
        "picked_variants": picked_variants,
        "placeholder_warnings": warnings,
        "open_batches": open_batches,
        "remaining": summary["remaining"],
    }
    if over_fixed:
        payload["hint"] = "fixed_length lines over budget will be cut in-game: resubmit shorter dub_text or variants"
    payload["next"] = "babeldub batch next" if open_batches else "babeldub glossary check"
    return emit(payload, EXIT_ATTENTION if over else EXIT_OK)


# ---- glossary -----------------------------------------------------------------------------------


def cmd_glossary_check(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    glossary = load_glossary(project)
    if not glossary:
        payload: dict[str, Any] = {"terms": 0, "checked": 0, "violations": []}
        if project.path(layout.GLOSSARY_AGENT).exists():
            payload["next"] = "babeldub glossary apply"
            return emit(payload, EXIT_ATTENTION)
        payload["next"] = "babeldub synth"
        return emit(payload, EXIT_OK)
    lines = {ln["id"]: ln for ln in load_lines(project)}
    translations = latest_translations(project)
    patterns = [(g, _term_regex(g["term"])) for g in glossary]
    violations: list[dict[str, Any]] = []
    checked = 0
    for rid, tr in translations.items():
        line = lines.get(rid)
        if line is None:
            continue
        src = _fold(src_text(line))
        if not src:
            continue
        checked += 1
        for g, regex in patterns:
            if regex.search(src) and not translation_present(g["translation"], tr.get("dub_text") or ""):
                violations.append({"id": rid, "term": g["term"], "expected": g["translation"], "by": tr.get("by"),
                                   "dub_text": tr.get("dub_text")})
    write_jsonl(project.path(GLOSSARY_VIOLATIONS), violations)
    payload = {
        "terms": len(glossary),
        "checked": checked,
        "violations": clip([f"{v['id']}: {v['term']} -> {v['expected']}" for v in violations], MAX_ERRORS_SHOWN),
        "total": len(violations),
        "file": str(project.path(GLOSSARY_VIOLATIONS)),
    }
    if violations:
        payload["next"] = "fix the lines (batch make --ids <ids>) or extend the glossary, then check again"
        return emit(payload, EXIT_ATTENTION)
    payload["next"] = "babeldub synth"
    return emit(payload, EXIT_OK)


def cmd_glossary_apply(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    agent_path = project.path(layout.GLOSSARY_AGENT)
    if not agent_path.exists():
        raise BabelDubError(f"{layout.GLOSSARY_AGENT} not found", code=EXIT_USAGE,
                            hint="write {term, translation, pronounce?, note?} per line there")
    errors: list[str] = []
    incoming: list[dict[str, Any]] = []
    try:
        rows = read_jsonl(agent_path)
    except ValueError as exc:
        raise BabelDubError(str(exc), code=EXIT_USAGE) from exc
    for n, row in enumerate(rows, 1):
        term, translation = row.get("term"), row.get("translation")
        if not isinstance(term, str) or not term.strip() or not isinstance(translation, str) or not translation.strip():
            errors.append(f"row {n}: term and translation must be non-empty strings")
            continue
        clean = {"term": term.strip(), "translation": translation.strip()}
        for key in ("pronounce", "note"):
            if isinstance(row.get(key), str) and row[key].strip():
                clean[key] = row[key].strip()
        incoming.append(clean)
    if errors:
        return emit({"errors": clip(errors, MAX_ERRORS_SHOWN), "applied": 0}, EXIT_USAGE)
    current = latest_by(read_jsonl(project.path(layout.GLOSSARY)), "term")
    added = sum(1 for r in incoming if r["term"] not in current)
    updated = sum(1 for r in incoming if r["term"] in current and current[r["term"]] != r)
    for row in incoming:
        current[row["term"]] = row
    write_jsonl(project.path(layout.GLOSSARY), list(current.values()), schema=GLOSSARY_SCHEMA)
    return emit({"added": added, "updated": updated, "total": len(current), "next": "babeldub glossary check"}, EXIT_OK)


# ---- registration -------------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction, common: Common) -> None:
    tr = sub.add_parser("translate", help="translation mode and official translations")
    tsub = tr.add_subparsers(dest="translate_command", metavar="<action>")
    tsub.required = True
    p = tsub.add_parser("plan", help="decide official/translate/adapt and count what the agent must translate")
    common(p)
    p.set_defaults(func=cmd_translate_plan)
    p = tsub.add_parser("use-official", help="copy official target texts into translations.jsonl (by=official)")
    p.add_argument("--force", action="store_true", help="also replace agent/human translations")
    common(p)
    p.set_defaults(func=cmd_use_official)

    batch = sub.add_parser("batch", help="translation batches for the agent: make | next | submit")
    bsub = batch.add_subparsers(dest="batch_command", metavar="<action>")
    bsub.required = True
    p = bsub.add_parser("make", help="cut untranslated voiced lines into batches by scene")
    p.add_argument("--size", type=int, default=60, help="lines per batch (default 60)")
    p.add_argument("--ids", help="comma list of line ids to (re)translate regardless of their state")
    p.add_argument("--from-decisions", action="store_true", help="add lines with QA decision retranslate/shorten")
    p.add_argument("--force", action="store_true", help="drop unmerged batches and cut them again")
    common(p)
    p.set_defaults(func=cmd_batch_make)
    p = bsub.add_parser("next", help="first batch without a merged .out")
    common(p)
    p.set_defaults(func=cmd_batch_next)
    p = bsub.add_parser("submit", help="validate <bid>.out.jsonl and merge it into translations.jsonl")
    p.add_argument("batch", help="batch id, e.g. b0001")
    p.add_argument("--force", action="store_true", help="merge again even if this .out was merged already")
    common(p)
    p.set_defaults(func=cmd_batch_submit)

    gl = sub.add_parser("glossary", help="glossary: check | apply")
    gsub = gl.add_subparsers(dest="glossary_command", metavar="<action>")
    gsub.required = True
    p = gsub.add_parser("check", help="glossary terms present in source must be translated as agreed")
    common(p)
    p.set_defaults(func=cmd_glossary_check)
    p = gsub.add_parser("apply", help="merge glossary.agent.jsonl into glossary.jsonl")
    common(p)
    p.set_defaults(func=cmd_glossary_apply)
