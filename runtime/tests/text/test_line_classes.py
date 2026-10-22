"""Line classes of the originals (babeldub.analysis.classes)."""

from __future__ import annotations

import pytest

from babeldub.analysis.classes import SHORT_WORD_S, classify, interjection_only, tokens


@pytest.mark.parametrize(
    "text",
    ["Hehe...", "Hmm.", "Um... uh", "Ugh!", "Oh!", "Ah...", "Ha!", "Haha!", "Huh?", "Mhm.", "Uh-huh.", "Oops!",
     "Pfft.", "Argh!", "Whoa!", "Ох!", "Хм...", "Ага.", "Эх...", "Ой-ой!", "Ach!", "Oj!", "Uff."],
)
def test_interjections(text: str) -> None:
    assert interjection_only(text)


@pytest.mark.parametrize(
    "text",
    ["He said of it", "Hi there", "Yes, Chief!", "Freeze!", "A co?", "Ну, давай.", "Hmm, let me see", "of",
     "Okay.", "Tak.", "Hey!", ""],
)
def test_words_are_not_interjections(text: str) -> None:
    assert not interjection_only(text)


def test_tokens_drop_markup_and_note_stage_directions() -> None:
    assert tokens("<i>Okay</i>, {0} let's go [laughs]") == (
        ["okay", "let's", "go"], True)
    assert tokens(None) == ([], False)


@pytest.mark.parametrize(
    ("text", "flags", "speech_s", "asr", "conf", "expected"),
    [
        ("Okay, let's see what we have here.", [], 2.4, None, None, "speech"),
        ("Freeze!", [], SHORT_WORD_S - 0.1, None, None, "short_word"),
        ("Freeze!", [], None, None, None, "speech"),  # no VAD: not called short
        ("Hehe...", [], 0.5, None, None, "interjection"),
        ("Hehe...", ["nonverbal"], 0.5, None, None, "nonverbal"),
        ("Haha, okay, you got me.", [], 2.0, None, None, "mixed"),
        ("Okay [sighs] fine.", [], 2.0, None, None, "mixed"),
        ("Fine, fine.", ["nonverbal"], 2.0, None, None, "mixed"),
        ("[laughs]", [], 1.0, None, None, "nonverbal"),
        (None, [], 1.0, "", None, "nonverbal"),
        (None, [], 1.0, "Hello there", 0.2, "nonverbal"),  # unsure ASR of a grunt
        (None, [], 1.0, "Hmm.", 0.9, "interjection"),
        (None, ["nonverbal"], 1.0, "Hmm.", 0.9, "nonverbal"),
        (None, [], 0.8, "Go!", 0.9, "short_word"),
        (None, [], 2.0, "Let's go home now", 0.9, "speech"),
        (None, ["nonverbal"], 2.0, "Let's go home now", 0.9, "mixed"),
        (None, [], 2.0, None, None, "speech"),
        (None, ["nonverbal"], 2.0, None, None, "nonverbal"),
    ],
)
def test_classify(text, flags, speech_s, asr, conf, expected) -> None:
    cls, reason = classify(text, flags, speech_s, asr, conf)
    assert cls == expected and reason
