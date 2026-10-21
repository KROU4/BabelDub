from __future__ import annotations

import struct

import numpy as np
import pytest
from fmod_synth import SubSpec, make_fsb, tone

from babeldub.adapters.fmod import fsb5 as F


def _subs() -> list[SubSpec]:
    return [
        SubSpec("dlg_a1_m01_d00_Hero_01", 48000, tone(4800, 48000, 1)),
        SubSpec("sfx_door", 44100, tone(1000, 44100, 2, 220.0), [(F.CHUNK_LOOP, struct.pack("<II", 0, 999))]),
        SubSpec("odd_rate", 22000, tone(333, 22000, 1, 1000.0)),  # needs a FREQUENCY chunk
        SubSpec("three_channels", 24000, tone(256, 24000, 3)),  # needs a CHANNELS chunk
        SubSpec("dlg_a1_m01_d00_Hero_01", 48000, tone(10, 48000, 1)),  # duplicate name, tiny
    ]


@pytest.mark.parametrize("codec", [F.CODEC_PCM16, F.CODEC_FADPCM])
def test_write_parse_round_trip(codec: int) -> None:
    subs = _subs()
    blob = make_fsb(codec, subs)
    fsb = F.parse_fsb5(blob)
    assert fsb.codec == codec and fsb.version == 1
    assert fsb.total_size == len(blob)
    assert fsb.data_start % 32 == 0
    assert struct.unpack_from("<I", blob, 0x1C)[0] == 1  # header tail kept
    assert [s.name for s in fsb.subsounds] == [s.name for s in subs]
    for spec, sub in zip(subs, fsb.subsounds, strict=True):
        assert (sub.rate, sub.channels, sub.nsamples) == (spec.rate, spec.pcm.shape[1], spec.pcm.shape[0])
        assert sub.data_offset % 32 == 0
        data = blob[fsb.data_start + sub.data_offset : fsb.data_start + sub.data_offset + sub.data_size]
        dec = F.decode_native(fsb, sub, data)
        assert dec.shape == spec.pcm.shape
        if codec == F.CODEC_PCM16:
            assert np.array_equal(dec, spec.pcm)
    assert fsb.subsounds[1].chunk(F.CHUNK_LOOP) == struct.pack("<II", 0, 999)
    assert fsb.subsounds[2].chunk(F.CHUNK_FREQUENCY) == struct.pack("<I", 22000)
    assert fsb.subsounds[3].chunk(F.CHUNK_CHANNELS) == bytes([3])


def test_verbatim_rebuild_is_byte_identical() -> None:
    blob = make_fsb(F.CODEC_FADPCM, _subs())
    assert F.rebuild_verbatim(blob) == blob
    padded = b"\xaa" * 64 + blob
    assert F.rebuild_verbatim(padded, 64) == blob


def test_fsb_without_names() -> None:
    blob = make_fsb(F.CODEC_PCM16, _subs()[:2], names=False)
    fsb = F.parse_fsb5(blob)
    assert not fsb.has_names and fsb.subsounds[0].name is None
    assert F.rebuild_verbatim(blob) == blob


def test_native_decoders_for_other_pcm_formats() -> None:
    pcm = np.array([[0], [16384], [-32768], [32767]], dtype=np.int16)
    sub = F.Subsound(0, "x", 48000, 1, 4, 0, 32)
    head = F.MAGIC + struct.pack("<6I", 1, 1, 8, 0, 32, 0) + bytes(0x3C - 28)
    for codec, data in (
        (F.CODEC_PCM8, (pcm[:, 0] >> 8).astype(np.int8).tobytes()),
        (F.CODEC_PCMFLOAT, (pcm[:, 0] / 32768.0).astype("<f4").tobytes()),
        (F.CODEC_PCM32, (pcm[:, 0].astype(np.int32) << 16).astype("<i4").tobytes()),
    ):
        fsb = F.Fsb5(1, codec, head, 8, 0, 32, [sub])
        out = F.decode_native(fsb, sub, data)
        assert np.array_equal(out >> 8, pcm >> 8)


def test_big_endian_pcm16_flag() -> None:
    blob = bytearray(make_fsb(F.CODEC_PCM16, [SubSpec("x", 48000, tone(64, 48000))]))
    fsb = F.parse_fsb5(blob)
    sub = fsb.subsounds[0]
    start = fsb.data_start + sub.data_offset
    le = np.frombuffer(bytes(blob[start : start + 128]), "<i2").copy()
    blob[start : start + 128] = le.astype(">i2").tobytes()
    struct.pack_into("<I", blob, 0x20, 1)
    fsb_be = F.parse_fsb5(blob)
    assert fsb_be.big_endian_pcm
    out = F.decode_native(fsb_be, fsb_be.subsounds[0], bytes(blob[start : start + 128]))
    assert np.array_equal(out[:, 0], le)
    # writing PCM16 clears the big-endian flag
    w = F.Fsb5Writer(F.CODEC_PCM16, fsb_be.header)
    w.add(F.NewSubsound("x", 48000, 1, 64, le.astype("<i2").tobytes()))
    assert struct.unpack_from("<I", w.to_bytes(), 0x20)[0] == 0


def test_parse_errors() -> None:
    with pytest.raises(F.Fsb5Error):
        F.parse_fsb5(b"FSB4" + bytes(80))
    blob = make_fsb(F.CODEC_PCM16, _subs()[:1])
    with pytest.raises(F.Fsb5Error):
        F.parse_fsb5(blob[:70])
    with pytest.raises(F.Fsb5Error):
        F.encode_payload(F.CODEC_VORBIS, tone(10, 48000))


def test_payload_size_and_codec_names() -> None:
    assert F.payload_size(F.CODEC_PCM16, 2, 100) == 400
    assert F.payload_size(F.CODEC_FADPCM, 1, 257) == 2 * 0x8C
    assert F.payload_size(F.CODEC_VORBIS, 1, 100) is None
    assert F.codec_name(15) == "vorbis" and F.codec_name(99) == "codec99"
