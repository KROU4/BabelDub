import pytest

from babeldub.text import normalize
from babeldub.text.normalize import apply_yo, numbers_to_words, prepare_for_tts, strip_markup


def test_strip_markup_removes_speaker_prefix_and_tags():
    text = "{ColorStart}{CharacterName}:{ColorEnd} Well, I told you <b>you</b> need to go [sighs] right…"
    assert strip_markup(text) == "Well, I told you you need to go right…"


def test_strip_markup_values_and_breaks_separate_words():
    assert strip_markup("You have {0} coins\\nand %d gems<br>now") == "You have coins and gems now"
    assert strip_markup("Hello , world {{x}}") == "Hello, world x"
    assert strip_markup("") == ""


def test_numbers_unchanged_without_num2words(monkeypatch):
    monkeypatch.setattr(normalize, "_num2words", lambda: None)
    assert not normalize.has_num2words()
    assert numbers_to_words("У меня 25 монет", "ru") == "У меня 25 монет"


def test_numbers_with_converter(monkeypatch):
    calls = []

    def fake(value, lang):
        calls.append((value, lang))
        return f"<{value}:{lang}>"

    monkeypatch.setattr(normalize, "_num2words", lambda: fake)
    assert numbers_to_words("I have 1,000 coins and 2.5 kg", "en-US") == "I have <1000:en> coins and <2.5:en> kg"
    assert numbers_to_words("Цена 3,5 и R2D2", "ru") == "Цена <3.5:ru> и R2D2"
    assert calls == [(1000, "en"), (2.5, "en"), (3.5, "ru")]


def test_numbers_unsupported_language_keeps_digits(monkeypatch):
    def fake(value, lang):
        raise NotImplementedError(lang)

    monkeypatch.setattr(normalize, "_num2words", lambda: fake)
    assert numbers_to_words("7 dwarfs", "xx") == "7 dwarfs"


def test_yo_modes():
    assert apply_yo("Ещё её", "ru", "keep") == "Ещё её"
    assert apply_yo("Ещё её", "ru", "strip") == "Еще ее"
    assert apply_yo("Черт, ее еще нет. ЕЩЕ все-таки пошел", "ru", "restore") == "Чёрт, её ещё нет. ЕЩЁ всё-таки пошёл"
    assert apply_yo("еще", "uk", "restore") == "еще"
    with pytest.raises(ValueError):
        apply_yo("x", "ru", "maybe")


def test_prepare_for_tts(monkeypatch):
    monkeypatch.setattr(normalize, "_num2words", lambda: (lambda value, lang: "двадцать пять"))
    text = "{ColorStart}{CharacterName}:{ColorEnd} Ох, черт… ее еще нет, 25 раз"
    assert prepare_for_tts(text, "ru", yo="restore") == "Ох, чёрт… её ещё нет, двадцать пять раз"
    assert prepare_for_tts(text, "ru", numbers=False) == "Ох, черт… ее еще нет, 25 раз"


def test_stress_marks_are_stripped_unless_kept():
    stressed = "Ну что, мало́й, пое́хали."
    assert normalize.strip_stress(stressed) == "Ну что, малой, поехали."
    assert prepare_for_tts(stressed, "ru") == "Ну что, малой, поехали."
    assert prepare_for_tts(stressed, "ru", stress=True) == stressed


def test_stress_rendering_and_pronounce():
    from babeldub.text.normalize import acute_to_plus, apply_pronounce, plus_to_acute, render_stress

    assert plus_to_acute("з+амок") == "за́мок" and acute_to_plus("за́мок") == "з+амок"
    assert render_stress("замо́к и з+амок", "upper") == "замОк и зАмок"
    assert render_stress("за́мок", "none") == "замок" and render_stress("з+амок", "acute") == "за́мок"
    assert render_stress("2+2", "none") == "2+2"  # not a stress mark
    pron = {"Збышек": "Збы́шек", "Zbyszek": "Збышек"}
    assert apply_pronounce("Збышек и збышека", pron) == "Збы́шек и збы́шека"  # inflected form keeps the mark
    assert apply_pronounce("Zbyszek, Zbyszeka", {"Zbyszek": "Збышек"}) == "Збышек, Zbyszeka"  # whole words only
    assert prepare_for_tts("Збышек!", "ru", stress="upper", pronounce={"Збышек": "Зб+ышек"}) == "ЗбЫшек!"
