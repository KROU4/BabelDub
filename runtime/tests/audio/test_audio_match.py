"""audio/match.py: LTAS EQ, bandwidth match and the radio chain on synthetic speech-like signals."""

from __future__ import annotations

import numpy as np
import pytest

from babeldub.audio import match
from babeldub.qa.metrics import ltas_distance_db

SR = 48000


def voice(seconds: float, sr: int = SR, f0: float = 120.0, seed: int = 0) -> np.ndarray:
    """Harmonics (voiced) plus noise bursts (fricatives) under a syllable envelope, 0.2 s silence on both sides."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sr)) / sr
    harmonics = sum(np.sin(2 * np.pi * k * f0 * t + k) / k for k in range(1, int(8000 / f0)))
    noise = rng.standard_normal(len(t)) * (np.sin(2 * np.pi * 2.0 * t) > 0.6)
    sig = (harmonics + 0.6 * noise) * (0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t))
    sig = 0.3 * sig / np.max(np.abs(sig))
    pad = np.zeros(int(0.2 * sr))
    return np.concatenate([pad, sig, pad]).astype(np.float32)


def channel(audio: np.ndarray, eq_db: np.ndarray, lowpass: float | None = None, highpass: float | None = None) -> np.ndarray:
    return match.apply_fir(audio, match.design_fir(SR, eq_db, lowpass=lowpass, highpass=highpass))


TILT = np.linspace(4.0, -6.0, len(match.BAND_CENTERS))  # a darker channel: +4 dB lows, -6 dB highs


def test_profile_measures_speech_frames_only() -> None:
    sig = voice(2.0)
    p = match.profile(sig, SR)
    assert p.speech_s == pytest.approx(2.0, abs=0.25)
    longer = match.profile(np.concatenate([sig, np.zeros(SR * 3, np.float32)]), SR)  # silence does not count
    assert np.nanmax(np.abs(longer.bands_db - p.bands_db)) < 0.5
    assert match.profile(np.zeros(SR, np.float32), SR).speech_s == 0.0
    low = match.profile(voice(1.0, sr=16000), 16000)
    assert np.isnan(low.bands_db[-1]) and not np.isnan(low.bands_db[0])  # no bands above the Nyquist limit


def test_eq_matches_a_tilted_channel_and_keeps_length() -> None:
    dub, orig = voice(2.0, seed=1), channel(voice(2.0, seed=2), TILT)
    res = match.match_channel(dub, SR, orig, SR, "eq")
    assert res.applied and res.audio.shape == dub.shape and res.audio.dtype == np.float32
    assert res.info["cutoff_hz"] is None and res.info["radio"] is False and res.info["ref"] == "line"
    eq = np.array(res.info["eq_db"])
    assert np.all(np.abs(eq) <= match.MAX_EQ_DB)
    assert eq[1] - eq[-3] == pytest.approx(TILT[1] - TILT[-3], abs=2.5)  # the slope is recovered
    before, after = res.info["ltas_db"]["before"], res.info["ltas_db"]["after"]
    assert after < before * 0.5 and after < 1.5
    assert ltas_distance_db(res.audio, SR, orig, SR) == pytest.approx(after, abs=0.01)
    # deterministic, and a second pass changes (almost) nothing
    assert np.array_equal(match.match_channel(dub, SR, orig, SR, "eq").audio, res.audio)
    again = match.match_channel(res.audio, SR, orig, SR, "eq")
    assert np.max(np.abs(again.info["eq_db"])) < 1.5


def test_eq_is_clipped_and_never_boosts_what_the_dub_lacks() -> None:
    steep = np.where(np.arange(len(match.BAND_CENTERS)) < 6, 20.0, 0.0)  # +20 dB under 250 Hz
    res = match.match_channel(voice(2.0, seed=1), SR, channel(voice(2.0, seed=2), steep), SR, "eq")
    assert max(res.info["eq_db"]) == pytest.approx(match.MAX_EQ_DB)
    # a dub with nothing above 4 kHz: the original's highs are not "restored" with boosted noise
    dark = channel(voice(2.0, seed=1), np.zeros(len(match.BAND_CENTERS)), lowpass=4000.0)
    res = match.match_channel(dark, SR, voice(2.0, seed=2), SR, "eq")
    high = [g for g, fc in zip(res.info["eq_db"], match.BAND_CENTERS, strict=True) if fc >= 6000]
    assert max(high) <= 0.5


def test_bandwidth_match_low_passes_at_the_originals_cutoff() -> None:
    orig = channel(voice(2.0, seed=2), np.zeros(len(match.BAND_CENTERS)), lowpass=8000.0)  # an 8 kHz bank
    dub = voice(2.0, seed=1)
    res = match.match_channel(dub, SR, orig, SR, "eq")
    assert res.info["cutoff_hz"] == pytest.approx(8000, rel=0.12) and res.info["radio"] is False
    out = match.profile(res.audio, SR)
    top = [b for b, fc in enumerate(match.BAND_CENTERS) if fc >= 11000]
    speech = match._speech_level(out.bands_db)
    assert all(out.bands_db[b] - speech < -40 for b in top)
    assert res.info["ltas_db"]["after"] < res.info["ltas_db"]["before"] * 0.5
    # a full-band original: no cutoff
    assert match.match_channel(dub, SR, voice(2.0, seed=2), SR, "eq").info["cutoff_hz"] is None


def test_radio_original_gets_the_radio_chain_only_in_full_mode() -> None:
    flat = np.zeros(len(match.BAND_CENTERS))
    orig = channel(voice(2.0, seed=2), flat, lowpass=3400.0, highpass=300.0)  # a phone/radio line
    dub = voice(2.0, seed=1)
    eq = match.match_channel(dub, SR, orig, SR, "eq")
    full = match.match_channel(dub, SR, orig, SR, "full")
    assert eq.info["radio"] is True and eq.info["radio_chain"] is False and eq.info["low_hz"] is None
    assert full.info["radio"] is True and full.info["radio_chain"] is True
    assert 150 <= full.info["low_hz"] <= 400 and full.info["cutoff_hz"] == pytest.approx(3400, rel=0.15)
    assert full.info["cutoff_db"] == pytest.approx(match.STOP_DB) and full.info["low_db"] <= -30  # hard edges
    out, src, ref = (match.profile(a, SR) for a in (full.audio, dub, orig))
    rel = [p.bands_db - match._speech_level(p.bands_db) for p in (out, src, ref)]
    lows = [b for b, fc in enumerate(match.BAND_CENTERS) if fc <= 125]
    assert all(rel[0][b] < rel[1][b] - 25 and abs(rel[0][b] - rel[2][b]) < 12 for b in lows)  # band-passed
    assert full.info["ltas_db"]["after"] < eq.info["ltas_db"]["after"] < eq.info["ltas_db"]["before"]
    assert np.max(np.abs(full.audio)) <= np.max(np.abs(dub)) * 1.5  # saturation, no blow-up
    # a merely darker original is no radio
    assert match.match_channel(dub, SR, channel(voice(2.0, seed=2), TILT), SR, "full").info["radio"] is False


def test_a_soft_phone_band_limit_counts_as_radio() -> None:
    # highs from ~4 kHz 14 dB down, lows up to 200 Hz 15 dB down: no hard edges, still a phone/radio line
    centers = np.array(match.BAND_CENTERS)
    soft = np.where(centers >= 4000, -14.0, 0.0) + np.where(centers <= 200, -15.0, 0.0)
    orig, dub = channel(voice(2.0, seed=2), soft), voice(2.0, seed=1)
    eq = match.match_channel(dub, SR, orig, SR, "eq")
    full = match.match_channel(dub, SR, orig, SR, "full")
    assert 3500 <= full.info["cutoff_hz"] <= match.RADIO_MAX_CUTOFF_HZ
    assert eq.info["radio"] is True and eq.info["radio_chain"] is False
    assert full.info["radio"] is True and full.info["radio_chain"] is True and full.info["low_hz"] is not None
    # the stop bands follow the soft limit instead of cutting 60 dB: the dub gets closer, not further
    assert -20 <= full.info["cutoff_db"] <= -match.CUTOFF_DROP_DB and -20 <= full.info["low_db"] <= match.RADIO_LOW_DB
    for res in (eq, full):
        assert res.info["ltas_db"]["after"] < res.info["ltas_db"]["before"] * 0.6
    # the same soft high cut with full lows is a band limit, not a radio
    highs_only = match.match_channel(dub, SR, channel(voice(2.0, seed=2), np.where(centers >= 4000, -14.0, 0.0)),
                                     SR, "full")
    assert highs_only.info["cutoff_hz"] is not None and highs_only.info["radio"] is False


def test_short_line_pools_the_characters_originals_and_off_or_silence_do_nothing() -> None:
    dub = voice(0.5, seed=1)
    short = channel(voice(0.5, seed=2), TILT)
    others = [match.profile(channel(voice(2.0, seed=s), TILT), SR) for s in (3, 4)]
    res = match.match_channel(dub, SR, short, SR, "eq", extra=others)
    assert res.applied and res.info["ref"] == "character"
    assert match.match_channel(dub, SR, short, SR, "eq").info["ref"] == "line"
    # a blip of an original: no bandwidth/radio check of its own, the EQ target is the character's originals
    blip = match.match_channel(dub, SR, channel(voice(0.1, seed=5), TILT), SR, "eq", extra=lambda: others)
    assert blip.applied and blip.info["ref"] == "character" and blip.info["cutoff_hz"] is None
    assert not match.match_channel(dub, SR, channel(voice(0.1, seed=5), TILT), SR, "eq").applied
    off = match.match_channel(dub, SR, short, SR, "off")
    assert not off.applied and off.audio is dub
    silent = match.match_channel(dub, SR, np.zeros(SR, np.float32), SR, "eq")
    assert not silent.applied and silent.info["reason"] and silent.audio is dub
    with pytest.raises(ValueError):
        match.match_channel(dub, SR, short, SR, "loud")


def test_character_pair_recovers_the_shared_channel_the_line_curves_over_fit() -> None:
    # six "lines": each original has its own content colouring (phonemes) over one shared channel tilt, each dub
    # another colouring of its own; the colourings average out of both pools, the tilt stays
    idx = np.arange(len(match.BAND_CENTERS))
    dubs, origs, line_eqs = [], [], []
    for j in range(6):
        phase = 2 * np.pi * j / 6
        orig = channel(voice(2.0, seed=10 + j), TILT + 3.0 * np.sin(2 * np.pi * idx / 8.0 + phase))
        dub = channel(voice(2.0, seed=30 + j), 3.0 * np.sin(2 * np.pi * idx / 5.0 + 2 * phase + 1.0))
        origs.append(match.profile(orig, SR))
        dubs.append(match.profile(dub, SR))
        line_eqs.append(np.array(match.match_channel(dub, SR, orig, SR, "eq").info["eq_db"]))
        if j == 0:
            first = (dub, orig)
    res = match.match_channel(first[0], SR, first[1], SR, "eq", pair=(dubs, origs))
    assert res.applied and res.info["ref"] == "pair" and res.info["pair_lines"] == 6
    pair_eq = np.array(res.info["eq_db"])
    speech = [b for b, fc in enumerate(match.BAND_CENTERS) if 300 <= fc <= 3400]
    shared = TILT - TILT[speech].mean()
    assert np.mean(np.abs(pair_eq - shared)) < 0.5  # the channel itself
    assert all(np.mean(np.abs(eq - shared)) > 1.0 for eq in line_eqs)  # each line curve carries its content
    assert np.mean(np.abs(pair_eq)) < np.mean([np.mean(np.abs(eq)) for eq in line_eqs])
    # ltas_db is still measured against the line's own original; an empty pair is no pair
    assert res.info["ltas_db"]["after"] is not None
    assert match.match_channel(first[0], SR, first[1], SR, "eq", pair=([], [])).info["ref"] == "line"


def test_stereo_input_keeps_its_channels_and_ltas_distance_metric() -> None:
    dub = np.repeat(voice(1.5, seed=1)[:, None], 2, axis=1)
    orig = channel(voice(1.5, seed=2), TILT)
    res = match.match_channel(dub, SR, orig, SR, "eq")
    assert res.audio.shape == dub.shape and np.allclose(res.audio[:, 0], res.audio[:, 1])
    assert ltas_distance_db(voice(1.5, seed=1), SR, voice(1.5, seed=1), SR) == pytest.approx(0.0, abs=1e-6)
    assert ltas_distance_db(np.zeros(SR, np.float32), SR, orig, SR) is None
    assert ltas_distance_db(dub, SR, orig, SR) > 1.0
