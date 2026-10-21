"""Link voice lines with text rows: exact key -> normalized key -> file name.

Games usually share one identifier between the audio and the subtitle (Low-Budget Repairs: FMOD
subsound name == Unity Localization key). Matching runs in three passes and stops at the first hit:
  1. exact     - entry.key (or entry.name) equals a text key
  2. normalized - both sides lowercased, without folder/event prefixes, without a known file
                  extension and without take suffixes (_v1, _v2, _best, _alt, _take3, _final)
  3. filename  - the stem of entry.file / entry.name against text keys and against an optional
                  `audio`/`file`/`voice` field of text rows (tables that name their audio file)
The spoken text drops subtitle-only markup: a leading speaker label made of placeholders
("{ColorStart}{CharacterName}:{ColorEnd} "), rich-text tags (<i>, <color=...>) and Ren'Py text
tags ({i}, {w}); the raw string is kept in src_raw/tgt_raw when it differs.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from babeldub.core import layout
from babeldub.core.jsonl import read_jsonl, write_jsonl
from babeldub.core.output import EXIT_USAGE, BabelDubError
from babeldub.core.project import Project
from babeldub.recon.langs import lang_of

KNOWN_EXTS = frozenset(
    ".wav .ogg .mp3 .flac .opus .m4a .aac .wem .fsb .bnk .bank .fuz .xwm .lip .vsnd .vsnd_c .uasset .uexp "
    ".asset .bytes .txt .json .csv .xml .rpy .rpyc .po .mo .locres .strings .dat .snd .wma .aif .aiff".split()
)
_SUFFIX_RE = re.compile(r"(?:[_\-.](?:v\d+|best|alt\d*|take\d+|final|edit\d*))+$", re.I)
_COPY_MARK_RE = re.compile(r"\s*\(\d+\)")
_SEPARATORS_RE = re.compile(r"[\s.\-_]+")
_BEST_RE = re.compile(r"(?:^|[_\-. ])best(?:$|[_\-. ])", re.I)
_VERSION_RE = re.compile(r"[_\-. ]v(\d+)(?:$|[_\-. ])", re.I)
_PREFIX_SPLIT_RE = re.compile(r"[\\/:]")
_SPEAKER_PREFIX_RE = re.compile(r"^\s*(?:\{[^{}]*\}\s*)+:\s*(?:\{[^{}]*\}\s*)*")
_RICH_TAG_RE = re.compile(
    r"</?(?:color|b|i|u|s|size|font|material|sprite|link|mark|sup|sub|style|alpha|cspace|mspace|voffset|nobr|"
    r"noparse|lowercase|uppercase|smallcaps|align|indent|line-height|pos|margin|rotate|width|gradient)(?:=[^>]*)?>",
    re.I,
)
_RENPY_TAG_RE = re.compile(
    r"\{/?(?:b|i|u|s|plain|a|alpha|color|cps|font|k|rb|rt|size|space|vspace|w|p|nw|fast|done|clear)(?:=[^}]*)?\}"
)
PRESERVE_SOURCES = frozenset({"asr", "agent", "human", "manual"})
_DIALOGUE_TABLE_RE = re.compile(r"dialog|subtit|voice|speech|cutscene|line|vo(?:[_\-]|$)", re.I)


def norm_key(key: str) -> str:
    """Comparable form of a key or file name (see module docstring, pass 2).

    Also separator-insensitive ('.', '-', ' ' == '_') and blind to copy markers like '(2)'.
    """
    text = _PREFIX_SPLIT_RE.split(key.strip())[-1].lower()
    root, dot, ext = text.rpartition(".")
    if dot and root and f".{ext}" in KNOWN_EXTS:
        text = root
    text = _COPY_MARK_RE.sub("", text)
    text = _SEPARATORS_RE.sub("_", text).strip("_")
    previous = None
    while previous != text:
        previous = text
        text = _SUFFIX_RE.sub("", text)
    return text


def spoken_text(raw: str | None) -> str | None:
    """Text as it is spoken: without speaker-label placeholders and markup tags."""
    if raw is None:
        return None
    text = _SPEAKER_PREFIX_RE.sub("", raw)
    text = _RICH_TAG_RE.sub("", text)
    text = _RENPY_TAG_RE.sub("", text)
    text = text.replace("\\n", " ").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or None


def _table_priority(table: str | None) -> int:
    return 1 if table and _DIALOGUE_TABLE_RE.search(table) else 0


@dataclass
class TextIndex:
    by_key: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)  # key -> lang -> row
    key_priority: dict[str, int] = field(default_factory=dict)
    by_norm: dict[str, list[str]] = field(default_factory=dict)
    by_audio: dict[str, list[str]] = field(default_factory=dict)
    langs: Counter[str] = field(default_factory=Counter)

    @classmethod
    def build(cls, texts: list[dict[str, Any]]) -> TextIndex:
        index = cls()
        for row in texts:
            key = row.get("key")
            if key is None or row.get("text") in (None, ""):
                continue
            key = str(key)
            lang = row.get("lang") or lang_of(row.get("locale")) or "und"
            prio = _table_priority(row.get("table"))
            per_lang = index.by_key.setdefault(key, {})
            current = per_lang.get(lang)
            if current is None or prio > _table_priority(current.get("table")):
                per_lang[lang] = row
            index.key_priority[key] = max(index.key_priority.get(key, 0), prio)
            index.langs[lang] += 1
            for audio_field in ("audio", "file", "voice"):
                value = row.get(audio_field)
                if isinstance(value, str) and value:
                    bucket = index.by_audio.setdefault(norm_key(value), [])
                    if key not in bucket:
                        bucket.append(key)
        for key in index.by_key:
            index.by_norm.setdefault(norm_key(key), []).append(key)
        return index

    def pick(self, keys: list[str]) -> tuple[str, bool]:
        """Best key among candidates (dialogue tables first); flag tells whether it was ambiguous."""
        if len(keys) == 1:
            return keys[0], False
        ranked = sorted(keys, key=lambda k: (-self.key_priority.get(k, 0), k))
        return ranked[0], True

    def default_lang(self) -> str | None:
        if not self.langs:
            return None
        best = max(self.langs.values())
        tops = sorted(lang for lang, n in self.langs.items() if n == best)
        return "en" if "en" in tops else tops[0]


def _candidates(line: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    entry = line.get("entry") or {}
    exact = [str(entry[k]) for k in ("key", "name") if entry.get(k)]
    normal = [norm_key(str(entry[k])) for k in ("key", "name", "event_path", "path") if entry.get(k)]
    files = [norm_key(str(entry[k])) for k in ("file", "name") if entry.get(k)]
    return exact, normal, files


def find_key(line: dict[str, Any], index: TextIndex) -> tuple[str | None, str | None, bool]:
    """(text key, match pass, ambiguous) for one line."""
    exact, normal, files = _candidates(line)
    for key in exact:
        if key in index.by_key:
            return key, "exact", False
    for nk in normal:
        keys = index.by_norm.get(nk)
        if keys:
            key, ambiguous = index.pick(keys)
            return key, "normalized", ambiguous
    for nk in files:
        keys = index.by_audio.get(nk) or index.by_norm.get(nk)
        if keys:
            key, ambiguous = index.pick(keys)
            return key, "filename", ambiguous
    return None, None, False


def _take_rank(line: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Higher is better: referenced by the engine table, exact key, '_best', higher _vN, earlier order."""
    entry = line.get("entry") or {}
    key = str(entry.get("key") or "")
    version = _VERSION_RE.search(key)
    return (
        int(bool(entry.get("table_key"))),
        int((line.get("text") or {}).get("match") == "exact"),
        int(bool(_BEST_RE.search(key))),
        int(version.group(1)) if version else 0,
        -int(line.get("order") or 0),
    )


def mark_alt_takes(lines: list[dict[str, Any]]) -> int:
    """Several recordings of one text key in one container: keep the best take, flag the others.

    A recording with the very same entry key as the kept one is a `duplicate`; a differently
    named one (`_v1`, `_v2_BEST` ...) is an `alt_take`. Lines the adapter already marked
    `duplicate` are left alone. Returns the number of lines flagged here.
    """
    groups: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for line in lines:
        key = (line.get("text") or {}).get("key")
        flags = line.get("flags") or []
        if key is not None and "alt_take" not in flags and "duplicate" not in flags:
            groups.setdefault((line.get("asset_id"), key), []).append(line)
    flagged = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        primary = max(group, key=_take_rank)
        primary_key = (primary.get("entry") or {}).get("key")
        for line in group:
            if line is primary:
                continue
            same = (line.get("entry") or {}).get("key") == primary_key
            line["flags"] = [*(line.get("flags") or []), "duplicate" if same else "alt_take"]
            flagged += 1
    return flagged


def link_lines(
    lines: list[dict[str, Any]], texts: list[dict[str, Any]], target_lang: str, source_lang: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fill line['text'] for every line; returns (lines, summary)."""
    index = TextIndex.build(texts)
    target = lang_of(target_lang) or target_lang
    fallback_src = lang_of(source_lang) or index.default_lang()
    by_match: Counter[str] = Counter()
    src_langs: Counter[str] = Counter()
    unlinked: list[str] = []
    ambiguous = 0
    no_src = 0
    with_target = 0
    out: list[dict[str, Any]] = []
    for line in lines:
        line = dict(line)
        flags = [f for f in (line.get("flags") or []) if f not in ("no_text", "alt_take")]
        old = line.get("text") or {}
        src_lang = lang_of(line.get("locale")) or fallback_src
        if old.get("source") in PRESERVE_SOURCES:
            line["flags"] = flags
            out.append(line)
            by_match["kept"] += 1
            if old.get("tgt_official"):
                with_target += 1
            continue
        key, how, amb = find_key(line, index)
        if key is None:
            line["text"] = {
                "key": None,
                "src": None,
                "src_lang": src_lang,
                "tgt_official": None,
                "source": None,
                "match": None,
            }
            flags.append("no_text")
            unlinked.append(str((line.get("entry") or {}).get("key") or line.get("id")))
        else:
            rows = index.by_key[key]
            src_row = rows.get(src_lang) if src_lang else None
            tgt_row = rows.get(target)
            src = spoken_text(src_row["text"]) if src_row else None
            tgt = spoken_text(tgt_row["text"]) if tgt_row else None
            any_row = src_row or tgt_row or next(iter(rows.values()))
            text: dict[str, Any] = {
                "key": key,
                "src": src,
                "src_lang": src_lang,
                "tgt_official": tgt,
                "source": any_row.get("source"),
                "match": how,
                "table": any_row.get("table"),
            }
            if src_row and src_row["text"] != src:
                text["src_raw"] = src_row["text"]
            if tgt_row and tgt_row["text"] != tgt:
                text["tgt_raw"] = tgt_row["text"]
            line["text"] = text
            by_match[how or "?"] += 1
            ambiguous += int(amb)
            if src is None:
                no_src += 1
            if tgt:
                with_target += 1
            if src_lang:
                src_langs[src_lang] += 1
        line["flags"] = flags
        out.append(line)
    linked = len(out) - len(unlinked)
    alt_takes = mark_alt_takes(out)
    summary: dict[str, Any] = {
        "lines": len(out),
        "linked": linked,
        "unlinked": len(unlinked),
        "with_official_target": with_target,
        "by_match": dict(by_match),
        "src_lang": src_langs.most_common(1)[0][0] if src_langs else fallback_src,
        "target_lang": target,
        "ambiguous": ambiguous,
        "alt_takes": alt_takes,
        "no_src_text": no_src,
        "unlinked_examples": unlinked[:5],
        "text_langs": len(index.langs),
    }
    return out, summary


def run_link(project: Project, dry_run: bool = False) -> dict[str, Any]:
    """Link inventory/lines.jsonl with inventory/texts.jsonl; sets project source_lang."""
    lines = read_jsonl(project.path(layout.LINES))
    if not lines:
        raise BabelDubError("no lines to link", code=EXIT_USAGE, hint="run `babeldub extract` first")
    texts = read_jsonl(project.path(layout.TEXTS))
    linked, summary = link_lines(lines, texts, project.target_lang, project.data.get("source_lang"))
    if not dry_run:
        write_jsonl(project.path(layout.LINES), linked, schema="babeldub.lines/1")
        if summary.get("src_lang") and summary["linked"]:
            project.data["source_lang"] = summary["src_lang"]
            project.save()
    else:
        summary["dry_run"] = True
    if not texts:
        summary["hint"] = (
            "no text tables were found: lines without text cannot be translated in v0.1 (no transcription "
            "command); check the text references of the recon skill for this engine"
        )
    return summary
