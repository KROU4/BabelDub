"""Delivery tags measured on the originals (analysis/prosody_tags.py) with synthetic populations."""

from __future__ import annotations

import math
from typing import Any

import pytest

from babeldub.analysis import prosody_tags as pt


def row(f0: float | None = 130.0, rms: float = -20.0, voiced: float = 0.6, rng: float | None = 6.0,
        rate: float = 4.5, speech: float = 2.5, dur: float = 3.0, cls: str = "speech") -> dict[str, Any]:
    return {"speech_s": speech, "dur_s": dur, "class": cls,
            "prosody": {"f0_median_hz": f0, "f0_range_st": rng, "rms_db": rms, "voiced_ratio": voiced, "syl_rate": rate}}


def normal(i: int) -> dict[str, Any]:
    """A plain line of the character: tiny spread, so the sigma floors set the scale."""
    return row(f0=130.0 + (i % 3 - 1), rms=-20.0 + 0.1 * (i % 5 - 2), voiced=0.6 + 0.005 * (i % 3 - 1),
               rng=6.0 + 0.1 * (i % 4 - 1.5), rate=4.5 + 0.05 * (i % 3 - 1))


def cast_of(analysis: dict[str, Any], char: str = "hero") -> dict[str, str]:
    return {lid: char for lid in analysis}


def population(extra: dict[str, dict[str, Any]], n: int = 10) -> dict[str, dict[str, Any]]:
    return {**{f"n{i}": normal(i) for i in range(n)}, **extra}


def semitones_up(st: float, base: float = 130.0) -> float:
    return base * 2 ** (st / 12)


def test_shout_whisper_and_calm_lines_get_their_tags() -> None:
    analysis = population({
        "shout": row(f0=semitones_up(9), rms=-10.0),
        "whisper": row(f0=None, rng=None, rms=-32.0, voiced=0.05),
        "calm": row(f0=semitones_up(-2), rms=-21.5),
    })
    measured = pt.measure(analysis, cast_of(analysis))
    assert measured["shout"].tags == ["shouting"] and measured["shout"].control == "shouting"
    assert measured["shout"].z["f0"] == pytest.approx(9.0, abs=0.6) and measured["shout"].ref["rms"]["n"] == 12
    assert measured["whisper"].tags == ["whispered"] and "f0" not in measured["whisper"].z
    assert measured["calm"].tags == ["calm"]
    assert all(not measured[f"n{i}"].tags for i in range(10))  # the plain lines stay untagged


def test_tempo_tags_and_slight_phrases() -> None:
    analysis = population({"fast": row(rate=5.3), "faster": row(rate=5.1), "slow": row(rate=3.7),
                           "short_fast": row(rate=6.5, speech=1.0)})
    measured = pt.measure(analysis, cast_of(analysis))
    assert measured["fast"].tags == ["quick"] and measured["fast"].control == "fast pace"  # z 2.29
    assert measured["faster"].tags == ["quick"] and measured["faster"].control == "slightly faster"  # z 1.71
    assert measured["slow"].tags == ["slow"] and measured["slow"].control == "slow pace"
    assert "rate" not in measured["short_fast"].z and measured["short_fast"].tags == []  # rate needs LONG_S
    assert pt.control_phrase(["quick"]) == "fast pace"  # an agent tag has no z: the full phrase
    assert pt.control_phrase(["shouting", "quick"], {"rate": 1.6}) == "shouting, slightly faster"
    assert pt.control_phrase(["unknown", "soft"]) == "soft voice"


def test_too_small_cast_and_missing_analysis_give_no_tags() -> None:
    small = {f"s{i}": normal(i) for i in range(4)} | {"shout": row(f0=semitones_up(9), rms=-10.0)}
    measured = pt.measure(small, cast_of(small))
    assert measured["shout"].tags == [] and measured["shout"].z == {}  # 4 other lines < MIN_POPULATION
    analysis = population({"shout": row(f0=semitones_up(9), rms=-10.0)})
    chars = cast_of(analysis)
    chars.pop("n0")  # a line without a character is not measured
    measured = pt.measure(analysis, chars)
    assert "n0" not in measured and "never_analysed" not in measured
    unknown = pt.measure(analysis, cast_of(analysis, "unknown"))  # a mix of voices: no population
    assert unknown == {}


def test_nonverbal_lines_are_excluded_from_the_population() -> None:
    analysis = {f"n{i}": normal(i) for i in range(4)}
    analysis["laugh"] = row(f0=semitones_up(12), rms=-8.0, cls="nonverbal")
    analysis["shout"] = row(f0=semitones_up(9), rms=-10.0)
    measured = pt.measure(analysis, cast_of(analysis))
    assert "laugh" not in measured
    assert measured["shout"].tags == []  # 5 other lines with the laugh, 4 comparable ones without it: too few
    analysis["n4"] = normal(4)
    assert pt.measure(analysis, cast_of(analysis))["shout"].tags == ["shouting"]
    analysis["tiny"] = row(speech=0.2)
    assert "tiny" not in pt.measure(analysis, cast_of(analysis))  # under MIN_SPEECH_S


def test_pitch_tracking_failure_is_ignored() -> None:
    feats = pt.features(row(rng=30.0))
    assert feats["f0"] is None and feats["rng"] is None and feats["rms"] == -20.0
    feats = pt.features(row(voiced=0.5, speech=2.0, dur=3.0))
    assert feats["voic"] == pytest.approx(0.75) and feats["long"] is True
    assert pt.features(row(f0=200.0))["f0"] == pytest.approx(12 * math.log2(2.0))
    assert pt.features({"prosody": {"rms_db": float("nan")}})["rms"] is None


def test_lab_order_of_tags_and_cap() -> None:
    assert pt.tags_from_z({"f0": 2.5, "rms": 1.2, "rate": 2.5, "rng": 2.5}) == ["shouting", "quick", "expressive"]
    assert pt.tags_from_z({"f0": 1.2, "rms": 1.0, "rate": 1.2}) == ["urgent"]
    assert pt.tags_from_z({"f0": -1.2, "rms": -1.1, "rate": -0.8}) == ["sad"]
    assert pt.tags_from_z({"rms": -2.2, "rng": -2.5}) == ["whispered"]  # no flat after a whisper
    assert pt.tags_from_z({"rms": 2.1, "f0": 0.0}) == ["loud"]
    assert pt.tags_from_z({}) == []


def test_parse_and_clean_agent_tags() -> None:
    assert pt.parse_tags([" Shouting", "quick", "quick"]) == ["shouting", "quick"]
    assert pt.parse_tags([]) == []
    for bad, message in ((["sarcastic"], "unknown delivery tag"), (["calm", "sad", "soft", "slow"], "at most 3"),
                         ("calm", "must be a list"), ([1], "must be strings")):
        with pytest.raises(ValueError, match=message):
            pt.parse_tags(bad)
    assert pt.clean_tags(["calm", "bogus", "SAD", "soft", "slow"]) == ["calm", "sad", "soft"]
    assert pt.clean_tags("calm") == []
    assert set(pt.VOCABULARY) == {"shouting", "whispered", "urgent", "energetic", "calm", "sad", "soft", "loud",
                                  "quick", "slow", "expressive", "flat"}


def test_delivery_counts() -> None:
    rows = [{"tags_source": "measured", "tags": ["shouting", "quick"], "control": "shouting, fast pace"},
            {"tags_source": "agent", "tags": ["shouting"], "control": "shouting"},
            {"tags_source": "agent", "tags": [], "control": None},
            {"tags_source": "dropped", "tags": ["calm"], "control": None},
            {"tags_source": "none", "tags": [], "control": None},
            {"status": "ok"}]
    assert pt.delivery_counts(rows) == {"by_source": {"measured": 1, "agent": 2, "dropped": 1, "none": 1},
                                        "tags": {"shouting": 2, "quick": 1}}
    assert pt.delivery_counts([{"status": "ok"}]) is None
