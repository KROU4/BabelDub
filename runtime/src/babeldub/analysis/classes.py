"""Line classes of the original audio: speech | short_word | interjection | nonverbal | mixed.

Signals: the subtitle text (an English/Russian/Polish interjection lexicon), the `nonverbal`
flag of the FMOD file-name heuristics, the ASR of the original (empty or low confidence) and
the VAD speech length. `nonverbal` and `interjection` lines keep the original audio; a
`short_word` ("Freeze!", "Yes, Chief!") is real words with less than SHORT_WORD_S of speech;
`mixed` is speech with a nonverbal part (a laugh, a groan, a stage direction).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

CLASSES = ("speech", "short_word", "interjection", "nonverbal", "mixed")
KEEP_ORIGINAL_CLASSES = frozenset({"interjection", "nonverbal"})
SHORT_WORD_S = 1.2
LOW_ASR_CONF = 0.45  # mean word probability below which the ASR of the original is not trusted
SUBTITLE_MISMATCH_CER = 0.35  # ASR of the original vs the subtitle: above this the subtitle does not match

# sounds rather than words: laughter, groans, growls; such a token next to words makes a line `mixed`
_SOUNDS = [
    r"a?(?:h[aeiouy]){2,}h?", r"h[aeiou]h", r"ha", r"bwa(?:ha)+", r"mu(?:ha)+", r"u+g+h+", r"a+r+g+h*", r"a+g+h+", r"g+a+h+", r"g+r+",
    r"p+f+t*", r"h+m+p+h+", r"z{3,}", r"a+c+h+o+", r"a+h+e+m+", r"s+n+i+f+",
    r"(?:х[аеиоэы])+х?", r"г+р+", r"п+ф+", r"к+х+м*", r"а+п+ч+х+и+",
]
# discourse interjections and hesitations (alone they still are not translatable speech)
_INTERJECTIONS = _SOUNDS + [
    r"h+m+", r"m+h+m+", r"m{2,}", r"u+m+", r"u+h+", r"h+u+h+", r"e+r+m*", r"e+h+", r"a+h+", r"o+h+", r"a+w+",
    r"o+w+", r"wo+w+", r"o+u+c+h+", r"o{2,}f+", r"w?h?o{2,}p+s+", r"p?h?e+w+", r"w+h+e+w+", r"w+h?o+a+", r"y+a+y+",
    r"y+i+k+e+s+", r"p+s+t+", r"s+h+", r"t+s+k+", r"b+r+", r"y+e+e+h+a+w*", r"u+h+o+h*", r"o+o+h*", r"a{2,}h*",
    r"o{2,}", r"e{2,}h*", r"u{2,}h*",
    # Polish
    r"[aeou]+c+h+", r"[oa]+j+", r"u+f+", r"f+i+u+",
    # Russian / Ukrainian
    r"х+м+", r"г+м+", r"м{2,}", r"э+м*", r"[аоуэы]+х+", r"о+й+", r"а+й+", r"а+г+а+", r"у+г+у+", r"о+г+о+",
    r"ф+у+", r"т+ь+ф+у+", r"у+ф+", r"ф+у+х+", r"б+р+", r"ш{2,}", r"а{2,}", r"о{2,}", r"у{2,}", r"э{2,}",
    r"ы+",
]
_SOUND_RE = re.compile("(?:" + "|".join(_SOUNDS) + ")")
_INTERJECTION_RE = re.compile("(?:" + "|".join(_INTERJECTIONS) + ")")
_DIRECTION_RE = re.compile(r"\[[^\[\]]*\]|\*[^*]+\*|\([^()]*\)")  # [laughs], *sighs*, (groans)
_MARKUP_RE = re.compile(r"\{[^{}]*\}|<[^<>]*>")
_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")


def tokens(text: str | None) -> tuple[list[str], bool]:
    """Lower-case word tokens of a subtitle (markup and stage directions removed) and whether it had a direction."""
    if not text:
        return [], False
    raw = _MARKUP_RE.sub(" ", text)
    direction = bool(_DIRECTION_RE.search(raw))
    raw = _DIRECTION_RE.sub(" ", raw)
    norm = unicodedata.normalize("NFC", raw).lower().replace("ё", "е")
    return [t.replace("’", "'") for t in _WORD_RE.findall(norm)], direction


def is_interjection(token: str) -> bool:
    return bool(_INTERJECTION_RE.fullmatch(token))


def is_sound(token: str) -> bool:
    return bool(_SOUND_RE.fullmatch(token))


def interjection_only(text: str | None) -> bool:
    """The text is nothing but interjections ("Hehe...", "Hmm.", "Um... uh", "Ох!")."""
    toks, _direction = tokens(text)
    return bool(toks) and all(is_interjection(t) for t in toks)


def classify(
    text: str | None,
    flags: Iterable[str] = (),
    speech_s: float | None = None,
    asr_text: str | None = None,
    asr_conf: float | None = None,
) -> tuple[str, str]:
    """(class, reason) of one original line."""
    nonverbal = "nonverbal" in set(flags or ())
    toks, direction = tokens(text)
    real = [t for t in toks if not is_interjection(t)]
    short = speech_s is not None and speech_s < SHORT_WORD_S
    if toks:
        if not real:
            if nonverbal:
                return "nonverbal", "file name says nonverbal; text is interjections only"
            return "interjection", "text is interjections only"
        if nonverbal or direction or any(is_sound(t) for t in toks):
            why = "file name says nonverbal" if nonverbal else "stage direction" if direction else "laugh/groan"
            return "mixed", f"words with a nonverbal part ({why})"
        if short:
            return "short_word", f"words in {speech_s:.2f} s of speech"
        return "speech", "words"
    # no subtitle words: the audio decides
    if direction:
        return "nonverbal", "subtitle is a stage direction only"
    if asr_text is None:
        return ("nonverbal", "file name says nonverbal; no text") if nonverbal else ("speech", "no text and no ASR")
    asr_toks, _ = tokens(asr_text)
    if not asr_toks or (asr_conf is not None and asr_conf < LOW_ASR_CONF):
        return "nonverbal", "no text; ASR of the original is empty or unsure"
    if all(is_interjection(t) for t in asr_toks):
        return ("nonverbal" if nonverbal else "interjection"), "no text; ASR hears interjections only"
    if nonverbal:
        return "mixed", "no text; ASR hears words, file name says nonverbal"
    if short:
        return "short_word", f"no text; ASR hears words in {speech_s:.2f} s"
    return "speech", "no text; ASR hears words"
