"""qc_worker analysis and scoring ops with faked models (the worker module imports no torch at load)."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import pytest

from babeldub.qa.metrics import cer as cli_cer
from babeldub.workers import qc_worker

SR = qc_worker.SV_SR


class FakeTensor:
    def __init__(self, data: np.ndarray) -> None:
        self.data = data

    def numpy(self) -> np.ndarray:
        return self.data


def _word(w: str, start: float, end: float, p: float) -> Any:
    return SimpleNamespace(word=w, start=start, end=end, probability=p)


class FakeWhisper:
    def __init__(self, hears: bool = True) -> None:
        self.kwargs: list[dict[str, Any]] = []
        self.hears = hears

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> tuple[Any, Any]:
        self.kwargs.append(kwargs)
        if not self.hears and kwargs["vad_filter"]:
            segs: list[Any] = []
        else:
            segs = [
                SimpleNamespace(text=" Hello there.", start=0.1, end=0.9, avg_logprob=-0.2, no_speech_prob=0.05,
                                words=[_word(" Hello", 0.1, 0.4, 0.9), _word(" there.", 0.45, 0.9, 0.7)]),
                SimpleNamespace(text=" Bye.", start=1.2, end=1.5, avg_logprob=-0.5, no_speech_prob=0.1,
                                words=[_word(" Bye.", 1.2, 1.5, 0.8)]),
            ]
        return iter(segs), SimpleNamespace(language="en", language_probability=0.97, duration=2.0)


@pytest.fixture(autouse=True)
def audio(monkeypatch: pytest.MonkeyPatch) -> np.ndarray:
    data = np.zeros(2 * SR, dtype=np.float32)
    monkeypatch.setattr(qc_worker, "_read_16k", lambda path: [FakeTensor(data)])
    monkeypatch.setitem(qc_worker._STATE, "audio_cache", None)
    for key in ("asr", "sv", "mos", "mos_dir", "emotion", "emotion_dir"):
        monkeypatch.setitem(qc_worker._STATE, key, None)
    return data


def test_asr_words_reports_words_and_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    whisper = FakeWhisper()
    monkeypatch.setitem(qc_worker._STATE, "asr", whisper)
    out = qc_worker.op_asr_words({"id": "a", "wav": "a.wav", "lang": "en-US"})
    assert out["text"] == "Hello there. Bye." and out["lang"] == "en" and out["dur_s"] == 2.0
    assert out["words"][0] == {"w": "Hello", "start": 0.1, "end": 0.4, "prob": 0.9} and len(out["words"]) == 3
    assert out["avg_logprob"] == pytest.approx((0.8 * -0.2 + 0.3 * -0.5) / 1.1, abs=1e-3)
    assert out["no_speech_prob"] == 0.05
    kw = whisper.kwargs[0]
    assert kw["word_timestamps"] is True and kw["language"] == "en" and kw["vad_filter"] is True


def test_asr_words_retries_without_vad(monkeypatch: pytest.MonkeyPatch) -> None:
    whisper = FakeWhisper(hears=False)
    monkeypatch.setitem(qc_worker._STATE, "asr", whisper)
    out = qc_worker.op_asr_words({"id": "a", "wav": "a.wav"})
    assert [k["vad_filter"] for k in whisper.kwargs] == [True, False] and out["text"]
    assert whisper.kwargs[0]["language"] is None  # auto-detect without a language


def test_vad_segments_in_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class VadOptions:
        def __init__(self, **kw: Any) -> None:
            seen.update(kw)

    def get_speech_timestamps(audio: np.ndarray, vad_options: Any, sampling_rate: int) -> list[dict[str, int]]:
        return [{"start": 1600, "end": 8000}, {"start": 16000, "end": 24000}]

    fw = ModuleType("faster_whisper")
    vad = ModuleType("faster_whisper.vad")
    vad.VadOptions, vad.get_speech_timestamps = VadOptions, get_speech_timestamps  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad)
    out = qc_worker.op_vad({"id": "a", "wav": "a.wav"})
    assert out["segments"] == [[0.1, 0.5], [1.0, 1.5]] and out["speech_s"] == 0.9 and out["dur_s"] == 2.0
    assert seen["min_silence_duration_ms"] == 250  # pauses of 250 ms split segments


def test_prosody_semitone_range(monkeypatch: pytest.MonkeyPatch) -> None:
    lib = ModuleType("librosa")
    f0 = np.array([np.nan, 100.0, 110.0, 200.0, 150.0, np.nan, 120.0, 180.0, 130.0, 160.0])
    voiced = ~np.isnan(f0)
    lib.pyin = lambda y, **kw: (f0, voiced, np.ones_like(f0))  # type: ignore[attr-defined]
    lib.feature = SimpleNamespace(rms=lambda y, **kw: np.array([[0.1, 0.1, 0.0001]]))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "librosa", lib)
    out = qc_worker.op_prosody({"id": "a", "wav": "a.wav"})
    lo, hi = np.percentile(f0[voiced], [10, 90])
    assert out["f0_median_hz"] == pytest.approx(140.0) and out["f0_range_st"] == round(12 * np.log2(hi / lo), 2)
    assert out["rms_db"] == pytest.approx(-20.0) and out["voiced_ratio"] == 0.8


def test_score_combines_metrics_and_reports_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(qc_worker._STATE, "asr", FakeWhisper())
    out = qc_worker.op_score({"id": "a", "wav": "a.wav", "text": "Hello there, bye!", "lang": "en",
                              "want": ["cer", "sim", "emo", "mos"]})
    assert out["cer"] == 0.0 and out["asr_text"] == "Hello there. Bye."
    assert out["sim"] is None and out["emo_cos"] is None and out["mos"] is None
    assert out["unavailable"] == ["sim", "emo", "mos"]
    # with speaker, emotion and MOS models
    monkeypatch.setitem(qc_worker._STATE, "sv", object())
    monkeypatch.setattr(qc_worker, "op_sim", lambda req: {"sim": 0.61})
    monkeypatch.setitem(qc_worker._STATE, "emotion_dir", "emo")
    vecs = {"a.wav": np.array([1.0, 0.0]), "o.wav": np.array([1.0, 1.0])}
    monkeypatch.setattr(qc_worker, "_emotion_of", lambda path: {"emb": vecs[path]})
    monkeypatch.setitem(qc_worker._STATE, "mos_dir", "mos")
    monkeypatch.setattr(qc_worker, "_mos_of", lambda path: 3.21)
    out = qc_worker.op_score({"id": "a", "wav": "a.wav", "text": "Hello", "orig_wav": "o.wav",
                              "refs": ["r.wav"], "want": ["sim", "emo", "mos"]})
    assert out["sim"] == 0.61 and out["emo_cos"] == pytest.approx(0.7071, abs=1e-4) and out["mos"] == 3.21
    assert out["cer"] is None and "unavailable" not in out
    with pytest.raises(ValueError):
        qc_worker.op_score({"wav": "a.wav", "want": ["pesq"]})


@pytest.mark.parametrize(
    ("ref", "hyp"),
    [("Привет, как дела?", "привет как дела"), ("Ёлка <i>тут</i>", "елка тут"), ("Hello", "Hallo there"),
     ("", ""), ("", "x"), ("It's [laughs] fine", "its fine")],
)
def test_worker_cer_matches_the_cli(ref: str, hyp: str) -> None:
    assert qc_worker._cer(ref, hyp) == pytest.approx(cli_cer(ref, hyp))


def test_load_keeps_mos_and_emotion_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qc_worker, "_resolve_device", lambda requested: "cpu")
    monkeypatch.setitem(qc_worker._STATE, "device", "cpu")
    out = qc_worker.op_load({"op": "load", "device": "cpu", "mos_model": "m", "emotion_model": "e"})
    assert out["mos"] and out["emotion"] and not out["asr"] and not out["sv"]
    assert qc_worker._STATE["mos"] is None and qc_worker._STATE["mos_dir"] == "m"  # nothing loaded yet
