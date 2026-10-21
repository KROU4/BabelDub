from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from babeldub.qa.metrics import (
    audio_levels,
    cer,
    cosine,
    duration_ratio,
    identity_margin,
    levenshtein,
    max_ratio_for,
    mean_pairwise_cosine,
    normalize_text,
)


def _unit(*xs: float) -> np.ndarray:
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_identity_margin() -> None:
    hero, villain = _unit(1, 0, 0), _unit(0, 1, 0)
    cents = {"hero": hero, "villain": villain}
    margin, closest = identity_margin(_unit(0.8, 0.2, 0), "hero", cents, 0.75)
    assert closest == "hero" and margin == pytest.approx(cosine(_unit(0.8, 0.2, 0), hero) - cosine(_unit(0.8, 0.2, 0), villain))
    margin, closest = identity_margin(_unit(0.3, 0.9, 0), "hero", cents, 0.75)
    assert closest == "villain" and margin < -0.5
    # one centroid only, or the line's own character has none: no margin
    assert identity_margin(hero, "hero", {"hero": hero}, 0.75) == (None, None)
    assert identity_margin(hero, "ghost", cents, 0.75) == (None, "hero")


def test_identity_margin_same_voice_is_not_another_character() -> None:
    hero = _unit(1, 0, 0)
    cluster = _unit(1, 0.45, 0)  # a merged voice cluster of the hero: centroid cosine ~0.91
    villain = _unit(0, 0, 1)
    dub = cluster  # the dub happens to sit right on the cluster's centroid
    margin, closest = identity_margin(dub, "hero", {"hero": hero, "cluster_04": cluster, "villain": villain}, 0.75)
    assert closest == "cluster_04"
    assert margin == pytest.approx(cosine(dub, hero) - cosine(dub, villain)) and margin > 0.8
    # with the same-voice rule off, the hero's own cluster would fail him
    margin, _ = identity_margin(dub, "hero", {"hero": hero, "cluster_04": cluster, "villain": villain}, 1.01)
    assert margin < -0.05
    # every other centroid is the same voice: nothing to tell apart
    assert identity_margin(dub, "hero", {"hero": hero, "cluster_04": cluster}, 0.75) == (None, "cluster_04")


def _ref_lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def test_normalize_text() -> None:
    assert normalize_text("Ёлка, ЁЖ!  Привет…") == "елка еж привет"
    assert normalize_text("Let's go -- now?!") == "lets go now"
    assert normalize_text("{ColorStart}Zbyszek:{ColorEnd} <b>Hi</b> [laughs] there") == "zbyszek hi there"
    assert normalize_text("В 2026 году 3,5 %") == "в 2026 году 3 5"
    assert normalize_text("по-моему") == normalize_text("по моему")
    assert normalize_text(None) == ""


@pytest.mark.parametrize("seed", range(6))
def test_levenshtein_matches_reference(seed: int) -> None:
    rng = np.random.default_rng(seed)
    alphabet = list("абвгд ")
    for _ in range(40):
        a = "".join(rng.choice(alphabet, size=int(rng.integers(0, 15))))
        b = "".join(rng.choice(alphabet, size=int(rng.integers(0, 15))))
        assert levenshtein(a, b) == _ref_lev(a, b)


def test_levenshtein_basics() -> None:
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert levenshtein("abc", "") == 3
    assert levenshtein("same", "same") == 0


def test_cer() -> None:
    assert cer("Привет, мир!", "привет мир") == 0.0
    assert cer("Ёжик в тумане.", "ежик в тумане") == 0.0
    assert cer("абвгд", "абвгж") == pytest.approx(0.2)
    assert cer("", "") == 0.0
    assert cer("", "лишнее") == 1.0
    assert cer("текст", "") == 1.0
    assert cer("да", "да да да да") > 1.0  # hallucinated repetitions are not capped


def test_duration_ratio_and_limit() -> None:
    assert duration_ratio(3.0, 2.0) == pytest.approx(1.5)
    assert duration_ratio(3.0, 0) is None
    assert duration_ratio(None, 2.0) is None
    assert max_ratio_for(False, 1.35, 0.03) == 1.35
    assert max_ratio_for(True, 1.35, 0.03) == pytest.approx(1.03)


def test_audio_levels(tmp_path: Path) -> None:
    sr = 16000
    t = np.arange(sr) / sr
    sf.write(str(tmp_path / "half.wav"), (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), sr)
    lv = audio_levels(tmp_path / "half.wav")
    assert lv.dur_s == pytest.approx(1.0)
    assert lv.peak_db == pytest.approx(-6.02, abs=0.05)
    assert lv.rms_db == pytest.approx(-9.03, abs=0.1)
    assert lv.clip_frac == 0.0
    sf.write(str(tmp_path / "silent.wav"), np.zeros(sr, np.float32), sr)
    assert audio_levels(tmp_path / "silent.wav").active_rms_db <= -100
    sf.write(str(tmp_path / "clip.wav"), np.clip(2 * np.sin(2 * np.pi * 440 * t), -1, 1).astype(np.float32), sr)
    assert audio_levels(tmp_path / "clip.wav").clip_frac > 0.1
    sf.write(str(tmp_path / "stereo.wav"), np.stack([0.1 * np.sin(2 * np.pi * 300 * t)] * 2, axis=1).astype(np.float32), sr)
    assert audio_levels(tmp_path / "stereo.wav").peak_db == pytest.approx(-20.0, abs=0.1)


def test_cosines() -> None:
    assert cosine([1, 0], [2, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 3]) == pytest.approx(0.0)
    assert mean_pairwise_cosine([np.array([1.0, 0.0])]) is None
    vals = [np.array([1.0, 0.0]), np.array([1.0, 0.0]), np.array([0.0, 1.0])]
    assert mean_pairwise_cosine(vals) == pytest.approx(1.0 / 3.0)
