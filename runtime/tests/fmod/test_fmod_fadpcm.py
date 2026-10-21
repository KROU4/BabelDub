from __future__ import annotations

import struct

import numpy as np
import pytest

from babeldub.adapters.fmod import fadpcm

_REF_COEFS = [(0, 0), (60, 0), (122, 60), (115, 52), (98, 55), (0, 0), (0, 0), (0, 0)]


def reference_decode_channel(data: bytes) -> list[int]:
    """Straight port of vgmstream decode_fadpcm (sample by sample) used as the oracle."""
    out: list[int] = []
    for f in range(len(data) // fadpcm.FRAME_BYTES):
        fr = data[f * fadpcm.FRAME_BYTES : (f + 1) * fadpcm.FRAME_BYTES]
        coefs, shifts, h1, h2 = struct.unpack_from("<IIhh", fr, 0)
        for i in range(8):
            c1, c2 = _REF_COEFS[((coefs >> (i * 4)) & 0xF) % 7]
            shift = 22 - ((shifts >> (i * 4)) & 0xF)
            for j in range(4):
                (word,) = struct.unpack_from("<I", fr, 0x0C + 0x10 * i + 4 * j)
                for k in range(8):
                    v = ((word >> (k * 4)) & 0xF) << 28
                    if v & 0x80000000:
                        v -= 1 << 32
                    s = ((v >> shift) - h2 * c2 + h1 * c1) >> 6
                    s = max(-32768, min(32767, s))
                    out.append(s)
                    h2, h1 = h1, s
    return out


def test_decoder_matches_reference_on_random_frames() -> None:
    rng = np.random.default_rng(3)
    data = rng.integers(0, 256, size=(12, fadpcm.FRAME_BYTES), dtype=np.uint8).tobytes()
    ours = fadpcm.decode(data, 1)[:, 0].tolist()
    assert ours == reference_decode_channel(data)


def test_round_trip_tone_quality_and_size() -> None:
    sr = 48000
    t = np.arange(10_000) / sr
    x = np.round(0.4 * 32767 * np.sin(2 * np.pi * 330 * t)).astype(np.int16)
    enc = fadpcm.encode(x)
    assert len(enc) == fadpcm.encoded_size(len(x), 1) == fadpcm.frames_for(len(x)) * fadpcm.FRAME_BYTES
    dec = fadpcm.decode(enc, 1, len(x))[:, 0].astype(np.float64)
    snr = 10 * np.log10(np.sum(x.astype(np.float64) ** 2) / np.sum((x - dec) ** 2))
    assert snr > 30
    # the encoder simulates the exact decoder: the reference decoder agrees bit for bit
    assert reference_decode_channel(enc)[: len(x)] == dec.astype(int).tolist()


def test_silence_is_exact_and_empty_input() -> None:
    sil = np.zeros(700, dtype=np.int16)
    assert np.array_equal(fadpcm.decode(fadpcm.encode(sil), 1, 700)[:, 0], sil)
    assert fadpcm.encode(np.zeros((0, 2), dtype=np.int16)) == b""


def test_frame_history_is_previous_source_samples() -> None:
    x = (np.arange(600) * 37 % 2000 - 1000).astype(np.int16)
    enc = fadpcm.encode(x)
    h1, h2 = struct.unpack_from("<hh", enc, fadpcm.FRAME_BYTES + 8)
    assert (h1, h2) == (int(x[255]), int(x[254]))
    assert struct.unpack_from("<hh", enc, 8) == (0, 0)


def test_stereo_frames_are_interleaved_per_channel() -> None:
    rng = np.random.default_rng(5)
    st = rng.integers(-8000, 8000, size=(1000, 2)).astype(np.int16)
    enc = fadpcm.encode(st)
    left = fadpcm.encode(st[:, 0])
    right = fadpcm.encode(st[:, 1])
    frames = np.frombuffer(enc, np.uint8).reshape(-1, 2, fadpcm.FRAME_BYTES)
    assert frames[:, 0].tobytes() == left
    assert frames[:, 1].tobytes() == right
    dec = fadpcm.decode(enc, 2, 1000)
    assert dec.shape == (1000, 2)


def test_encode_many_matches_single_calls() -> None:
    rng = np.random.default_rng(9)
    items = [
        rng.integers(-3000, 3000, size=(n, ch)).astype(np.int16)
        for n, ch in ((300, 1), (1, 2), (2000, 1), (0, 1))
    ]
    many = fadpcm.encode_many(items)
    assert many == [fadpcm.encode(a) for a in items]


def test_encoder_rejects_non_int16() -> None:
    with pytest.raises(ValueError):
        fadpcm.encode(np.zeros(10, dtype=np.float32))
