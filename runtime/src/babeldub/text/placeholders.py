"""Placeholders and markup that a translation must keep intact.

Recognised tokens (returned in order of appearance, in a normalised form):
  {0} {name} {ColorStart}        brace placeholders (also Ren'Py-style {b}{/b}{w=0.5})
  {count:plural}                 Unity Smart Strings / .NET formats: `{Var:formatter:...}` is reduced to
                                 `{Var:formatter}` because the forms inside are language-specific
  %s %d %.2f %1$s                printf-style specifiers (`%%` is a literal percent sign)
  <b> </b> <color=#fff> <br>     angle-bracket markup tags (TextMeshPro, HTML-like)
  [29] [b] [/color] [player]     square tags without spaces (BBCode, Ren'Py variables)
  \\n (two chars) and real newlines

Line breaks (`\\n`, newline, <br>) are "soft": a translation may re-wrap lines, so a count mismatch is
reported as a warning, not an error. So are bracketed words that look like stage directions
([snoring], [laughs]): translators may translate or drop them. BBCode tags ([b], [color=red]), numbers
([29]) and identifiers ([player_name]) stay strict.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

_PRINTF_RE = re.compile(r"%(?:\d+\$)?[-+#0]*\d*(?:\.\d+)?(?:hh|h|ll|l|L|z|j|t)?[sdiufFeEgGxXoc@]")
_ANGLE_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9_:-]*(?:[\s=][^<>\n]{0,200})?/?>")
_SQUARE_RE = re.compile(r"\[/?[A-Za-z0-9_.#=:+-]{1,40}\]")
_LITERAL_NL_RE = re.compile(r"\\n")
_FORMATTER_RE = re.compile(r"[A-Za-z0-9_.]+")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)

SOFT_TOKENS = frozenset({"\n", "\\n", "<br>"})
_BBCODE = frozenset({"b", "i", "u", "s", "color", "size", "url", "img", "center", "font", "quote", "code", "wave",
                     "shake", "rainbow", "outline", "bgcolor", "fgcolor", "p", "br", "lb", "rb"})
_DIRECTION_RE = re.compile(r"\[([A-Za-z]{3,})\]")


def is_soft(token: str) -> bool:
    """Line breaks and stage directions ([snoring]) may legitimately differ in a translation."""
    if token in SOFT_TOKENS:
        return True
    match = _DIRECTION_RE.fullmatch(token)
    return bool(match) and match.group(1).lower() not in _BBCODE


def _split_top_level(inner: str) -> list[str]:
    """Split on ':' that are not nested inside braces or parentheses."""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(inner):
        if ch in "{(":
            depth += 1
        elif ch in "})":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            parts.append(inner[start:i])
            start = i + 1
    parts.append(inner[start:])
    return parts


def _normalize_brace(inner: str) -> str:
    parts = _split_top_level(inner)
    name = parts[0].strip()
    if len(parts) == 1:
        return "{" + name + "}"
    match = _FORMATTER_RE.match(parts[1].strip())
    formatter = match.group(0) if match else ""
    return "{" + name + (":" + formatter if formatter else "") + "}"


def _brace_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, normalised token) for balanced {...} groups; `{{`/`}}` are escapes."""
    spans: list[tuple[int, int, str]] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            if i + 1 < n and text[i + 1] == "{":
                i += 2
                continue
            depth = 0
            j = i
            while j < n:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                elif text[j] == "\n":
                    j = n
                    break
                j += 1
            if j < n and j - i <= 400:
                inner = text[i + 1 : j]
                if inner.strip():
                    spans.append((i, j + 1, _normalize_brace(inner)))
                i = j + 1
                continue
        i += 1
    return spans


def placeholder_spans(text: str) -> list[tuple[int, int, str]]:
    """All placeholder spans (start, end, normalised token), sorted by position, non-overlapping."""
    if not text:
        return []
    spans = _brace_spans(text)
    masked = list(text)
    for start, end, _ in spans:
        masked[start:end] = [" "] * (end - start)
    for regex in (_ANGLE_RE, _SQUARE_RE, _PRINTF_RE, _LITERAL_NL_RE):
        # mask every match so later patterns never re-match inside an earlier token
        for m in list(regex.finditer("".join(masked))):
            token = m.group(0)
            if regex is _ANGLE_RE and _BR_RE.fullmatch(token):
                token = "<br>"
            spans.append((m.start(), m.end(), token))
            masked[m.start() : m.end()] = [" "] * (m.end() - m.start())
    for idx, ch in enumerate(text):
        if ch == "\n":
            spans.append((idx, idx + 1, "\n"))
    spans.sort(key=lambda s: s[0])
    return spans


def extract_placeholders(text: str) -> list[str]:
    """Placeholders/tags in order of appearance (normalised, see module docstring)."""
    return [token for _, _, token in placeholder_spans(text)]


def check_placeholders(src: str, tgt: str) -> list[dict[str, Any]]:
    """Compare placeholder multisets of a source string and its translation.

    Returns problems: {"severity": "error"|"warn", "kind": "missing"|"extra", "token", "expected", "found"}.
    Order is not checked (translations may legitimately reorder {0} and {1}).
    """
    want = Counter(extract_placeholders(src or ""))
    got = Counter(extract_placeholders(tgt or ""))
    problems: list[dict[str, Any]] = []
    for token in sorted(set(want) | set(got)):
        expected, found = want.get(token, 0), got.get(token, 0)
        if expected == found:
            continue
        problems.append(
            {
                "severity": "warn" if is_soft(token) else "error",
                "kind": "missing" if found < expected else "extra",
                "token": token,
                "expected": expected,
                "found": found,
            }
        )
    return problems


def has_errors(problems: list[dict[str, Any]]) -> bool:
    return any(p.get("severity") == "error" for p in problems)


def describe(problems: list[dict[str, Any]]) -> str:
    """Short human/agent readable summary, e.g. "missing {0} (1/0); extra <b> (0/1)"."""
    return "; ".join(f"{p['kind']} {p['token']!r} ({p['expected']}/{p['found']})" for p in problems)
