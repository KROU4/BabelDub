"""FMOD FADPCM (FSB5 codec 16): vectorized decoder and encoder.

Frame layout (0x8C bytes per channel, 256 samples), as in vgmstream src/coding/fadpcm_decoder.c,
which is byte-accurate against FMOD:
  u32 coefs   8 nibbles: coefficient-pair index per 32-sample group (index % 7, pairs 5..6 are zero)
  u32 shifts  8 nibbles: scale per group (decoder shift = 22 - nibble)
  s16 hist1, s16 hist2   decoder history at the frame start
  8 groups x 4 u32 words x 8 nibbles; nibble k of word j is sample 8*j + k of the group
Multichannel data interleaves whole frames: f0c0, f0c1, f1c0, f1c1, ...

Decoding a sample: ((nib << 28) >> (22 - shift) - hist2*c2 + hist1*c1) >> 6, clamped to int16;
(nib << 28) >> (22 - s) equals the sign-extended nibble shifted left by 6 + s.

Every frame carries its own history, so frames are decoded independently (that is what makes
vectorizing over frames exact). The encoder writes the preceding *source* samples as the frame
history, so frames are also encoded independently; within a frame it picks, for each 32-sample
group, the coefficient pair and shift with the smallest squared error of the exact decoder
simulation (candidates: 5 pairs x 3 shifts around the open-loop estimate).
"""

from __future__ import annotations

import numpy as np

FRAME_BYTES = 0x8C
SAMPLES_PER_FRAME = 256
GROUPS = 8
GROUP_SAMPLES = 32

# coefficient pairs (c1, c2); the decoder takes index % 7 and pairs 5..6 are zero
_COEFS = np.array([[0, 0], [60, 0], [122, 60], [115, 52], [98, 55], [0, 0], [0, 0]], dtype=np.int32)
_ENC_PAIRS = 5
_ENC_SHIFT_DELTAS = np.array([-1, 0, 1], dtype=np.int32)
_MAX_SHIFT = 12  # 8 << (6 + 12) / 64 covers the whole int16 range
_BATCH = 4096  # frames per vectorized encoder/decoder pass


def frames_for(nsamples: int) -> int:
    return (max(0, nsamples) + SAMPLES_PER_FRAME - 1) // SAMPLES_PER_FRAME


def encoded_size(nsamples: int, channels: int) -> int:
    return frames_for(nsamples) * FRAME_BYTES * channels


# ----------------------------------------------------------------------------- decoder
def _decode_frames(frames: np.ndarray) -> np.ndarray:
    """frames: (n, 0x8C) uint8 -> (n, 256) int16."""
    n = frames.shape[0]
    head = np.ascontiguousarray(frames[:, :8]).view("<u4").astype(np.int64)  # (n, 2)
    hist = np.ascontiguousarray(frames[:, 8:12]).view("<i2").astype(np.int32)  # (n, 2)
    words = np.ascontiguousarray(frames[:, 12:]).view("<u4")  # (n, 32)
    nib = ((words[:, :, None] >> (np.arange(8, dtype=np.uint32) * 4)) & 0xF).astype(np.int32)
    nib = nib.reshape(n, SAMPLES_PER_FRAME)
    nib = (nib ^ 8) - 8  # sign-extend 4 bits
    gshift = np.arange(GROUPS, dtype=np.int64) * 4
    cidx = ((head[:, 0:1] >> gshift) & 0xF) % 7  # (n, 8)
    sfield = ((head[:, 1:2] >> gshift) & 0xF).astype(np.int32)  # (n, 8)
    c1 = _COEFS[cidx, 0]
    c2 = _COEFS[cidx, 1]
    scaled = nib << (6 + np.repeat(sfield, GROUP_SAMPLES, axis=1))  # (n, 256)
    out = np.empty((n, SAMPLES_PER_FRAME), dtype=np.int32)
    h1 = hist[:, 0].copy()
    h2 = hist[:, 1].copy()
    for k in range(SAMPLES_PER_FRAME):
        g = k // GROUP_SAMPLES
        s = (scaled[:, k] - h2 * c2[:, g] + h1 * c1[:, g]) >> 6
        np.clip(s, -32768, 32767, out=s)
        out[:, k] = s
        h2 = h1
        h1 = s
    return out.astype(np.int16)


def decode(data: bytes | memoryview, channels: int, nsamples: int | None = None) -> np.ndarray:
    """Decode FADPCM bytes to int16 samples of shape (nsamples, channels)."""
    if channels < 1:
        raise ValueError("channels must be >= 1")
    stride = FRAME_BYTES * channels
    nframes = len(data) // stride
    if nsamples is not None:
        nframes = min(nframes, frames_for(nsamples))
    raw = np.frombuffer(data, dtype=np.uint8, count=nframes * stride).reshape(nframes, channels, FRAME_BYTES)
    out = np.empty((nframes * SAMPLES_PER_FRAME, channels), dtype=np.int16)
    for c in range(channels):
        for start in range(0, nframes, _BATCH):
            stop = min(nframes, start + _BATCH)
            dec = _decode_frames(raw[start:stop, c, :])
            out[start * SAMPLES_PER_FRAME : stop * SAMPLES_PER_FRAME, c] = dec.reshape(-1)
    if nsamples is not None:
        out = out[:nsamples]
    return out


# ----------------------------------------------------------------------------- encoder
def _estimate_shifts(target: np.ndarray, h1: np.ndarray, h2: np.ndarray) -> np.ndarray:
    """Open-loop shift estimate per coefficient pair: (B, 5) int32 in [0, _MAX_SHIFT]."""
    prev = np.concatenate([h2[:, None], h1[:, None], target], axis=1).astype(np.int64)
    xm1 = prev[:, 1:-1]
    xm2 = prev[:, :-2]
    t64 = target.astype(np.int64) * 64
    est = np.empty((target.shape[0], _ENC_PAIRS), dtype=np.int32)
    for c in range(_ENC_PAIRS):
        c1, c2 = int(_COEFS[c, 0]), int(_COEFS[c, 1])
        resid = np.abs(t64 - (xm1 * c1 - xm2 * c2)).max(axis=1)
        # smallest s with resid <= 7.5 * 64 * 2**s
        ratio = np.maximum(resid, 1) / 480.0
        est[:, c] = np.clip(np.ceil(np.log2(ratio)), 0, _MAX_SHIFT).astype(np.int32)
    return est


def _encode_frames(x: np.ndarray, h1_0: np.ndarray, h2_0: np.ndarray) -> bytes:
    """x: (B, 256) int32 source frames; h1_0/h2_0: (B,) frame history -> B encoded frames."""
    nb = x.shape[0]
    rows = np.arange(nb)
    cand_pair = np.repeat(np.arange(_ENC_PAIRS, dtype=np.int32), len(_ENC_SHIFT_DELTAS))  # (C,)
    cand_delta = np.tile(_ENC_SHIFT_DELTAS, _ENC_PAIRS)  # (C,)
    ncand = cand_pair.size
    c1 = _COEFS[cand_pair, 0][None, :]
    c2 = _COEFS[cand_pair, 1][None, :]
    coefs_word = np.zeros(nb, dtype=np.uint32)
    shifts_word = np.zeros(nb, dtype=np.uint32)
    nibbles = np.empty((nb, SAMPLES_PER_FRAME), dtype=np.int32)
    H1 = h1_0.astype(np.int32)
    H2 = h2_0.astype(np.int32)
    nibs = np.empty((GROUP_SAMPLES, nb, ncand), dtype=np.int32)
    err = np.empty((nb, ncand), dtype=np.float32)
    pred = np.empty((nb, ncand), dtype=np.int32)
    tmp = np.empty((nb, ncand), dtype=np.int32)
    for g in range(GROUPS):
        target = x[:, g * GROUP_SAMPLES : (g + 1) * GROUP_SAMPLES]
        t64 = target * 64
        est = _estimate_shifts(target, H1, H2)
        shift = np.clip(est[:, cand_pair] + cand_delta[None, :], 0, _MAX_SHIFT).astype(np.int32)  # (B, C)
        sh6 = shift + 6
        half = (np.int32(1) << (sh6 - 1)).astype(np.int32)
        h1 = np.repeat(H1[:, None], ncand, axis=1)
        h2 = np.repeat(H2[:, None], ncand, axis=1)
        err.fill(0)
        for k in range(GROUP_SAMPLES):
            # exact decoder simulation for every candidate: in-place ops keep this loop fast
            np.multiply(h1, c1, out=pred)
            np.multiply(h2, c2, out=tmp)
            pred -= tmp
            nib = nibs[k]
            np.subtract(t64[:, k : k + 1], pred, out=nib)
            nib += half
            nib >>= sh6
            np.minimum(nib, 7, out=nib)
            np.maximum(nib, -8, out=nib)
            s = np.left_shift(nib, sh6)
            s += pred
            s >>= 6
            np.minimum(s, 32767, out=s)
            np.maximum(s, -32768, out=s)
            np.subtract(s, target[:, k : k + 1], out=tmp)
            sq = tmp.astype(np.float32)
            sq *= sq
            err += sq
            h2 = h1
            h1 = s
        best = np.argmin(err, axis=1)
        H1 = h1[rows, best]
        H2 = h2[rows, best]
        nibbles[:, g * GROUP_SAMPLES : (g + 1) * GROUP_SAMPLES] = nibs[:, rows, best].T
        coefs_word |= cand_pair[best].astype(np.uint32) << np.uint32(4 * g)
        shifts_word |= shift[rows, best].astype(np.uint32) << np.uint32(4 * g)
    # pack: 32 words of 8 nibbles
    packed = (nibbles.astype(np.uint32) & 0xF).reshape(nb, 32, 8)
    words = np.zeros((nb, 32), dtype=np.uint32)
    for k in range(8):
        words |= packed[:, :, k] << np.uint32(4 * k)
    out = np.empty((nb, FRAME_BYTES), dtype=np.uint8)
    out[:, 0:4] = coefs_word.astype("<u4")[:, None].view(np.uint8)
    out[:, 4:8] = shifts_word.astype("<u4")[:, None].view(np.uint8)
    hist = np.stack([h1_0, h2_0], axis=1).astype("<i2")
    out[:, 8:12] = hist.view(np.uint8)
    out[:, 12:] = words.astype("<u4").view(np.uint8)
    return out.tobytes()


def _as_int16_2d(pcm: np.ndarray) -> np.ndarray:
    arr = np.asarray(pcm)
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.ndim != 2 or arr.dtype != np.int16:
        raise ValueError("FADPCM encoder expects int16 samples shaped (nsamples, channels)")
    return arr


def _channel_frames(signal: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One channel -> (frames (n, 256) int32, hist1 (n,), hist2 (n,)) with source-sample history."""
    nframes = frames_for(signal.size)
    flat = np.zeros(nframes * SAMPLES_PER_FRAME, dtype=np.int32)
    flat[: signal.size] = signal
    starts = np.arange(nframes) * SAMPLES_PER_FRAME
    h1 = np.where(starts >= 1, flat[np.maximum(starts - 1, 0)], 0).astype(np.int32)
    h2 = np.where(starts >= 2, flat[np.maximum(starts - 2, 0)], 0).astype(np.int32)
    return flat.reshape(nframes, SAMPLES_PER_FRAME), h1, h2


def encode_many(items: list[np.ndarray]) -> list[bytes]:
    """Encode several int16 (nsamples, channels) arrays at once (frames of all of them share batches)."""
    arrays = [_as_int16_2d(a) for a in items]
    frames: list[np.ndarray] = []
    hist1: list[np.ndarray] = []
    hist2: list[np.ndarray] = []
    layout: list[tuple[int, int]] = []  # (nframes, channels) per item
    for arr in arrays:
        nframes = frames_for(arr.shape[0])
        layout.append((nframes, arr.shape[1]))
        for c in range(arr.shape[1]):
            fr, h1, h2 = _channel_frames(arr[:, c])
            frames.append(fr)
            hist1.append(h1)
            hist2.append(h2)
    total = sum(f.shape[0] for f in frames)
    if total == 0:
        return [b"" for _ in arrays]
    all_frames = np.concatenate(frames)
    all_h1 = np.concatenate(hist1)
    all_h2 = np.concatenate(hist2)
    encoded = bytearray()
    for start in range(0, total, _BATCH):
        stop = min(total, start + _BATCH)
        encoded += _encode_frames(all_frames[start:stop], all_h1[start:stop], all_h2[start:stop])
    rows = np.frombuffer(bytes(encoded), dtype=np.uint8).reshape(total, FRAME_BYTES)
    out: list[bytes] = []
    pos = 0
    for nframes, channels in layout:
        per_channel = []
        for _ in range(channels):
            per_channel.append(rows[pos : pos + nframes])
            pos += nframes
        out.append(np.stack(per_channel, axis=1).tobytes() if nframes else b"")
    return out


def encode(pcm: np.ndarray) -> bytes:
    """Encode int16 samples of shape (nsamples, channels) (or 1-D mono) to FADPCM bytes."""
    return encode_many([pcm])[0]
