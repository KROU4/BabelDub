"""audio/fit.py: energy-VAD speech span, bounded tempo fit to the exact original length, conform."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from babeldub.audio import fit
from babeldub.audio.dsp import level_db
from babeldub.audio.io import read_audio, write_wav


def tone(seconds: float, sr: int, amp: float = 0.3, f0: float = 150.0) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * f0 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)


def padded(speech: np.ndarray, sr: int, lead: float, trail: float, ch: int = 1) -> np.ndarray:
    full = np.concatenate([np.zeros(int(lead * sr), np.float32), speech, np.zeros(int(trail * sr), np.float32)])
    return np.repeat(full[:, None], ch, axis=1)


def fake_stretcher(calls: list[float]):
    """Time stretch by plain resampling (pitch is irrelevant here): output = ratio x input duration."""

    def stretch(src: Path, dst: Path, ratio: float) -> str:
        calls.append(ratio)
        data, sr = read_audio(src)
        mono = data[:, 0]
        n = int(round(len(mono) * ratio))
        out = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono).astype(np.float32)
        write_wav(dst, out, sr, subtype="FLOAT")
        return "fake"

    return stretch


def test_speech_bounds_and_span() -> None:
    sr = 16000
    data = padded(tone(1.5, sr), sr, 0.4, 0.6)[:, 0]
    start, end = fit.speech_bounds(data, sr)
    assert start / sr == pytest.approx(0.4, abs=0.021) and end / sr == pytest.approx(1.9, abs=0.021)
    assert fit.speech_span(data, sr) == pytest.approx(1.5, abs=0.04)
    assert fit.speech_bounds(np.zeros(sr, np.float32), sr) is None
    assert fit.length_score(2.0, 2.0) == 0.0 and fit.length_score(1.0, 2.0) == fit.length_score(4.0, 2.0)


@pytest.mark.parametrize(("dub_s", "tempo"), [(2.4, 1.2), (2.1, 1.05), (1.0, 0.93), (2.02, 1.0)])
def test_fit_bounds_tempo_and_keeps_exact_length(dub_s: float, tempo: float) -> None:
    sr, o_sr = 24000, 48000
    orig = padded(tone(2.0, o_sr), o_sr, 0.3, 0.5, ch=2)
    calls: list[float] = []
    res = fit.fit_to_original(padded(tone(dub_s, sr, amp=0.05), sr, 0.1, 0.1), sr, orig, o_sr, tempo_min=0.93,
                              tempo_max=1.2, tolerance=0.03, ceiling_db=-1.0, stretcher=fake_stretcher(calls))
    assert res.tempo == pytest.approx(tempo, abs=1e-3)
    assert bool(calls) == (tempo != 1.0)  # within the tolerance the dub keeps its tempo
    assert res.audio.shape == orig.shape and res.exact and res.fits and res.sr == o_sr
    assert res.lead_s == pytest.approx(0.3, abs=0.021)  # speech starts where the original's does
    assert res.level_out == pytest.approx(res.level_ref, abs=0.6)  # matched to the original speech
    assert res.peak_db <= -0.99


def test_fit_never_cuts_speech() -> None:
    sr = 24000
    orig = padded(tone(1.0, sr), sr, 0.2, 0.2)
    res = fit.fit_to_original(padded(tone(3.0, sr), sr, 0.0, 0.0), sr, orig, sr, tempo_min=0.93, tempo_max=1.2,
                              tolerance=0.03, ceiling_db=-1.0, stretcher=fake_stretcher([]))
    assert not res.fits and not res.exact and res.lead_s == 0.0
    assert res.audio.shape[0] == pytest.approx(int(3.0 * sr / 1.2), abs=sr * 0.03)  # longer than the original
    # speech that only overruns the trail moves into the lead and keeps the exact length
    res = fit.fit_to_original(padded(tone(1.35, sr), sr, 0.0, 0.0), sr, orig, sr, tempo_min=1.0, tempo_max=1.0,
                              tolerance=0.03, ceiling_db=-1.0, stretcher=fake_stretcher([]))
    assert res.exact and res.fits and res.lead_s < 0.2


def test_fit_rejects_silence() -> None:
    sr = 16000
    with pytest.raises(ValueError):
        fit.fit_to_original(np.zeros(sr, np.float32), sr, padded(tone(1.0, sr), sr, 0.1, 0.1), sr, tempo_min=0.93,
                            tempo_max=1.2, tolerance=0.03, ceiling_db=-1.0, stretcher=fake_stretcher([]))


def test_conform_matches_length_rate_channels_and_level() -> None:
    ref = padded(tone(1.0, 48000), 48000, 0.2, 0.3, ch=2)
    converted = tone(1.5, 40000, amp=0.02)  # 40 kHz RVC output, a little longer, much quieter
    level = level_db(ref[int(0.2 * 48000) : int(1.2 * 48000), 0], 48000, "lufs")
    out, peak = fit.conform(converted, 40000, ref, 48000, level_ref=level, measure="lufs", ceiling_db=-1.0)
    assert out.shape == ref.shape and peak <= -0.99
    bounds = fit.speech_bounds(out[:, 0], 48000)
    assert bounds is not None
    assert level_db(out[bounds[0] : bounds[1], 0], 48000, "lufs") == pytest.approx(level, abs=0.6)


# ---- fit_segments: never slower, pauses before tempo, fixed_length segment alignment ----------------------
def words(sr: int, spec: list[tuple[str, float]]) -> np.ndarray:
    """("w", seconds) = speech, ("p", seconds) = silence."""
    return np.concatenate([tone(s, sr) if kind == "w" else np.zeros(int(s * sr), np.float32) for kind, s in spec])


def fit_seg(dub: np.ndarray, sr: int, orig: np.ndarray, o_sr: int, calls: list[float], **kw):
    return fit.fit_segments(dub, sr, orig, o_sr, tempo_max=kw.pop("tempo_max", 1.15), tolerance=0.03,
                            ceiling_db=-1.0, stretcher=fake_stretcher(calls), **kw)


def test_speech_chunks_split_on_long_pauses_only() -> None:
    sr = 16000
    data = words(sr, [("p", 0.2), ("w", 0.5), ("p", 0.06), ("w", 0.3), ("p", 0.3), ("w", 0.4), ("p", 0.1)])
    chunks = fit.speech_chunks(data, sr)
    assert len(chunks) == 2  # the 60 ms gap stays inside the first chunk
    assert chunks[0][0] / sr == pytest.approx(0.2, abs=0.021) and chunks[1][0] / sr == pytest.approx(1.36, abs=0.021)
    assert fit.speech_chunks(np.zeros(sr, np.float32), sr) == []
    assert fit.speech_segments_s(data, sr)[1][1] == pytest.approx(1.76, abs=0.021)


@pytest.mark.parametrize("dub_s", [0.6, 1.0, 1.9])
def test_fit_segments_never_slows_down(dub_s: float) -> None:
    sr, o_sr = 24000, 48000
    orig = padded(tone(2.0, o_sr), o_sr, 0.3, 0.5, ch=2)
    calls: list[float] = []
    res = fit_seg(padded(tone(dub_s, sr, amp=0.05), sr, 0.1, 0.1), sr, orig, o_sr, calls)
    assert calls == [] and res.tempo == 1.0 and res.method == "none" and res.chunk_tempos == [1.0]
    assert res.audio.shape == orig.shape and res.exact and res.fits and res.pause_cut_s == 0.0
    assert res.lead_s == pytest.approx(0.3, abs=0.021) and res.onset_deltas == [pytest.approx(0.0, abs=0.021)]
    assert res.ratio == pytest.approx(dub_s / 2.0, abs=0.03)
    assert res.level_out == pytest.approx(res.level_ref, abs=0.6) and res.peak_db <= -0.99


def test_fit_segments_longer_dub_using_the_trail_is_left_alone() -> None:
    sr = 24000
    orig = padded(tone(1.5, sr), sr, 0.3, 0.8)  # room after the onset: 2.3 s
    calls: list[float] = []
    res = fit_seg(padded(tone(2.2, sr), sr, 0.0, 0.0), sr, orig, sr, calls)
    assert calls == [] and res.tempo == 1.0 and res.exact and res.ratio > 1.4


def test_fit_segments_shortens_pauses_before_speeding_up() -> None:
    sr = 24000
    orig = padded(tone(2.0, sr), sr, 0.3, 0.2)  # room 2.2 s
    calls: list[float] = []
    dub = words(sr, [("w", 0.6), ("p", 0.4), ("w", 0.6), ("p", 0.4), ("w", 0.6)])  # 2.6 s, 0.4 s cuttable
    res = fit_seg(dub, sr, orig, sr, calls)
    assert calls == [] and res.tempo == 1.0  # the pauses alone make it fit
    assert res.pause_cut_s == pytest.approx(0.4, abs=0.03)
    assert res.exact and res.fits and res.audio.shape == orig.shape
    placed = fit.speech_chunks(res.audio[:, 0], sr)
    assert len(placed) == 3 and all((b[0] - a[1]) / sr >= 0.18 for a, b in zip(placed, placed[1:], strict=False))
    # still too long after the pauses: the rest is a moderate speed-up
    dub = words(sr, [("w", 0.7), ("p", 0.4), ("w", 0.7), ("p", 0.4), ("w", 0.7)])  # 2.9 s -> 2.5 s -> x1.136
    res = fit_seg(dub, sr, orig, sr, calls)
    assert res.pause_cut_s == pytest.approx(0.4, abs=0.03)
    assert res.tempo == pytest.approx(2.5 / 2.2, abs=0.02) and len(calls) == 1
    assert res.exact and res.fits


def test_fit_segments_caps_tempo_and_never_cuts() -> None:
    sr = 24000
    orig = padded(tone(2.0, sr), sr, 0.3, 0.2)
    calls: list[float] = []
    res = fit_seg(padded(tone(4.0, sr), sr, 0.0, 0.0), sr, orig, sr, calls)
    assert res.tempo == pytest.approx(1.15, abs=1e-6) and calls == [pytest.approx(1 / 1.15)]
    assert not res.fits and not res.exact and res.lead_s == 0.0
    assert res.audio.shape[0] == pytest.approx(int(4.0 * sr / 1.15), abs=sr * 0.03)
    # tempo_max = 1: no speed-up at all
    calls.clear()
    res = fit_seg(padded(tone(4.0, sr), sr, 0.0, 0.0), sr, orig, sr, calls, tempo_max=1.0)
    assert calls == [] and res.tempo == 1.0 and not res.exact


def test_fit_segments_opt_in_slow_down() -> None:
    sr = 24000
    orig = padded(tone(2.0, sr), sr, 0.3, 0.2)
    res = fit_seg(padded(tone(1.0, sr), sr, 0.0, 0.0), sr, orig, sr, [], tempo_min=0.93)
    assert res.tempo == pytest.approx(0.93) and res.exact


ORIG_SEGS = [(0.2, 1.0), (1.6, 2.4), (3.0, 3.5)]


def cutscene(sr: int, ch: int = 1) -> np.ndarray:
    spec = [("p", 0.2), ("w", 0.8), ("p", 0.6), ("w", 0.8), ("p", 0.6), ("w", 0.5), ("p", 0.5)]  # 4.0 s
    return np.repeat(words(sr, spec)[:, None], ch, axis=1)


def test_fit_segments_fixed_length_places_chunks_on_original_onsets() -> None:
    sr, o_sr = 24000, 48000
    orig = cutscene(o_sr, ch=2)
    # dub chunks: shorter, longer, shorter than their segments, with other pauses
    dub = words(sr, [("p", 0.1), ("w", 0.7), ("p", 0.2), ("w", 0.9), ("p", 0.3), ("w", 0.4), ("p", 0.1)])
    calls: list[float] = []
    res = fit_seg(dub, sr, orig, o_sr, calls, fixed_length=True)
    assert res.audio.shape == orig.shape and res.exact and res.fits
    assert len(res.onset_deltas) == 3 and all(abs(d) <= 0.025 for d in res.onset_deltas)
    assert res.chunk_tempos[0] == 1.0 and res.chunk_tempos[2] == 1.0  # shorter is fine: never slowed
    assert res.chunk_tempos[1] == pytest.approx(0.9 / 0.8, abs=0.03) and len(calls) == 1
    placed = fit.speech_chunks(res.audio[:, 0], o_sr)
    assert [round(s / o_sr, 1) for s, _ in placed] == [0.2, 1.6, 3.0]
    for (s, e), (os_, oe) in zip(placed, ORIG_SEGS, strict=True):
        assert (e - s) / o_sr <= (oe - os_) + 0.03  # one-sided: not longer than the segment


def test_fit_segments_fixed_length_uses_analysis_segments_and_merges() -> None:
    sr = 24000
    orig = cutscene(sr)
    # one long dub phrase for the first two segments, a short one for the third
    dub = words(sr, [("w", 2.0), ("p", 0.3), ("w", 0.4)])
    calls: list[float] = []
    res = fit_seg(dub, sr, orig, sr, calls, fixed_length=True, orig_segments=[list(s) for s in ORIG_SEGS])
    assert len(res.onset_deltas) == 2 and all(abs(d) <= 0.025 for d in res.onset_deltas)
    assert res.chunk_tempos == [1.0, 1.0] and calls == [] and res.exact
    # given segments win over the energy VAD: a single segment over the whole speech
    res = fit_seg(dub, sr, orig, sr, [], fixed_length=True, orig_segments=[[0.2, 3.5]])
    assert len(res.onset_deltas) == 1 and res.lead_s == pytest.approx(0.2, abs=0.021)
    # an overrunning chunk is capped and pushes the next one later; speech is never cut
    dub = words(sr, [("w", 1.2), ("p", 0.3), ("w", 0.8)])
    res = fit_seg(dub, sr, orig, sr, [], fixed_length=True, orig_segments=[[0.2, 1.0], [1.1, 1.9]])
    assert res.chunk_tempos == [pytest.approx(1.15), 1.0] and res.onset_deltas[0] == pytest.approx(0.0, abs=0.021)
    assert res.onset_deltas[1] == pytest.approx(1.2 / 1.15 - 0.9, abs=0.03) and res.ratio > 1.0 and res.exact
    # the overrun shows in the offsets: the first chunk ends at its segment, the pushed one late
    assert res.offset_deltas[0] == pytest.approx(1.2 / 1.15 - 0.8, abs=0.03)
    assert res.offset_deltas[1] == pytest.approx(0.2 + 1.2 / 1.15 + 0.8 - 1.9, abs=0.03) and res.offset_deltas[1] > 0.08


@pytest.mark.parametrize("frames", [47999, 48001, 52357])
def test_fit_segments_keeps_exact_sample_count(frames: int) -> None:
    sr, o_sr = 22050, 48000
    orig = np.zeros((frames, 2), np.float32)
    orig[4800 : 4800 + 24000] = tone(0.5, o_sr)[:, None]
    for fixed in (False, True):
        res = fit_seg(padded(tone(0.45, sr), sr, 0.05, 0.05), sr, orig, o_sr, [], fixed_length=fixed)
        assert res.audio.shape == (frames, 2) and res.exact


def test_fit_segments_rejects_silence() -> None:
    sr = 16000
    with pytest.raises(ValueError):
        fit_seg(np.zeros(sr, np.float32), sr, padded(tone(1.0, sr), sr, 0.1, 0.1), sr, [])


def test_align_chunks_dp_edge_cases() -> None:
    assert fit.align_chunks([(0, 100)], [(0, 300)]) == [((0, 1), (0, 1))]
    # equal lists pair one to one (ties prefer more groups)
    same = [(0, 100), (200, 300), (400, 500)]
    assert fit.align_chunks(same, same) == [((0, 1), (0, 1)), ((1, 2), (1, 2)), ((2, 3), (2, 3))]
    # a split dub chunk is merged against one original segment
    assert fit.align_chunks([(0, 100), (110, 200), (400, 500)], [(0, 200), (400, 500)]) == [
        ((0, 2), (0, 1)), ((2, 3), (1, 2))]
    # and the other way round
    assert fit.align_chunks([(0, 200), (400, 500)], [(0, 90), (120, 200), (400, 500)]) == [
        ((0, 1), (0, 2)), ((1, 2), (2, 3))]
    # many against one, beyond the merge limit
    many = [(i * 100, i * 100 + 50) for i in range(20)]
    assert fit.align_chunks(many, [(0, 2000)]) == [((0, 20), (0, 1))]
    assert fit.align_chunks([(0, 2000)], many) == [((0, 1), (0, 20))]
    with pytest.raises(ValueError):
        fit.align_chunks([], [(0, 1)])
