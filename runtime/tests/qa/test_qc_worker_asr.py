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

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> tuple[Any, Any]:
        self.kwargs = kwargs
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
