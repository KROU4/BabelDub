"""Bootstrap voice prompts (tts.ref_strategy=bootstrap; its long references also serve ref_strategy=tags,
the default for VoxCPM2).

1. Long timbre references: 3-4 clean original lines of the character (4-7.5 s each, no flags, the
   cast references first) joined with 0.35 s gaps, ~15-20 s. It is the `reference_wav_path` of every
   line: the timbre comes from the original actor. One reference per arousal bucket (calm, animated,
   loud, soft; cast/refs.py) that has at least BUCKET_MIN_S of clean material; the calm one always
   exists and stands in for a bucket without its own.
2. Target-language prompt bank: once at least `min_accepted` lines of the character were accepted
   by QA, each bucket gets a 5-10 s continuation prompt in the target language made of 1-2 of its
   accepted dubs (final audio, after `vc` when the character has an RVC model, with the exact text
   they were synthesized from). Dubs are ranked by MOS and by how well their prosody follows their
   original (qa.jsonl `mos`, `prosody_sim`); CER is only a gate (<= qa.max_cer). A bucket without
   accepted dubs uses the nearest bucket's prompt. A bucket's prompt is frozen once built
   (synth/prompts/<char_id>/tgt.json `bank`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from babeldub.audio.dsp import resample, to_mono
from babeldub.audio.fit import speech_bounds
from babeldub.audio.io import read_audio, write_wav
from babeldub.cast.refs import BUCKETS
from babeldub.core import layout
from babeldub.core.jsonl import now_iso, read_json, write_json

GAP_S = 0.35
EDGE_S = 0.05  # silence kept around each part
LONG_REF_RANGE = (4.0, 7.5)
LONG_REF_RELAXED = (2.0, 12.0)
LONG_REF_TARGET_S = 15.0
LONG_REF_MAX_S = 20.0
BUCKET_MIN_S = 8.0  # clean material a non-calm bucket needs for its own long reference
MAX_PARTS = 4
PHASE1_RANGE = (2.5, 8.0)  # first lines of a character: plain lines that make good prompt pieces
BANK_PIECE_S = (2.5, 10.0)  # accepted dubs usable as prompt pieces
BANK_TARGET_S = (5.0, 10.0)  # one bucket prompt: 1-2 pieces within this length
MOS_RANGE = (1.0, 5.0)


@dataclass
class Prompt:
    wav: Path
    text: str  # exact transcript of `wav` ("" for a timbre reference)
    ref_id: str
    dur_s: float
    lines: list[str]


def _sha(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _abs(root: Path, rel: str | None) -> Path | None:
    if not rel:
        return None
    path = Path(rel)
    return path if path.is_absolute() else root / path


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _speech_part(path: Path, sr_out: int | None) -> tuple[np.ndarray, int]:
    data, sr = read_audio(path)
    mono = to_mono(data)
    bounds = speech_bounds(mono, sr)
    if bounds is not None:
        pad = int(EDGE_S * sr)
        mono = mono[max(0, bounds[0] - pad) : min(len(mono), bounds[1] + pad)]
    if sr_out is not None and sr != sr_out:
        mono = resample(mono, sr, sr_out)
        sr = sr_out
    return mono, sr


def _join(paths: list[Path], out: Path) -> float:
    parts: list[np.ndarray] = []
    sr: int | None = None
    for path in paths:
        mono, sr = _speech_part(path, sr)
        parts += [mono, np.zeros(int(GAP_S * sr), dtype=np.float32)]
    assert sr is not None
    audio = np.concatenate(parts[:-1]) if parts else np.zeros(0, dtype=np.float32)
    write_wav(out, audio, sr)
    return len(audio) / sr


def _clean(line: dict[str, Any]) -> bool:
    return not (line.get("flags") or []) and bool((line.get("audio") or {}).get("wav"))


def long_reference(root: Path, char_id: str, lines: Iterable[dict[str, Any]], refs: list[dict[str, Any]],
                   bucket: str | None = None) -> Prompt | None:
    """The character's ~15-20 s timbre reference (cached under synth/prompts/<char_id>/); `bucket`
    names a non-calm reference (file src-<bucket>-<key>.wav, ref_id long:<char>:<bucket>:<key>)."""
    pool = [ln for ln in lines if _clean(ln)]
    ref_order = {str(r.get("line_id")): n for n, r in enumerate(refs)}
    picked: list[dict[str, Any]] = []
    for lo, hi in (LONG_REF_RANGE, LONG_REF_RELAXED):
        cands = [ln for ln in pool if lo <= _num((ln.get("audio") or {}).get("dur_s")) <= hi
                 and (p := _abs(root, ln["audio"]["wav"])) is not None and p.exists()]
        cands.sort(key=lambda ln: (ref_order.get(ln["id"], len(ref_order)),
                                   abs(_num(ln["audio"]["dur_s"]) - 6.0), str(ln.get("uid") or ln["id"])))
        total = 0.0
        picked = []
        for ln in cands:
            dur = _num(ln["audio"]["dur_s"])
            if total + dur > LONG_REF_MAX_S:
                continue
            picked.append(ln)
            total += dur + GAP_S
            if total >= LONG_REF_TARGET_S or len(picked) >= MAX_PARTS:
                break
        if picked:
            break
    if not picked:
        return None
    paths = [_abs(root, ln["audio"]["wav"]) for ln in picked]
    key = _sha([(str(p), p.stat().st_size) for p in paths if p is not None])[:12]
    tag = f"{bucket}-" if bucket and bucket != "calm" else ""
    out = root / layout.SYNTH_PROMPTS_DIR / char_id / f"src-{tag}{key}.wav"
    if not out.exists():
        dur = _join([p for p in paths if p is not None], out)
    else:
        data, sr = read_audio(out)
        dur = data.shape[0] / sr
    ref_id = f"long:{char_id}:{bucket}:{key}" if tag else f"long:{char_id}:{key}"
    return Prompt(out, "", ref_id, round(dur, 2), [ln["id"] for ln in picked])


def long_references(root: Path, char_id: str, lines: list[dict[str, Any]], refs: list[dict[str, Any]],
                    banks: dict[str, list[str]] | None = None) -> dict[str, Prompt]:
    """{bucket: long reference}. Calm is built from the calm lines (all lines when they are too few);
    another bucket only with BUCKET_MIN_S of clean material in 2-12 s lines."""
    banks = banks or {}
    by_id = {ln["id"]: ln for ln in lines}
    out: dict[str, Prompt] = {}
    calm = [by_id[i] for i in banks.get("calm", []) if i in by_id]
    ref = long_reference(root, char_id, calm, refs) if calm else None
    ref = ref or long_reference(root, char_id, lines, refs)
    if ref is None:
        return out
    out["calm"] = ref
    lo, hi = LONG_REF_RELAXED
    for bucket in BUCKETS[1:]:
        pool = [by_id[i] for i in banks.get(bucket, []) if i in by_id and _clean(by_id[i])]
        material = sum(d for ln in pool if lo <= (d := _num((ln.get("audio") or {}).get("dur_s"))) <= hi)
        if material >= BUCKET_MIN_S and (prompt := long_reference(root, char_id, pool, refs, bucket)) is not None:
            out[bucket] = prompt
    return out


def phase1_ids(lines: list[dict[str, Any]], k: int) -> list[str]:
    """The character's first `k` lines dubbed with the original reference only: plain lines with text
    of 2.5-8 s first (they become the pieces of the target-language prompt), in game order."""
    def order(ln: dict[str, Any]) -> tuple[Any, ...]:
        return (str(ln.get("scene") or ""), int(ln.get("order") or 0), ln["id"])

    lo, hi = PHASE1_RANGE
    good = sorted((ln for ln in lines if _clean(ln) and lo <= _num((ln.get("audio") or {}).get("dur_s")) <= hi
                   and ((ln.get("text") or {}).get("src"))), key=order)
    good_ids = {ln["id"] for ln in good}
    rest = sorted((ln for ln in lines if ln["id"] not in good_ids), key=order)
    return [ln["id"] for ln in (good + rest)[: max(0, k)]]


def piece_score(qa_row: dict[str, Any]) -> float:
    """0..1 rank of an accepted dub as a prompt piece: half MOS, half prosody similarity to its original
    (a missing metric counts as 0.5)."""
    mos = qa_row.get("mos")
    lo, hi = MOS_RANGE
    mos_n = min(max((float(mos) - lo) / (hi - lo), 0.0), 1.0) if isinstance(mos, (int, float)) else 0.5
    pros = qa_row.get("prosody_sim")
    pros_n = min(max(float(pros), 0.0), 1.0) if isinstance(pros, (int, float)) else 0.5
    return 0.5 * mos_n + 0.5 * pros_n


def _load_bank(folder: Path) -> dict[str, Prompt]:
    meta = read_json(folder / "tgt.json", default=None) or {}
    entries = meta.get("bank") if isinstance(meta.get("bank"), dict) else ({"calm": meta} if meta.get("wav") else {})
    bank: dict[str, Prompt] = {}
    for bucket, e in entries.items():
        if bucket in BUCKETS and e.get("text") and (folder / str(e.get("wav"))).exists():
            bank[bucket] = Prompt(folder / e["wav"], e["text"], e["ref_id"], float(e.get("dur_s") or 0.0),
                                  list(e.get("lines") or []))
    return bank


def target_bank(
    root: Path,
    char_id: str,
    line_ids: Iterable[str],
    *,
    post: dict[str, dict[str, Any]],
    qa: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    texts: dict[str, str],
    min_accepted: int,
    max_cer: float,
    nonverbal: set[str],
    buckets: dict[str, str] | None = None,
) -> dict[str, Prompt]:
    """{bucket: target-language prompt} of a character, built from accepted dubs once there are enough.

    `post`: latest post rows (their wav is the final audio, after vc); `texts`: exact synthesized text
    per line id (from synth/results.jsonl for the accepted attempt); `buckets`: {line id: bucket of
    its original} (calm when missing)."""
    folder = root / layout.SYNTH_PROMPTS_DIR / char_id
    bank = _load_bank(folder)
    buckets = buckets or {}
    cands: list[tuple[float, float, float, str, Path, float]] = []
    for lid in line_ids:
        row, q, dec = post.get(lid), qa.get(lid), decisions.get(lid)
        if not row or not q or not dec or lid in nonverbal or not texts.get(lid):
            continue
        attempt = row.get("attempt")
        if dec.get("decision") != "accept" or dec.get("attempt") not in (None, attempt):
            continue
        if dec.get("by") == "auto" and dec.get("attention"):  # accepted with doubts (flat/unnatural, mixed)
            continue
        if q.get("attempt") != attempt or q.get("wav") != row.get("wav") or q.get("cer") is None:
            continue
        cer = float(q["cer"])
        wav = _abs(root, row.get("wav"))
        if cer > max_cer or wav is None or not wav.exists():
            continue
        dur = _num(row.get("target_s")) or _num(row.get("dur_s"))
        cands.append((-piece_score(q), cer, abs(dur - 7.0), lid, wav, dur))
    if len(cands) < max(1, min_accepted):
        return bank
    cands.sort(key=lambda c: c[:4])
    lo, hi = BANK_PIECE_S
    changed = False
    for bucket in BUCKETS:
        if bucket in bank:
            continue  # frozen
        picked: list[tuple[str, Path, float]] = []
        for _s, _c, _d, lid, wav, dur in cands:
            if buckets.get(lid, "calm") != bucket or not lo <= dur <= hi:
                continue
            total = sum(p[2] for p in picked)
            if picked and (total >= BANK_TARGET_S[0] or total + GAP_S + dur > BANK_TARGET_S[1]):
                continue
            picked.append((lid, wav, dur))
            if len(picked) == 2 or total + dur >= BANK_TARGET_S[0]:
                break
        if not picked:
            continue
        key = _sha([(lid, str(w), w.stat().st_size) for lid, w, _ in picked])[:12]
        name = f"tgt-{bucket}-{key}.wav"
        dur = _join([w for _, w, _ in picked], folder / name)
        text = " ".join(texts[lid].strip() for lid, _, _ in picked)
        bank[bucket] = Prompt(folder / name, text, f"tgt:{char_id}:{bucket}:{key}", round(dur, 2),
                              [lid for lid, _, _ in picked])
        changed = True
    if changed:
        write_json(folder / "tgt.json", {"bank": {b: {"wav": p.wav.name, "text": p.text, "ref_id": p.ref_id,
                                                      "dur_s": p.dur_s, "lines": p.lines} for b, p in bank.items()},
                                         "created": now_iso()})
    return bank
