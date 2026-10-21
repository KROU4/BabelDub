"""Language and locale helpers shared by recon and adapters.

Games name languages in many ways: ISO codes (en, en-US, en_US, es_419), Wwise folder names
(English(US), Chinese(PRC)), native names in Unity Localization bundle names (русский, polski)
and three-letter codes (eng, rus). `locale_from_token` maps any of them to a normalized locale
("en-US", "ru", "zh-CN"); `lang_of` reduces a locale to its two-letter language.
"""

from __future__ import annotations

import re

# Two-letter codes accepted as bare folder/file tokens. Deliberately excludes codes that collide
# with common audio folder names: "se" (sound effects), "bg" (background), "vo" (voice-over).
ISO_639_1 = frozenset(
    "af ar az be ca cs cy da de el en eo es et eu fa fi fil fr ga gl he hi hr hu hy id is it ja ka kk "
    "ko lt lv mk ms mt nb nl nn no pl pt ro ru sk sl sq sr sv sw ta th tl tr uk ur uz vi zh".split()
)

# Lowercased names -> normalized locale. Covers English names (Wwise style), native names
# (Unity Localization bundle names) and frequent three-letter codes.
_NAMES: dict[str, str] = {
    # English names / Wwise language folders
    "english": "en",
    "english(us)": "en-US",
    "english(uk)": "en-GB",
    "english(australia)": "en-AU",
    "english(canada)": "en-CA",
    "english(india)": "en-IN",
    "russian": "ru",
    "polish": "pl",
    "german": "de",
    "french": "fr",
    "french(france)": "fr-FR",
    "french(canada)": "fr-CA",
    "spanish": "es",
    "spanish(spain)": "es-ES",
    "spanish(mexico)": "es-MX",
    "spanish(latinamerica)": "es-419",
    "italian": "it",
    "portuguese": "pt",
    "portuguese(brazil)": "pt-BR",
    "portuguese(portugal)": "pt-PT",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "chinese(prc)": "zh-CN",
    "chinese(taiwan)": "zh-TW",
    "chinese(hk)": "zh-HK",
    "chinese(simplified)": "zh-CN",
    "chinese(traditional)": "zh-TW",
    "czech": "cs",
    "turkish": "tr",
    "arabic": "ar",
    "thai": "th",
    "ukrainian": "uk",
    "hungarian": "hu",
    "dutch": "nl",
    "swedish": "sv",
    "finnish": "fi",
    "norwegian": "no",
    "danish": "da",
    "greek": "el",
    "hebrew": "he",
    "vietnamese": "vi",
    "indonesian": "id",
    "romanian": "ro",
    "bulgarian": "bg",
    "georgian": "ka",
    "slovak": "sk",
    "croatian": "hr",
    "serbian": "sr",
    "lithuanian": "lt",
    "latvian": "lv",
    "estonian": "et",
    "kazakh": "kk",
    "belarusian": "be",
    "hindi": "hi",
    "malay": "ms",
    # native names (Unity Localization uses Locale names like "русский")
    "русский": "ru",
    "polski": "pl",
    "deutsch": "de",
    "français": "fr",
    "español": "es",
    "español(latinoamérica)": "es-419",
    "italiano": "it",
    "magyar": "hu",
    "português": "pt",
    "português(brasil)": "pt-BR",
    "português(portugal)": "pt-PT",
    "română": "ro",
    "tiếngviệt": "vi",
    "tiếng việt": "vi",
    "türkçe": "tr",
    "čeština": "cs",
    "українська": "uk",
    "العربية": "ar",
    "ქართული": "ka",
    "中文": "zh",
    "中文（简体）": "zh-CN",
    "中文(简体)": "zh-CN",
    "简体中文": "zh-CN",
    "中文（繁體）": "zh-TW",
    "中文(繁體)": "zh-TW",
    "繁體中文": "zh-TW",
    "日本語": "ja",
    "한국어": "ko",
    "nederlands": "nl",
    "svenska": "sv",
    "suomi": "fi",
    "norsk": "no",
    "dansk": "da",
    "ελληνικά": "el",
    "עברית": "he",
    "български": "bg",
    "беларуская": "be",
    "қазақ": "kk",
    "slovenčina": "sk",
    "hrvatski": "hr",
    "srpski": "sr",
    "bahasa indonesia": "id",
    "ไทย": "th",
    # three-letter codes
    "eng": "en",
    "rus": "ru",
    "pol": "pl",
    "deu": "de",
    "ger": "de",
    "fra": "fr",
    "fre": "fr",
    "spa": "es",
    "ita": "it",
    "por": "pt",
    "jpn": "ja",
    "jap": "ja",
    "kor": "ko",
    "chs": "zh-CN",
    "cht": "zh-TW",
    "zho": "zh",
    "chi": "zh",
    "ces": "cs",
    "cze": "cs",
    "tur": "tr",
    "ukr": "uk",
    "hun": "hu",
    "ara": "ar",
    "tha": "th",
    "nld": "nl",
    "dut": "nl",
    "swe": "sv",
    "fin": "fi",
    "nor": "no",
    "dan": "da",
    "ell": "el",
    "gre": "el",
    "heb": "he",
    "vie": "vi",
    "ind": "id",
    "ron": "ro",
    "rum": "ro",
    "bul": "bg",
    "kat": "ka",
    "geo": "ka",
}

_CODE_RE = re.compile(r"^([a-z]{2,3})(?:[-_]([a-z]{2}|\d{3}|[a-z]{4}))?$")


def _norm_name(token: str) -> str:
    text = token.strip().lower()
    text = re.sub(r"\s*\(\s*", "(", text)
    text = re.sub(r"\s*\)\s*", ")", text)
    return text


def locale_from_token(token: str | None) -> str | None:
    """Normalize a language token to a locale code, or None if it is not a language."""
    if not token:
        return None
    raw = token.strip()
    name = _norm_name(raw)
    if name in _NAMES:
        return _NAMES[name]
    compact = name.replace(" ", "")
    if compact in _NAMES:
        return _NAMES[compact]
    match = _CODE_RE.match(name)
    if not match:
        return None
    lang, region = match.group(1), match.group(2)
    if len(lang) == 3:
        mapped = _NAMES.get(lang)
        if not mapped:
            return None
        return mapped if not region else f"{lang_of(mapped)}-{region.upper()}"
    if region is None:
        # bare two-letter tokens are ambiguous ("se", "bg", "vo"); accept only known codes
        return lang if lang in ISO_639_1 else None
    if lang not in ISO_639_1 and lang != "bg":
        return None
    if region.isdigit():
        return f"{lang}-{region}"
    if len(region) == 4:
        return f"{lang}-{region.title()}"
    return f"{lang}-{region.upper()}"


def lang_of(locale: str | None) -> str | None:
    """'en-US' -> 'en', 'es_419' -> 'es', 'zh-Hans' -> 'zh'; None stays None."""
    if not locale:
        return None
    head = re.split(r"[-_]", locale.strip(), maxsplit=1)[0].lower()
    if len(head) == 2:
        return head
    mapped = _NAMES.get(head)
    return lang_of(mapped) if mapped else head or None


_REGION_TOKEN_RE = re.compile(r"(?:^|[_\-. ])([a-z]{2}[-_][A-Z]{2})(?=$|[_\-. ])")


def locale_in_path(relpath: str, include_file: bool = True) -> str | None:
    """Locale named by a path: deepest folder that is a language, else a token of the file name.

    File names only count with an explicit region ("Dialogue_en-US.bank") or a full language
    name ("vo_english.pck"): bare two-letter tokens in file names are too ambiguous.
    """
    parts = [p for p in re.split(r"[\\/]", relpath) if p]
    if not parts:
        return None
    folders, name = (parts[:-1], parts[-1]) if include_file else (parts, "")
    for part in reversed(folders):
        loc = locale_from_token(part)
        if loc:
            return loc
    if not name:
        return None
    stem = name.rsplit(".", 1)[0] if "." in name else name
    match = _REGION_TOKEN_RE.search(stem)
    if match:
        return locale_from_token(match.group(1))
    for token in reversed(re.split(r"[_\-. ]+", stem)):
        if len(token) > 3 and _norm_name(token) in _NAMES:
            return _NAMES[_norm_name(token)]
    return None
