import pytest

from babeldub.text import normalize
from babeldub.text.syllables import (
    budget_syllables,
    character_rate,
    count_syllables,
    fit_status,
    measured_rate,
    segment_text,
    speech_rate,
)


@pytest.fixture
def no_num2words(monkeypatch):
    monkeypatch.setattr(normalize, "_num2words", lambda: None)


@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        ("Так, посмотрим.", "ru", 4),
        ("Ох, чёрт…", "ru", 2),
        ("В дом", "ru", 1),  # the preposition has no vowel
        ("Okay, let's see.", "en", 4),
        ("I'm attaching a picture", "en", 7),
        ("No, mówiłam, że trzeba iść w prawo.", "pl", 10),
        ("Привіт, як справи?", "uk", 5),
        ("こんにちは、世界", "ja", 7),
        ("你好世界", "zh", 4),
        ("", "ru", 0),
    ],
)
def test_count_syllables(text, lang, expected, no_num2words):
    assert count_syllables(text, lang) == expected


def test_markup_is_not_counted(no_num2words):
    plain = count_syllables("Ну, я же говорила", "ru")
    marked = count_syllables("{ColorStart}{CharacterName}:{ColorEnd} <b>Ну</b>, я же говорила", "ru")
    assert marked == plain == 7


def test_latin_word_in_cyrillic_text_and_digits_fallback(no_num2words):
    # У(1) ме-ня(2) 25 -> ~3 мо-нет(2) и(1) OK(1)
    assert count_syllables("У меня 25 монет и OK", "ru") == 10


def test_digits_spelled_out_when_num2words_exists(monkeypatch):
    monkeypatch.setattr(normalize, "_num2words", lambda: (lambda value, lang: "двадцать пять"))
    assert count_syllables("25", "ru") == 3


def test_region_suffix_is_ignored(no_num2words):
    assert count_syllables("Так, посмотрим.", "ru-RU") == 4


def test_budget_and_rate():
    assert speech_rate("ru") == 5.5
    assert speech_rate("xx") == 6.0
    assert budget_syllables(2.0, "ru") == 11
    assert budget_syllables(5.856, "ru") == 32
    assert budget_syllables(0.05, "en") == 1
    assert budget_syllables(0, "ru") == 0
    assert budget_syllables(None, "ru") is None
    assert budget_syllables("bad", "ru") is None


def test_fit_status():
    assert fit_status(10, 10) == "ok"
    assert fit_status(11, 10) == "ok"
    assert fit_status(12, 10) == "over"
    assert fit_status(4, 10) == "under"
    assert fit_status(5, None) is None


def test_measured_and_character_rate() -> None:
    assert measured_rate("one two three four", 2.0, "en") == 2.0
    assert measured_rate("", 2.0, "en") is None and measured_rate("word", 0.1, "en") is None
    assert measured_rate("word", None, "en") is None
    assert character_rate([4.0, 5.0], "ru") == 5.5  # too few accepted dubs: the language default
    assert character_rate([4.0, 6.0, 5.0], "ru") == 5.0
    assert character_rate([4.0, 6.0, 5.0, 7.0], "ru") == 5.5  # even count: mean of the middle two
    assert character_rate([20.0, 30.0, 40.0], "ru") == 9.0 and character_rate([1.0, 1.0, 1.0], "ru") == 3.0
    assert budget_syllables(2.0, "ru", rate=4.0) == 8


def test_segment_text_marks_pauses() -> None:
    text = "We fix the pump today, then we go home now."
    assert segment_text(text, [(0.3, 1.1), (1.5, 2.3)], "en") == "We fix the pump today, / then we go home now."
    assert segment_text(text, [(0.0, 1.0)], "en") is None  # no pause
    assert segment_text("Hello", [(0.0, 1.0), (1.5, 2.0)], "en") is None  # one word: nowhere to put it
    # more pauses than word boundaries: every boundary used once, no empty group
    out = segment_text("Yes sir", [(0.0, 0.2), (0.5, 0.7), (1.0, 1.2)], "en")
    assert out == "Yes / sir"
    # a late pause lands late in the text
    assert segment_text("one two three four five six", [(0.0, 2.5), (3.0, 3.5)], "en") ==         "one two three four five / six"
