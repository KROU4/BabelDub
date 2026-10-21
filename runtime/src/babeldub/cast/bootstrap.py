"""Bootstrap voice prompts (tts.ref_strategy=bootstrap, the default for VoxCPM2).

1. Long timbre reference: 3-4 clean original lines of the character (4-7.5 s each, no flags, the cast
   references first) joined with 0.35 s gaps, ~15-20 s. It is the `reference_wav_path` of every line:
   the timbre comes from the original actor.
2. Target-language prompt: once at least `min_accepted` lines of the character were accepted by QA
   (lowest CER first), their final audio (after `vc` when the character has an RVC model) and the exact
   text they were synthesized from are joined into a ~20-26 s continuation prompt in the target
   language. The rest of the character's lines are spoken with both, so the dub keeps the actor's
   timbre AND sounds native. The prompt is frozen once built (synth/prompts/<char_id>/tgt.json).
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
from babeldub.core import layout
from babeldub.core.jsonl import now_iso, read_json, write_json

GAP_S = 0.35
EDGE_S = 0.05  # silence kept around each part
LONG_REF_RANGE = (4.0, 7.5)
LONG_REF_RELAXED = (2.0, 12.0)
LONG_REF_TARGET_S = 15.0
LONG_REF_MAX_S = 20.0
PROMPT_MAX_S = 26.0
MAX_PARTS = 4
PHASE1_RANGE = (2.5, 8.0)  # first lines of a character: plain lines that make good prompt pieces


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


def long_reference(root: Path, char_id: str, lines: Iterable[dict[str, Any]], refs: list[dict[str, Any]]) -> Prompt | None:
    """The character's ~15-20 s timbre reference (cached under synth/prompts/<char_id>/)."""
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
    out = root / layout.SYNTH_PROMPTS_DIR / char_id / f"src-{key}.wav"
    if not out.exists():
        dur = _join([p for p in paths if p is not None], out)
    else:
        data, sr = read_audio(out)
        dur = data.shape[0] / sr
    return Prompt(out, "", f"long:{char_id}:{key}", round(dur, 2), [ln["id"] for ln in picked])


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


def target_prompt(
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
) -> Prompt | None:
    """The frozen target-language prompt of a character, built from accepted dubs when there are enough.

    `post`: latest post rows (their wav is the final audio, after vc); `texts`: exact synthesized text
    per line id (from synth/results.jsonl for the accepted attempt)."""
    folder = root / layout.SYNTH_PROMPTS_DIR / char_id
    meta = read_json(folder / "tgt.json", default=None)
    if meta and (folder / str(meta.get("wav"))).exists() and meta.get("text"):
        return Prompt(folder / meta["wav"], meta["text"], meta["ref_id"], float(meta.get("dur_s") or 0.0),
                      list(meta.get("lines") or []))
    cands: list[tuple[float, float, str, Path]] = []
    for lid in line_ids:
        row, q, dec = post.get(lid), qa.get(lid), decisions.get(lid)
        if not row or not q or not dec or lid in nonverbal or not texts.get(lid):
            continue
        attempt = row.get("attempt")
        if dec.get("decision") != "accept" or dec.get("attempt") not in (None, attempt):
            continue
        if q.get("attempt") != attempt or q.get("wav") != row.get("wav") or q.get("cer") is None:
            continue
        cer = float(q["cer"])
        wav = _abs(root, row.get("wav"))
        if cer > max_cer or wav is None or not wav.exists():
            continue
        speech = _num(row.get("target_s")) or _num(row.get("dur_s"))
        cands.append((cer, abs(speech - 5.0), lid, wav))
    cands.sort(key=lambda c: (c[0], c[1], c[2]))
    picked: list[tuple[str, Path]] = []
    total = 0.0
    for _cer, _d, lid, wav in cands:
        dur = _num(post[lid].get("target_s")) or _num(post[lid].get("dur_s"))
        if picked and total + dur > PROMPT_MAX_S:
            continue
        picked.append((lid, wav))
        total += dur + GAP_S
        if len(picked) >= MAX_PARTS:
            break
    if len(picked) < max(1, min_accepted):
        return None
    key = _sha([(lid, str(w), w.stat().st_size) for lid, w in picked])[:12]
    name = f"tgt-{key}.wav"
    dur = _join([w for _, w in picked], folder / name)
    text = " ".join(texts[lid].strip() for lid, _ in picked)
    prompt = Prompt(folder / name, text, f"tgt:{char_id}:{key}", round(dur, 2), [lid for lid, _ in picked])
    write_json(folder / "tgt.json", {"wav": name, "text": text, "ref_id": prompt.ref_id, "dur_s": prompt.dur_s,
                                     "lines": prompt.lines, "created": now_iso()})
    return prompt
