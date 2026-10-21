"""Syllable estimates for fitting a translation into the duration of the original line.

`count_syllables` is a fast heuristic, not a phonetic analysis: vowel letters for languages with
transparent spelling (ru, uk, pl, cs, ...), vowel groups for other Latin-script languages, the classic
silent-e heuristic for English, characters for CJK. `budget_syllables` converts a duration into the number
of syllables an average dubbing actor says in that time.
"""

from __future__ import annotations

import math
import re
import unicodedata

from babeldub.text.normalize import numbers_to_words, strip_markup

# Every vowel letter is one syllable (no diphthongs in spelling, or diphthongs are rare).
_VOWEL_LETTERS: dict[str, str] = {
    "ru": "аеёиоуыэюя",
    "be": "аеёіоуыэюя",
    "uk": "аеєиіїоуюя",
    "bg": "аеиоуъюя",
    "sr": "аеиоуaeiou",
    "mk": "аеиоу",
    "pl": "aąeęioóuy",
    "cs": "aáeéěiíoóuúůyý",
    "sk": "aáäeéiíoóôuúyý",
    "sl": "aeiou",
    "hr": "aeiou",
    "tr": "aeıioöuü",
    "fi": "aeiouyäö",
    "hu": "aáeéiíoóöőuúüű",
    "ro": "aăâeiîou",
}

# Adjacent vowel letters usually form one syllable (diphthongs), so count vowel groups.
_VOWEL_GROUPS: dict[str, str] = {
    "de": "aeiouyäöü",
    "nl": "aeiouy",
    "sv": "aeiouyåäö",
    "no": "aeiouyæøå",
    "da": "aeiouyæøå",
    "es": "aeiouáéíóúü",
    "it": "aeiouàèéìíòóù",
    "pt": "aeiouáâãàéêíóôõú",
    "fr": "aeiouyàâæéèêëîïôœùûü",
    "ca": "aeiouàèéíïòóúü",
    "id": "aeiou",
    "ms": "aeiou",
    "vi": "aeiouyăâêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ",
}

# Typical syllables per second in dubbed/acted speech (a bit slower than read speech).
SYLLABLE_RATE: dict[str, float] = {
    "ru": 5.5,
    "uk": 5.5,
    "be": 5.5,
    "bg": 5.6,
    "pl": 5.8,
    "cs": 5.8,
    "sk": 5.8,
    "en": 5.8,
    "de": 5.6,
    "nl": 5.8,
    "fr": 6.8,
    "es": 7.2,
    "it": 6.8,
    "pt": 6.6,
    "tr": 6.6,
    "fi": 6.4,
    "hu": 6.2,
    "ro": 6.4,
    "sv": 5.8,
    "zh": 5.0,
    "ja": 7.4,
    "ko": 6.4,
    "vi": 5.6,
}
DEFAULT_RATE = 6.0

_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_DIGITS_RE = re.compile(r"\d+")
_EN_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+")
_EN_SILENT_END_RE = re.compile(r"(?:[^laeiouy]es|[^laeiouy]ed|[^laeiouy]e)$")
_SMALL_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮー")


def _primary(lang: str | None) -> str:
    return (lang or "").replace("_", "-").split("-")[0].lower()


def _english_word(word: str) -> int:
    w = word.lower().replace("’", "'").split("'")[0]
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = _EN_SILENT_END_RE.sub("", w)
    w = w[1:] if w.startswith("y") else w
    return max(1, len(_EN_VOWEL_GROUP_RE.findall(w)))


def _cjk_count(text: str) -> int:
    count = 0
    for ch in text:
        name = unicodedata.name(ch, "")
        if name.startswith("CJK UNIFIED IDEOGRAPH") or name.startswith("HANGUL SYLLABLE"):
            count += 1
        elif (name.startswith("HIRAGANA") or name.startswith("KATAKANA")) and ch not in _SMALL_KANA:
            count += 1
    return count


def _count_word(word: str, code: str) -> int:
    lower = word.lower()
    if code in _VOWEL_LETTERS:
        letters = _VOWEL_LETTERS[code]
        n = sum(1 for ch in lower if ch in letters)
        # a Latin word inside Cyrillic text ("OK", brand names): fall back to the English heuristic;
        # in Latin-script languages a vowel-less word ("w", "z") really has no syllable
        cyrillic_only = not any(ch.isascii() for ch in letters)
        return _english_word(lower) if n == 0 and cyrillic_only and lower.isascii() else n
    if code in _VOWEL_GROUPS:
        groups = re.findall("[" + re.escape(_VOWEL_GROUPS[code]) + "]+", lower)
        n = len(groups)
        if code == "fr" and n > 1 and re.search(r"[^aeiouy]e(s|nt)?$", lower):
            n -= 1  # mute final e
        return n
    if code == "en" or lower.isascii():
        return _english_word(lower)
    # unknown script/language: vowel groups of any Latin or Cyrillic vowel
    return len(re.findall(r"[aeiouyаеёиоуыэюяіїєáéíóúàèìòùäöü]+", lower))


def count_syllables(text: str, lang: str) -> int:
    """Estimated number of spoken syllables in a (possibly marked-up) line."""
    if not text:
        return 0
    code = _primary(lang)
    plain = strip_markup(text)
    if code in ("zh", "ja", "ko"):
        cjk = _cjk_count(plain)
        rest = _WORD_RE.findall(re.sub(r"[぀-ヿ㐀-鿿가-힯]", " ", plain))
        digits = _DIGITS_RE.findall(plain)
        return cjk + sum(_english_word(w) for w in rest) + sum(len(d) for d in digits)
    plain = numbers_to_words(plain, code)
    total = sum(_count_word(word, code) for word in _WORD_RE.findall(plain))
    # digits that num2words could not spell out: about 1.5 syllables per digit
    total += sum(max(1, round(len(d) * 1.5)) for d in _DIGITS_RE.findall(plain))
    return total


def speech_rate(lang: str) -> float:
    return SYLLABLE_RATE.get(_primary(lang), DEFAULT_RATE)


def budget_syllables(dur_s: float | None, lang: str, rate: float | None = None) -> int | None:
    """Syllables that fit into `dur_s` seconds of speech in `lang` (None if the duration is unknown)."""
    if dur_s is None:
        return None
    try:
        seconds = float(dur_s)
    except (TypeError, ValueError):
        return None
    if seconds <= 0 or math.isnan(seconds):
        return 0
    return max(1, int(round(seconds * (rate or speech_rate(lang)))))


def fit_status(est: int, budget: int | None, over: float = 1.15, under: float = 0.5) -> str | None:
    """ok | over (est > budget*over) | under (est < budget*under); None when the budget is unknown."""
    if budget is None or budget <= 0:
        return None
    if est > budget * over:
        return "over"
    if est < budget * under:
        return "under"
    return "ok"
