"""Line kind guesses from paths and names (dialogue / cutscene / bark / music / sfx ...).

Games rarely tag audio explicitly, but folder and file names are telling: VO/, Voice/, Dialogue/
hold speech, Music/ and BGM/ hold music. Tokens are split on separators and camelCase, so
"DialogueBank" and "vo_act1" both match. The first matching group in priority order wins.
"""

from __future__ import annotations

import re

VOICED_KINDS = ("dialogue", "cutscene", "bark")

_GROUPS: list[tuple[str, frozenset[str]]] = [
    ("cutscene", frozenset({"cutscene", "cutscenes", "cinematic", "cinematics", "cine", "timeline"})),
    (
        "bark",
        frozenset(
            {
                "bark",
                "barks",
                "grunt",
                "grunts",
                "effort",
                "efforts",
                "pain",
                "scream",
                "screams",
                "shout",
                "shouts",
                "breath",
                "breaths",
                "vocalization",
                "vocalizations",
            }
        ),
    ),
    (
        "dialogue",
        frozenset(
            {
                "voice",
                "voices",
                "vo",
                "vox",
                "voiceover",
                "voiceovers",
                "dialog",
                "dialogs",
                "dlg",
                "dialogue",
                "dialogues",
                "speech",
                "speeches",
                "lines",
                "narration",
                "narrator",
                "conversation",
                "conversations",
                "talk",
                "subtitle",
                "subtitles",
            }
        ),
    ),
    ("music", frozenset({"music", "musics", "bgm", "mus", "soundtrack", "ost", "song", "songs", "me"})),
    ("ambience", frozenset({"ambience", "ambiences", "ambient", "amb", "bgs", "atmos", "atmosphere"})),
    (
        "sfx",
        frozenset(
            {"sfx", "se", "fx", "sound_effects", "soundeffects", "foley", "effects", "weapons", "footsteps"}
        ),
    ),
    ("ui", frozenset({"ui", "menu", "menus", "interface", "hud"})),
]

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SPLIT_RE = re.compile(r"[\\/_\-. ()\[\]]+")


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for part in _SPLIT_RE.split(text):
        if not part:
            continue
        out.append(part.lower())
        pieces = [p.lower() for p in _CAMEL_RE.split(part) if p]
        if len(pieces) > 1:
            out.extend(pieces)
    joined = text.lower().replace("\\", "/")
    if "sound effects" in joined or "sound_effects" in joined:
        out.append("sound_effects")
    return out


def guess_kind(*texts: str | None) -> str | None:
    """Kind suggested by any of the given paths/names, or None when nothing is telling."""
    toks: set[str] = set()
    for text in texts:
        if text:
            toks.update(tokens(text))
    for kind, words in _GROUPS:
        if toks & words:
            return kind
    return None


def is_voice_path(*texts: str | None) -> bool:
    return guess_kind(*texts) in VOICED_KINDS
