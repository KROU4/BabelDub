"""Name/path heuristics shared by discover and list_lines: roles, locales, line kinds, speakers, scenes.

They only look at names (bank file name, subsound name, event path); nothing here decodes audio.
Everything is a hint for the agents: `babeldub scan`/`extract` reports let them correct it.
"""

from __future__ import annotations

import re

# --- banks ----------------------------------------------------------------------------------
_VOICE_SUBSTR = ("dialog", "voice", "speech", "narrat")
_VOICE_TOKENS = {"vo", "vox", "dub", "dubbing"}
_LOCALE_RE = re.compile(r"[_\-.]([a-z]{2})(?:[_\-]([A-Z]{2}|[A-Z][a-z]{3}|\d{3}))?$")
_LANGS = set(
    "af ar az be bg bn bs ca cs cy da de el en es et eu fa fi fr ga gl he hi hr hu hy id is it ja ka kk "
    "ko lt lv mk mn ms nb nl nn no pl pt ro ru sk sl sq sr sv sw ta te th tl tr uk ur uz vi zh".split()
)


def tokens(text: str) -> list[str]:
    """Split on separators and camelCase: 'Dialogue_enUS-VO' -> ['dialogue', 'en', 'us', 'vo']."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def bank_role(stem: str, has_audio: bool) -> str:
    if not has_audio:
        return "other"
    low = stem.lower()
    if any(s in low for s in _VOICE_SUBSTR) or _VOICE_TOKENS & set(tokens(stem)):
        return "voice"
    return "audio"


def locale_from_name(stem: str) -> str | None:
    """'Dialogue_en-US' -> 'en-US', 'VO_fr' -> 'fr', 'Master' -> None."""
    m = _LOCALE_RE.search(stem)
    if not m:
        return None
    lang, region = m.group(1), m.group(2)
    if lang not in _LANGS:
        return None
    return f"{lang}-{region}" if region else lang


def locale_from_siblings(stem: str, siblings: list[str]) -> str | None:
    """Localized banks share a GUID; the suffix after their common prefix is the locale ('Dialogue_FRE')."""
    if len(siblings) < 2:
        return None
    prefix = siblings[0]
    for s in siblings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
    cut = max(prefix.rfind("_"), prefix.rfind("-"))
    if cut < 0:
        return None
    suffix = stem[cut + 1 :]
    return suffix if re.fullmatch(r"[A-Za-z]{2,4}([-_][A-Za-z0-9]{2,4})?", suffix) else None


# --- lines ----------------------------------------------------------------------------------
_CUTSCENE_SEG = re.compile(r"(?i)^(cut[ _-]?scenes?|cinematics?|movies?|videos?|cs\d*)$")
_CUTSCENE_KEY = re.compile(r"(?i)(^|[_\-.])(cs\d+|cutscenes?|cinematics?)([_\-.]|$)|^(video|movie|cine)[_\-]")
_DIALOGUE_SEG = re.compile(
    r"(?i)^(dialog(ue)?s?|vo|voice(s|over|overs)?|speech|narrat\w*|conversations?|lines?|subtitles?)$"
)
_DIALOGUE_KEY = re.compile(r"(?i)^(dlg|dialog(ue)?|vo|voice|line|conv|narr\w*)[_\-.]")
_BARK = re.compile(r"(?i)(^|[_\-/.])barks?([_\-/.]|$)")
_MUSIC = re.compile(r"(?i)(^|[_\-/. ])(music|mus|msc|soundtrack|ost|score|songs?|stingers?)([_\-/. ]|$)")
_AMBIENCE = re.compile(r"(?i)(^|[_\-/. ])(amb|ambien(ce|t)|atmos\w*|environment|room ?tones?)([_\-/. ]|$)")
_UI = re.compile(r"(?i)(^|[_\-/. ])(ui|menu|hud|interface|frontend)([_\-/. ]|$)")
_SFX = re.compile(
    r"(?i)(^|[_\-/. ])(sfx|fx|foley|sound ?effects?|footsteps?|weapons?|impacts?|explosions?|whoosh\w*)([_\-/. ]|$)"
)
_LIBRARY_NAME = re.compile(r"^\d{3,}[-_.]")  # stock library files: '119300-Sticks_drop_on_dirt_2'
_ALT_TAKE = re.compile(r"(?i)([_\-.](v\d+|alt\d*|take\d+|best|old|new|backup|temp|tmp)|\(\d+\))$")
_NONVERBAL = {
    "hmm", "hm", "uh", "um", "ah", "oh", "grunt", "grunts", "breath", "breathing", "sigh", "laugh", "laughs",
    "cough", "burp", "scream", "screams", "shout", "yell", "gasp", "pain", "effort", "efforts", "death",
    "groan", "groans", "moan", "sniff", "snore", "whistle", "hum", "hmmm", "mmm", "ahh", "ohh", "uhh", "huh",
}  # fmt: skip
_GENERIC_FOLDERS = {
    "dialogue", "dialogues", "dialog", "dialogs", "vo", "voice", "voices", "voiceover", "speech", "lines",
    "barks", "bark", "cutscenes", "cutscene", "cinematics", "cinematic", "sfx", "fx", "music", "ambience",
    "ambient", "ui", "misc", "common", "generic", "characters", "character", "bonus", "test", "temp",
    "events", "event", "sounds", "sound", "audio", "master", "general", "other", "shared", "global",
    "narration", "narrator", "conversations", "conversation", "subtitles", "localized", "loc",
}  # fmt: skip
_SCENE_FOLDER = re.compile(
    r"(?i)^(act|chapter|ch|scene|level|lvl|mission|quest|episode|ep|part|day|stage|world)[ _-]?\d+"
)
_SCENE_KEY = re.compile(r"(?i)(?:^|[_\-.])([a-z]{1,3}\d{1,3}_[a-z]{1,3}\d{1,3})(?=[_\-.]|$)")
_SPEAKER_NAME = re.compile(r"^[A-Za-z][A-Za-z'. -]{0,39}$")
VOICED_KINDS = ("dialogue", "cutscene", "bark")


def _segments(event_path: str | None) -> list[str]:
    if not event_path:
        return []
    body = event_path.split(":/", 1)[-1]
    return [s for s in body.split("/") if s]


_NON_SPEECH = ((_SFX, "sfx"), (_MUSIC, "music"), (_AMBIENCE, "ambience"), (_UI, "ui"))


def classify(name: str, event_path: str | None, bank_stem: str, role: str) -> tuple[str, bool]:
    """(kind, fixed_length) for one subsound.

    Order: non-speech root folder of the event (event:/Sound Effects/Cutscenes/... is sfx) ->
    non-speech markers in a name without a dialogue prefix (stock-library numbers, sfx_/mus_/amb_)
    -> cutscene -> bark -> dialogue -> folder words -> unknown (voice banks) or bank-name category.
    """
    folders = _segments(event_path)[:-1]
    if folders:
        for rx, kind in _NON_SPEECH:
            if rx.search(folders[0]):
                return kind, False
    if not _DIALOGUE_KEY.match(name):
        if _LIBRARY_NAME.match(name):
            return "sfx", False
        for rx, kind in _NON_SPEECH:
            if rx.search(name):
                return kind, False
    if any(_CUTSCENE_SEG.match(f) for f in folders) or _CUTSCENE_KEY.search(name):
        return "cutscene", True
    if any(_BARK.search(f) for f in folders) or _BARK.search(name):
        return "bark", False
    if any(_DIALOGUE_SEG.match(f) for f in folders) or _DIALOGUE_KEY.match(name):
        return "dialogue", False
    context = "/".join(folders)
    for rx, kind in _NON_SPEECH:
        if context and rx.search(context):
            return kind, False
    if role == "voice":
        return "unknown", False
    for rx, kind in ((_MUSIC, "music"), (_AMBIENCE, "ambience"), (_UI, "ui"), (_SFX, "sfx")):
        if rx.search(bank_stem):
            return kind, False
    return "sfx", False


def speaker_hint(event_path: str | None) -> str | None:
    """Folder right before the key: event:/Dialogue/Act1/Zbyszek/key -> 'Zbyszek'."""
    segs = _segments(event_path)
    if len(segs) < 2:
        return None
    folder = segs[-2]
    if not _SPEAKER_NAME.match(folder) or folder.lower() in _GENERIC_FOLDERS or _SCENE_FOLDER.match(folder):
        return None
    if any(_CUTSCENE_SEG.match(s) for s in segs[:-2]) and len(segs) == 3:
        return None  # event:/Cutscenes/<scene>/<key>: the folder is a scene, not a speaker
    return folder


def scene_hint(name: str, event_path: str | None, kind: str) -> str | None:
    if kind not in VOICED_KINDS:
        return None
    m = _SCENE_KEY.search(name)
    if m:
        return m.group(1)
    segs = _segments(event_path)
    folders = segs[:-1]
    if kind == "cutscene" and len(folders) >= 2 and _CUTSCENE_SEG.match(folders[0]):
        return folders[1]
    for f in folders:
        if _SCENE_FOLDER.match(f):
            return f
    return None


def line_flags(name: str, kind: str) -> list[str]:
    flags: list[str] = []
    if _ALT_TAKE.search(name):
        flags.append("alt_take")
    if kind in VOICED_KINDS and _NONVERBAL & set(tokens(name)):
        flags.append("nonverbal")
    if kind == "cutscene" and re.match(r"(?i)^(video|movie)[_\-]", name):
        flags.append("has_bed")  # video soundtracks are full mixes (speech + music + sfx)
    return flags
