"""qc_worker analysis and scoring ops with faked models (the worker module imports no torch at load)."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import pytest

from babeldub.qa.metrics import RU_VOWEL_FOLD
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
    for key in ("asr", "sv", "mos", "mos_dir", "emotion", "emotion_dir", "accent", "accent_dir", "asr2", "asr2_dir",
                "asr2_error", "mos_backend", "mos_fallback_dir", "mos_used", "device"):
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
    monkeypatch.setattr(qc_worker, "CONTOUR_HZ", 100)  # one point per frame
    out = qc_worker.op_prosody({"id": "a", "wav": "a.wav"})
    lo, hi = np.percentile(f0[voiced], [10, 90])
    assert out["f0_median_hz"] == pytest.approx(140.0) and out["f0_range_st"] == round(12 * np.log2(hi / lo), 2)
    assert out["rms_db"] == pytest.approx(-20.0) and out["voiced_ratio"] == 0.8
    contour = out["contour"]  # semitones from the median, None where unvoiced
    assert len(contour) == 10 and contour[0] is None and contour[1] == pytest.approx(round(12 * np.log2(100 / 140), 2))
    assert out["voiced_s"] == 0.08 and out["rms_std_db"] is not None


def _fake_pyin(monkeypatch: pytest.MonkeyPatch, coarse: np.ndarray, fine: np.ndarray) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def pyin(y: np.ndarray, **kw: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        calls.append(kw)
        f0 = coarse if kw["hop_length"] == qc_worker.COARSE_HOP else fine
        return f0, ~np.isnan(f0), np.ones_like(f0)

    lib = ModuleType("librosa")
    lib.pyin = pyin  # type: ignore[attr-defined]
    lib.feature = SimpleNamespace(rms=lambda y, **kw: np.array([[0.1, 0.05, 0.0001]]))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "librosa", lib)
    return calls


def test_prosody_second_pass_is_one_octave_around_the_coarse_median(monkeypatch: pytest.MonkeyPatch) -> None:
    coarse = np.array([200.0, 205.0, 410.0, np.nan, 198.0])  # one octave jump in the coarse pass
    fine = np.array([200.0, 210.0, 190.0, np.nan, 220.0, 180.0])
    calls = _fake_pyin(monkeypatch, coarse, fine)
    out = qc_worker.op_prosody({"id": "a", "wav": "a.wav"})
    assert [c["hop_length"] for c in calls] == [qc_worker.COARSE_HOP, 160]
    assert (calls[0]["fmin"], calls[0]["fmax"]) == (qc_worker.F0_MIN_HZ, qc_worker.F0_MAX_HZ)
    assert calls[1]["fmin"] == pytest.approx(101.25) and calls[1]["fmax"] == pytest.approx(405.0)
    assert out["f0_bounds_hz"] == [101.2, 405.0] and out["f0_bounds_source"] == "coarse"
    assert out["f0_median_hz"] == 200.0 and out["voiced_s"] == 0.05


def test_prosody_hint_sets_the_bounds_and_they_are_clipped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_pyin(monkeypatch, np.array([np.nan]), np.array([120.0, 130.0, 125.0]))
    qc_worker.op_prosody({"id": "a", "wav": "a.wav", "f0_hint_hz": 140.0})
    assert len(calls) == 1 and (calls[0]["fmin"], calls[0]["fmax"]) == (70.0, 280.0)
    calls.clear()
    qc_worker.op_prosody({"id": "a", "wav": "a.wav", "f0_hint_hz": 80.0})
    assert (calls[0]["fmin"], calls[0]["fmax"]) == (50.0, 160.0)  # never below 50 Hz
    calls.clear()
    qc_worker.op_prosody({"id": "a", "wav": "a.wav", "f0_hint_hz": 900.0})
    assert (calls[0]["fmin"], calls[0]["fmax"]) == (450.0, 1000.0)  # never above 1000 Hz


def test_prosody_without_a_voice_in_the_coarse_pass_keeps_the_wide_range(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_pyin(monkeypatch, np.array([np.nan, np.nan, 300.0]), np.array([np.nan, np.nan, np.nan]))
    out = qc_worker.op_prosody({"id": "a", "wav": "a.wav"})
    assert (calls[1]["fmin"], calls[1]["fmax"]) == (qc_worker.F0_MIN_HZ, qc_worker.F0_MAX_HZ)
    assert out["f0_bounds_source"] == "wide" and out["f0_median_hz"] is None and out["voiced_s"] == 0.0


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
     ("", ""), ("", "x"), ("It's [laughs] fine", "its fine"), ("Посмотрим, закрыто!", "пасмотрим закрыта"),
     ("Тебя здесь нет", "тибя здесь нет"), ("Эй, ёж", "ий иж")],
)
@pytest.mark.parametrize("fold", [False, True])
def test_worker_cer_matches_the_cli(ref: str, hyp: str, fold: bool) -> None:
    assert qc_worker._cer(ref, hyp, fold=fold) == pytest.approx(cli_cer(ref, hyp, fold=fold))
    assert qc_worker.RU_VOWEL_FOLD == RU_VOWEL_FOLD


class FakeGiga:
    """transformers' GigaAMModel: `.model` is the GigaAMASR with forward, head and greedy decoding."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.lengths: list[int] = []
        outer = self

        class Inner:
            head = object()

            def parameters(self) -> Any:
                return iter([SimpleNamespace(device="cpu")])

            def forward(self, wav: Any, length: Any) -> tuple[Any, Any]:
                outer.lengths.append(int(wav.shape[-1]))
                return wav, length

            class decoding:  # noqa: N801 - mirrors the attribute name
                @staticmethod
                def decode(head: Any, encoded: Any, lengths: Any) -> list[str]:
                    return [outer.texts.pop(0)]

        self.model = Inner()


class FakeTorchTensor:
    def __init__(self, data: np.ndarray) -> None:
        self.data = data
        self.shape = data.shape

    def to(self, device: Any) -> FakeTorchTensor:
        return self

    def unsqueeze(self, dim: int) -> FakeTorchTensor:
        return FakeTorchTensor(self.data[None, :])


def _fake_torch() -> ModuleType:
    import contextlib

    torch = ModuleType("torch")
    torch.from_numpy = FakeTorchTensor  # type: ignore[attr-defined]
    torch.full = lambda shape, value, device=None: value  # type: ignore[attr-defined]
    torch.inference_mode = contextlib.nullcontext  # type: ignore[attr-defined]
    return torch


def test_asr2_transcribes_long_clips_in_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    long = np.zeros(int(45 * SR), dtype=np.float32)
    monkeypatch.setattr(qc_worker, "_read_16k", lambda path: [FakeTensor(long)])
    giga = FakeGiga([" садись ", "в машину", ""])
    monkeypatch.setitem(qc_worker._STATE, "asr2", giga)
    out = qc_worker.op_asr2({"id": "a", "wav": "a.wav"})
    assert out == {"id": "a", "text": "садись в машину", "model": "gigaam-v3-ctc"}
    assert giga.lengths == [20 * SR, 20 * SR, 5 * SR]


def test_score_uses_the_second_asr_for_russian_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(qc_worker._STATE, "asr", FakeWhisper())
    monkeypatch.setitem(qc_worker._STATE, "asr2_dir", "giga")
    monkeypatch.setattr(qc_worker, "_asr2_text", lambda path: "пасмотрим")
    out = qc_worker.op_score({"id": "a", "wav": "a.wav", "text": "Посмотрим", "lang": "ru", "want": ["cer"]})
    assert out["asr2_text"] == "пасмотрим" and out["cer2"] == 0.0 and out["cer_folded"] is not None
    out = qc_worker.op_score({"id": "a", "wav": "a.wav", "text": "Hello", "lang": "en", "want": ["cer"]})
    assert "asr2_text" not in out and "cer_folded" not in out

    def broken(path: str) -> str:
        raise RuntimeError("GigaAM needs transformers")

    monkeypatch.setattr(qc_worker, "_asr2_text", broken)
    out = qc_worker.op_score({"id": "a", "wav": "a.wav", "text": "Посмотрим", "lang": "ru", "want": ["cer"]})
    assert out["unavailable"] == ["asr2"] and "transformers" in out["asr2_error"] and out["cer"] is not None


def test_asr2_load_failure_is_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(qc_worker._STATE, "asr2_dir", "giga")
    monkeypatch.setitem(qc_worker._STATE, "asr2_error", None)
    monkeypatch.setitem(sys.modules, "transformers", None)  # import fails
    with pytest.raises(RuntimeError, match="hydra-core"):
        qc_worker._get_asr2()
    assert "setup --engines qc" in qc_worker._STATE["asr2_error"]
    monkeypatch.delitem(sys.modules, "transformers")
    with pytest.raises(RuntimeError, match="hydra-core"):  # not retried
        qc_worker._get_asr2()


def test_mos_backends_and_the_utmos_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qc_worker, "_resolve_device", lambda requested: "cpu")
    monkeypatch.setitem(qc_worker._STATE, "device", "cpu")
    for key in ("mos_backend", "mos_fallback_dir", "mos_used"):
        monkeypatch.setitem(qc_worker._STATE, key, None)

    class Model:
        def __init__(self, name: str) -> None:
            self.name = name

        def eval(self) -> Model:
            return self

        def to(self, device: str) -> Model:
            return self

    loaded: list[str] = []
    monkeypatch.setattr(qc_worker, "_load_utmos22", lambda folder: loaded.append(f"utmos:{folder}") or Model("u"))
    monkeypatch.setattr(qc_worker, "_load_distillmos", lambda folder: loaded.append(f"distill:{folder}") or Model("d"))
    qc_worker.op_load({"op": "load", "device": "cpu", "mos_model": "dm", "mos_backend": "distillmos",
                       "mos_fallback": "ut"})
    assert qc_worker._get_mos().name == "d" and qc_worker._STATE["mos_used"] == "distillmos"
    assert loaded == ["distill:dm"]

    def missing(folder: str) -> Any:
        raise ImportError("No module named 'distillmos'")

    monkeypatch.setattr(qc_worker, "_load_distillmos", missing)
    qc_worker.op_load({"op": "load", "device": "cpu", "mos_model": "dm2", "mos_backend": "distillmos",
                       "mos_fallback": "ut"})
    assert qc_worker._get_mos().name == "u" and qc_worker._STATE["mos_used"] == "utmos22"  # fell back
    qc_worker.op_load({"op": "load", "device": "cpu", "mos_model": "dm3", "mos_backend": "distillmos"})
    monkeypatch.setitem(qc_worker._STATE, "mos_fallback_dir", None)
    with pytest.raises(RuntimeError, match="distillmos package"):
        qc_worker._get_mos()
    qc_worker.op_load({"op": "load", "device": "cpu", "mos_model": "ut2", "mos_backend": "utmos22"})
    assert qc_worker._get_mos().name == "u" and loaded[-1] == "utmos:ut2"


def test_mos_reports_the_model_that_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(qc_worker._STATE, "mos_used", "distillmos")
    monkeypatch.setattr(qc_worker, "_mos_of", lambda path: 3.14159)
    assert qc_worker.op_mos({"id": "a", "wav": "a.wav"}) == {"id": "a", "mos": 3.142, "mos_model": "distillmos"}


def test_load_keeps_mos_and_emotion_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qc_worker, "_resolve_device", lambda requested: "cpu")
    monkeypatch.setitem(qc_worker._STATE, "device", "cpu")
    out = qc_worker.op_load({"op": "load", "device": "cpu", "mos_model": "m", "emotion_model": "e"})
    assert out["mos"] and out["emotion"] and not out["asr"] and not out["sv"]
    assert qc_worker._STATE["mos"] is None and qc_worker._STATE["mos_dir"] == "m"  # nothing loaded yet


def test_score_prosody_of_the_take_and_the_original(monkeypatch: pytest.MonkeyPatch) -> None:
    hints: list[Any] = []

    def prosody(req: dict[str, Any]) -> dict[str, Any]:
        hints.append(req.get("f0_hint_hz"))
        return {"f0_range_st": 5.0 if req["wav"] == "a.wav" else 7.0}

    monkeypatch.setattr(qc_worker, "op_prosody", prosody)
    out = qc_worker.op_score({"id": "a", "wav": "a.wav", "orig_wav": "o.wav", "want": ["prosody"], "f0_hint_hz": 150})
    assert out["prosody"]["f0_range_st"] == 5.0 and out["orig_prosody"]["f0_range_st"] == 7.0
    assert hints == [150, 150]  # the character's median bounds both passes


def test_accent_marks_homographs_only(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAccent:
        omographs = {"замок": ["з+амок", "зам+ок"]}

        def process_all(self, text: str) -> str:
            return text.replace("замок", "зам+ок").replace("открой", "откр+ой")

    monkeypatch.setitem(qc_worker._STATE, "accent", FakeAccent())
    out = qc_worker.op_accent({"texts": ["открой замок", "Зб+ышек, открой", ""]})
    assert out["texts"] == ["открой зам+ок", "Зб+ышек, открой", ""]  # input marks are kept
    full = qc_worker.op_accent({"texts": ["открой замок"], "homographs_only": False})
    assert full["texts"] == ["откр+ой зам+ок"]
    monkeypatch.setattr(qc_worker, "_resolve_device", lambda requested: "cpu")
    monkeypatch.setitem(qc_worker._STATE, "device", "cpu")
    load = qc_worker.op_load({"op": "load", "device": "cpu", "accent_model": "ra"})
    assert load["accent"] and qc_worker._STATE["accent"] is None  # reloaded lazily from the new dir
