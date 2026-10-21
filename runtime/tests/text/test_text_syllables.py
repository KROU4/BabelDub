import pytest

from babeldub.text import normalize
from babeldub.text.syllables import budget_syllables, count_syllables, fit_status, speech_rate


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
