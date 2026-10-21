"""Text helpers: placeholders/markup checks, syllable budgets, TTS normalisation (CONTRACT.md §8)."""

from babeldub.text.normalize import (
    apply_yo,
    has_num2words,
    numbers_to_words,
    prepare_for_tts,
    strip_markup,
)
from babeldub.text.placeholders import check_placeholders, extract_placeholders, has_errors
from babeldub.text.syllables import budget_syllables, count_syllables, fit_status, speech_rate

__all__ = [
    "apply_yo",
    "budget_syllables",
    "check_placeholders",
    "count_syllables",
    "extract_placeholders",
    "fit_status",
    "has_errors",
    "has_num2words",
    "numbers_to_words",
    "prepare_for_tts",
    "speech_rate",
    "strip_markup",
]
