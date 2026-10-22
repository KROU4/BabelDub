"""Prepare a translated line for TTS.

- markup and placeholders are removed so they are never spoken ({..}, <tag>, [..], %s, \\n);
- numbers are spelled out with `num2words` when it is installed, otherwise digits stay as they are;
- Russian `ё`: kept as written by default; `yo="strip"` replaces it with `е`, `yo="restore"` puts it back
  into a small list of unambiguous words (ещё, её, чёрт, пошёл ...). A full yoficator needs a dictionary
  and belongs to the TTS worker (e.g. RUAccent), not to the CLI.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from babeldub.text.placeholders import SOFT_TOKENS, placeholder_spans

_BRACKET_RE = re.compile(r"\[[^\[\]\n]{0,80}\]")
_WS_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.!?;:…])")
_LEADING_PUNCT_RE = re.compile(r"^[\s:;,]+")
_NUM_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![\w])")
_EN_THOUSANDS_RE = re.compile(r"(?<![\w.,])\d{1,3}(?:,\d{3})+(?![\w,])")

YO_MODES = ("keep", "strip", "restore")

# е-spelling -> ё-spelling for words where the е-reading does not exist in modern Russian.
_YO_WORDS = [
    "её", "ещё", "моё", "твоё", "своё", "всё-таки",
    "чёрт", "чёрта", "чёрту", "чёртом", "чёрный", "чёрная", "чёрное", "чёрные", "чёрного", "чёрной",
    "чёрным", "чёрных",
    "шёл", "пошёл", "нашёл", "пришёл", "ушёл", "зашёл", "подошёл", "прошёл", "вошёл", "перешёл",
    "отошёл", "сошёл", "нашёлся",
    "идём", "пойдём", "придём", "найдём",
    "идёт", "пойдёт", "придёт", "найдёт", "ведёт", "несёт", "живёт", "даёт",
    "тёмный", "тёмная", "тёмное", "тёмные", "тёмно", "тёмного", "тёмной", "тёмным",
    "лёгкий", "лёгкая", "лёгкое", "лёгкие", "лёгкого", "лёгкой",
    "жёлтый", "жёлтая", "жёлтое", "жёлтые", "зелёный", "зелёная", "зелёное", "зелёные",
    "мёртвый", "мёртвая", "мёртвое", "мёртвые",
    "тётя", "тёти", "тётю", "тётей", "ёлка", "ёлки", "ёж", "ёжик",
    "ребёнок", "ребёнка", "ребёнку", "ребёнком", "ребёнке",
    "приём", "объём", "отчёт", "счёт", "полёт", "самолёт", "вертолёт",
    "четвёртый", "четвёртая", "четвёртое", "четвёрка",
]
_YO_MAP = {w.replace("ё", "е"): w for w in _YO_WORDS}
_YO_RE = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, _YO_MAP), key=len, reverse=True)) + r")(?![\w-])", re.IGNORECASE)


@lru_cache(maxsize=1)
def _num2words() -> Callable[..., str] | None:
    try:
        from num2words import num2words
    except ImportError:
        return None
    return num2words


def has_num2words() -> bool:
    return _num2words() is not None


def _primary(lang: str | None) -> str:
    return (lang or "").replace("_", "-").split("-")[0].lower()


def strip_markup(text: str) -> str:
    """Remove placeholders, tags and stage directions; collapse whitespace."""
    if not text:
        return ""
    out: list[str] = []
    pos = 0
    for start, end, token in placeholder_spans(text):
        if start < pos:
            continue
        out.append(text[pos:start])
        # tags glue to words ("<b>Hi</b>"), values and line breaks separate them ("{0} coins")
        out.append("" if token[0] in "<[" and token not in SOFT_TOKENS else " ")
        pos = end
    out.append(text[pos:])
    result = "".join(out).replace("{{", "").replace("}}", "")  # escaped literal braces
    result = _BRACKET_RE.sub(" ", result)
    result = _WS_RE.sub(" ", result)
    result = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", result)
    result = _LEADING_PUNCT_RE.sub("", result)
    return result.strip()


def numbers_to_words(text: str, lang: str) -> str:
    """Spell out integers and decimals with num2words; unchanged if it is missing or lacks the language."""
    convert = _num2words()
    if convert is None or not text:
        return text
    code = _primary(lang)
    if code == "en":
        text = _EN_THOUSANDS_RE.sub(lambda m: m.group(0).replace(",", ""), text)

    def repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        value: Any = float(raw.replace(",", ".")) if ("." in raw or "," in raw) else int(raw)
        try:
            return str(convert(value, lang=code))
        except Exception:  # num2words raises NotImplementedError/KeyError/... for unsupported input
            return raw

    return _NUM_RE.sub(repl, text)


def apply_yo(text: str, lang: str, mode: str = "keep") -> str:
    if mode not in YO_MODES:
        raise ValueError(f"yo mode must be one of {YO_MODES}, got {mode!r}")
    if _primary(lang) != "ru" or mode == "keep" or not text:
        return text
    if mode == "strip":
        return text.replace("ё", "е").replace("Ё", "Е")

    def repl(match: re.Match[str]) -> str:
        word = match.group(0)
        target = _YO_MAP.get(word.lower())
        if target is None:
            return word
        if word.isupper() and len(word) > 1:
            return target.upper()
        if word[0].isupper():
            return target[0].upper() + target[1:]
        return target

    return _YO_RE.sub(repl, text)


STRESS_MARK = "́"  # combining acute accent: Russian stress mark (а́)
STRESS_MODES = ("none", "upper", "acute")  # how an engine reads stress: not at all, CAPITAL vowel, U+0301
_VOWELS = "аеёиоуыэюяaeiouyАЕЁИОУЫЭЮЯAEIOUY"
_PLUS_RE = re.compile(r"\+([" + _VOWELS + r"])")
_ACUTE_RE = re.compile(r"([" + _VOWELS + r"])" + STRESS_MARK)


def strip_stress(text: str) -> str:
    """Remove U+0301 stress marks. Every engine except an explicit stress model (ru-stress) needs plain
    text: VoxCPM2 and Qwen3-TTS Base turn the marks into extra sounds."""
    return text.replace(STRESS_MARK, "")


def plus_to_acute(text: str) -> str:
    """RUAccent notation ("з+амок") to U+0301 after the vowel ("за́мок"); a "+" not before a vowel stays."""
    return _PLUS_RE.sub(lambda m: m.group(1) + STRESS_MARK, text)


def acute_to_plus(text: str) -> str:
    """U+0301 after a vowel to RUAccent notation ("за́мок" -> "з+амок")."""
    return _ACUTE_RE.sub(lambda m: "+" + m.group(1), text)


def render_stress(text: str, mode: str) -> str:
    """U+0301 marks as the engine reads them: kept (acute), a CAPITAL vowel (upper) or removed (none).
    VoxCPM2 reads "+" as the word "plus", so "+" never reaches an engine."""
    if mode not in STRESS_MODES:
        raise ValueError(f"stress mode must be one of {STRESS_MODES}, got {mode!r}")
    text = plus_to_acute(text)
    if mode == "upper":
        text = _ACUTE_RE.sub(lambda m: m.group(1).upper(), text)
    if mode != "acute":
        text = strip_stress(text)
    return text


def _plain(word: str) -> str:
    return strip_stress(word.replace("+", ""))


def apply_pronounce(text: str, pronounce: dict[str, str] | None) -> str:
    """Glossary `pronounce`: the translation of a term is replaced with its pronunciation.

    When the pronunciation only adds stress marks to the same letters ("Збышек" -> "Збы́шек"), inflected
    forms get the marks too ("Збышека" -> "Збы́шека"); a respelling replaces whole words only."""
    if not text or not pronounce:
        return text
    for written, said in sorted(pronounce.items(), key=lambda kv: -len(kv[0])):
        if not written or not said or written == said:
            continue
        said = plus_to_acute(said)
        same_letters = _plain(said).lower() == written.lower()
        ending = r"\w{0,3}" if same_letters and len(written) > 3 else ""
        pattern = re.compile(r"(?<!\w)(" + re.escape(written) + r")(" + ending + r")(?!\w)", re.IGNORECASE)

        def repl(match: re.Match[str], said: str = said, same_letters: bool = same_letters) -> str:
            word = match.group(1)
            if same_letters:  # the marks go onto the word as written (its own case)
                letters = iter(word)
                out = "".join(ch if ch == STRESS_MARK else next(letters, ch) for ch in said)
            else:
                out = said[:1].upper() + said[1:] if word[:1].isupper() else said
            return out + match.group(2)

        text = pattern.sub(repl, text)
    return text


def prepare_for_tts(
    text: str, lang: str, numbers: bool = True, yo: str = "keep", stress: bool | str = False,
    pronounce: dict[str, str] | None = None,
) -> str:
    """Text exactly as it should be sent to a TTS engine.

    stress: True/"acute" keeps U+0301 marks, "upper" turns them into a capital vowel, False/"none" drops
    them. pronounce: {glossary translation: pronunciation} applied before numbers and ё."""
    mode = "acute" if stress is True else "none" if stress is False else str(stress)
    result = apply_pronounce(strip_markup(text), pronounce)
    if numbers:
        result = numbers_to_words(result, lang)
    result = apply_yo(result, lang, yo)
    result = render_stress(result, mode)
    return _WS_RE.sub(" ", result).strip()
