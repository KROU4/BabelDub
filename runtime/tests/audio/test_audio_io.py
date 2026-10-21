"""WAV I/O round trips."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from babeldub.audio.io import probe, read_audio, write_wav


def test_write_read_probe_roundtrip(tmp_path: Path) -> None:
    sr = 22050
    data = np.stack([np.linspace(-0.5, 0.5, sr), np.linspace(0.5, -0.5, sr)], axis=1).astype(np.float32)
    out = write_wav(tmp_path / "sub" / "x.wav", data, sr)
    assert out.exists()
    info = probe(out)
    assert info["sr"] == sr and info["ch"] == 2 and info["frames"] == sr
    assert info["dur_s"] == pytest.approx(1.0)
    back, sr2 = read_audio(out)
    assert sr2 == sr and back.shape == (sr, 2) and back.dtype == np.float32
    assert np.max(np.abs(back - data)) < 1e-3


def test_mono_is_2d_on_read_and_pcm_is_clipped(tmp_path: Path) -> None:
    out = write_wav(tmp_path / "m.wav", np.array([0.0, 2.0, -2.0], dtype=np.float32), 8000)
    back, _ = read_audio(out)
    assert back.shape == (3, 1)
    assert back[1, 0] == pytest.approx(1.0, abs=1e-3) and back[2, 0] == pytest.approx(-1.0, abs=1e-3)
    flt = write_wav(tmp_path / "f.wav", np.array([0.25, 1.5], dtype=np.float32), 8000, subtype="FLOAT")
    back2, _ = read_audio(flt)
    assert back2[1, 0] == pytest.approx(1.5)
    assert not list(tmp_path.glob("*.tmp.wav"))
