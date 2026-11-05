"""qc_worker ASR options (faster-whisper is faked: the worker module itself needs no torch to import)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from babeldub.workers import qc_worker


class FakeWhisper:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.calls: list[bool] = []
        self.vad_hears_nothing = False

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> tuple[Any, Any]:
        self.kwargs = kwargs
        self.calls.append(kwargs["vad_filter"])
        if self.vad_hears_nothing and kwargs["vad_filter"]:
            segments: Any = iter([])
        else:
            segments = iter([SimpleNamespace(text=" Привет, "), SimpleNamespace(text="как дела? ")])
        return segments, SimpleNamespace(language="ru", language_probability=0.98, duration=1.0)


class FakeTensor:
    def __init__(self, data: np.ndarray) -> None:
        self.data = data

    def numpy(self) -> np.ndarray:
        return self.data


@pytest.fixture
def whisper(monkeypatch: pytest.MonkeyPatch) -> FakeWhisper:
    model = FakeWhisper()
    monkeypatch.setitem(qc_worker._STATE, "asr", model)
    monkeypatch.setattr(qc_worker, "_read_16k", lambda path: [FakeTensor(np.ones(16000, dtype=np.float32))])
    return model


def test_asr_filters_silence_and_does_not_condition_on_previous_text(whisper: FakeWhisper) -> None:
    out = qc_worker.op_asr({"id": "a", "wav": "a.wav", "lang": "ru-RU"})
    assert out["text"] == "Привет, как дела?" and out["lang"] == "ru"
    kw = whisper.kwargs
    assert kw["language"] == "ru" and kw["condition_on_previous_text"] is False
    assert kw["vad_filter"] is True and kw["vad_parameters"]["min_silence_duration_ms"] == 500


def test_asr_options_can_turn_vad_off(whisper: FakeWhisper) -> None:
    qc_worker.op_asr({"id": "a", "wav": "a.wav", "lang": "ru", "options": {"vad_filter": False}})
    assert whisper.kwargs["vad_filter"] is False and whisper.kwargs["vad_parameters"] is None
    assert whisper.calls == [False]


def test_asr_retries_without_vad_when_it_hears_nothing(whisper: FakeWhisper) -> None:
    whisper.vad_hears_nothing = True  # e.g. a short quiet bark the VAD drops entirely
    out = qc_worker.op_asr({"id": "a", "wav": "a.wav", "lang": "ru"})
    assert out["text"] == "Привет, как дела?" and whisper.calls == [True, False]


@pytest.mark.parametrize(
    ("heard", "reference", "kept"),
    [
        ("Закрыто. Продолжение следует...", "Закрыто.", "Закрыто."),
        ("Пойдём. Субтитры сделал DimaTorzok", "Пойдём.", "Пойдём."),
        ("Ладно. Редактор субтитров А.Синецкая Корректор А.Егорова", "Ладно.", "Ладно."),
        ("Хорошо! Спасибо за просмотр! Продолжение следует...", "Хорошо!", "Хорошо!"),
        ("Okay. Thanks for watching!", "Okay.", "Okay."),
        ("Dobrze. Napisy stworzone przez społeczność Amara.org", "Dobrze.", "Dobrze."),
        ("Продолжение следует...", "", ""),  # a silent take: nothing was said
        # the dub text itself says it: kept
        ("Продолжение следует.", "Продолжение следует!", "Продолжение следует."),
        ("To be continued, he said.", "To be continued, he said.", "To be continued, he said."),
        # only at the end
        ("Продолжение следует, сказал он.", "Это всё, сказал он.", "Продолжение следует, сказал он."),
        ("Всё в порядке.", "Всё в порядке.", "Всё в порядке."),
    ],
)
def test_hallucination_tails_are_stripped_unless_the_reference_says_them(heard: str, reference: str, kept: str) -> None:
    assert qc_worker.strip_hallucinations(heard, reference) == kept


def test_asr_returns_the_raw_text_and_strips_tails_against_the_reference(whisper: FakeWhisper,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    def transcribe(audio: np.ndarray, **kwargs: Any) -> tuple[Any, Any]:
        segs = iter([SimpleNamespace(text=" Закрыто."), SimpleNamespace(text=" Продолжение следует...")])
        return segs, SimpleNamespace(language="ru", language_probability=0.9, duration=1.0)

    monkeypatch.setattr(whisper, "transcribe", transcribe)
    out = qc_worker.op_asr({"id": "a", "wav": "a.wav", "lang": "ru", "text": "Закрыто."})
    assert out["text"] == "Закрыто." and out["text_raw"] == "Закрыто. Продолжение следует..."
    out = qc_worker.op_score({"id": "a", "wav": "a.wav", "lang": "ru", "text": "Закрыто.", "want": ["cer"]})
    assert out["cer"] == 0.0 and out["asr_text_raw"].endswith("следует...")
