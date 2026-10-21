"""Verification of a rebuilt bank against the original (no game launch).

Structural checks: RIFF size and chunk walk, SNDH entries (FSB5 magic, 32-byte alignment, sizes),
metadata outside the SND chunks byte-identical to the original, same number of FSBs, and per FSB the
same number, order and names of subsounds (the audio table addresses them by index); untouched FSBs
byte-identical; kept subsounds keep rate/channels/length. Content checks: replaced subsounds decode
with BabelDub's decoder to the expected length and match the replacement audio; optional
vgmstream cross-check; optional game-runtime check (gameruntime.py) when options["game_runtime"].
"""

from __future__ import annotations

import hashlib
import re
import shutil
import struct
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from babeldub.adapters.fmod import fsb5 as F
from babeldub.adapters.fmod import gameruntime, pcm, vgm
from babeldub.adapters.fmod.bank import Container
from babeldub.adapters.fmod.bankinfo import BankInfo, EventIndex, load_bank, parse_line_id
from babeldub.adapters.fmod.meta import programmer_keys
from babeldub.core.output import BabelDubError
from babeldub.core.tools import find_tool, run

_MIN_SNR_DB = 10.0  # wrong or misplaced audio scores around 0 dB; FADPCM keeps 20-45 dB
_KEPT_SAMPLES = 3
_MAX_RUNTIME_PROBES = 12


class _Report:
    def __init__(self) -> None:
        self.checks: list[str] = []
        self.problems: list[str] = []
        self.details: dict[str, Any] = {}

    def check(self, name: str, ok: bool, why: str = "") -> bool:
        if ok:
            self.checks.append(name)
        else:
            self.problems.append(f"{name}: {why}" if why else name)
        return ok

    def result(self) -> dict[str, Any]:
        return {
            "ok": not self.problems,
            "checks": self.checks,
            "problems": self.problems[:40],
            "details": self.details,
        }


def _snr_db(ref: np.ndarray, test: np.ndarray) -> float:
    n = min(len(ref), len(test))
    a = ref[:n].astype(np.float64)
    b = test[:n].astype(np.float64)
    noise = float(np.sum((a - b) ** 2))
    signal = float(np.sum(a * a))
    if noise == 0.0:
        return float("inf")
    return 10.0 * np.log10(max(signal, 1e-9) / noise)


def _hash_range(cont: Container, offset: int, size: int) -> str:
    h = hashlib.sha1()
    with cont.path.open("rb") as fh:
        fh.seek(offset)
        remaining = size
        while remaining:
            block = fh.read(min(remaining, 8 << 20))
            if not block:
                break
            h.update(block)
            remaining -= len(block)
    return h.hexdigest()


def _masked_meta(cont: Container) -> list[tuple[bytes, bytes]]:
    """(chunk id, payload) of top-level non-SND chunks with SNDH entries zeroed."""
    out: list[tuple[bytes, bytes]] = []
    for chunk in cont.chunks:
        if chunk.cid == b"SND ":
            out.append((chunk.cid, b""))
            continue
        data = bytearray(cont.meta.get(chunk.pos, b""))
        sndh = cont.sndh
        if sndh is not None and chunk.pos <= sndh.pos < chunk.end:
            start = sndh.data_pos + cont.sndh_header - chunk.data_pos
            data[start : start + len(cont.fsbs) * cont.sndh_stride] = bytes(len(cont.fsbs) * cont.sndh_stride)
        out.append((chunk.cid, bytes(data)))
    return out


def _check_structure(orig: BankInfo, built: BankInfo, rep: _Report) -> None:
    oc, bc = orig.container, built.container
    rep.check("container_kind", oc.kind == bc.kind, f"{oc.kind} -> {bc.kind}")
    if bc.kind == "bank":
        with bc.path.open("rb") as fh:
            head = fh.read(12)
        (riff_size,) = struct.unpack_from("<I", head, 4)
        rep.check("riff_size", riff_size == bc.file_size - 8, f"{riff_size} != {bc.file_size - 8}")
        last_end = bc.chunks[-1].end if bc.chunks else 12
        rep.check(
            "chunk_walk_to_eof", last_end == bc.file_size, f"chunks end at {last_end}, file {bc.file_size}"
        )
        rep.check(
            "chunk_sequence",
            [(c.cid, c.list_type) for c in oc.chunks] == [(c.cid, c.list_type) for c in bc.chunks],
            "top-level chunk ids differ",
        )
        aligned = all(loc.offset % 32 == 0 for loc in bc.fsbs)
        inside = all(loc.chunk is not None for loc in bc.fsbs)
        rep.check("sndh_entries", aligned and inside, "FSB5 not 32-byte aligned or outside its SND chunk")
        rep.check(
            "metadata_identical", _masked_meta(oc) == _masked_meta(bc), "metadata outside SND chunks changed"
        )
    rep.check("fsb_count", len(orig.fsbs) == len(built.fsbs), f"{len(orig.fsbs)} -> {len(built.fsbs)}")
    if orig.audio_table is not None:
        rep.check("audio_table", built.audio_table == orig.audio_table, "STBL no longer matches its FSB")


def _expected_replacements(options: dict[str, Any], relpath: str) -> dict[tuple[int, int], Path | None]:
    out: dict[tuple[int, int], Path | None] = {}
    for line_id, wav in (options.get("replacements") or {}).items():
        out[parse_line_id(line_id, relpath)] = Path(wav)
    for line_id in options.get("replaced_ids") or []:
        out.setdefault(parse_line_id(line_id, relpath), None)
    return out


def _check_fsbs(
    orig: BankInfo,
    built: BankInfo,
    expected: dict[tuple[int, int], Path | None],
    options: dict[str, Any],
    rep: _Report,
    kept_keys: list[tuple[int, int]],
) -> dict[tuple[int, int], np.ndarray]:
    """Per-FSB checks; returns own-decoder PCM of checked subsounds, kept ones listed in kept_keys."""
    decoded: dict[tuple[int, int], np.ndarray] = {}
    summary: list[dict[str, Any]] = []
    rate_opt = options.get("sample_rate")
    for k, (fo, fb) in enumerate(zip(orig.fsbs, built.fsbs, strict=False)):
        names_o = [s.name for s in fo.subsounds]
        names_b = [s.name for s in fb.subsounds]
        rep.check(
            f"fsb{k}_names_order",
            names_o == names_b,
            f"{len(names_o)} -> {len(names_b)} subsounds or names differ",
        )
        lo, lb = orig.container.fsbs[k], built.container.fsbs[k]
        identical = lo.size == lb.size and _hash_range(orig.container, lo.offset, lo.size) == _hash_range(
            built.container, lb.offset, lb.size
        )
        replaced_here = sorted(i for (kk, i) in expected if kk == k)
        summary.append(
            {"sub": f"fsb{k}", "codec": f"{fo.codec_name}->{fb.codec_name}", "identical": identical}
        )
        if identical:
            rep.check(
                f"fsb{k}_unchanged", not replaced_here, f"expected {len(replaced_here)} replaced subsounds"
            )
            continue
        if names_o != names_b:
            continue
        inferred = [
            s.index
            for s, t in zip(fo.subsounds, fb.subsounds, strict=True)
            if (s.rate, s.channels, s.nsamples) != (t.rate, t.channels, t.nsamples)
        ]
        changed_unexpected = [i for i in inferred if (k, i) not in expected] if expected else []
        rep.check(
            f"fsb{k}_kept_format",
            not changed_unexpected,
            f"{len(changed_unexpected)} kept subsounds changed rate/channels/length (first: {changed_unexpected[:3]})",
        )
        if fb.codec not in F.NATIVE_DECODE:
            rep.check(
                f"fsb{k}_codec", False, f"rebuilt FSB uses {fb.codec_name}, which BabelDub cannot decode"
            )
            continue
        targets = replaced_here or inferred
        bad_len: list[int] = []
        bad_audio: list[str] = []
        for i in targets:
            sub_o, sub_b = fo.subsounds[i], fb.subsounds[i]
            try:
                data = F.decode_native(fb, sub_b, built.read_data(k, sub_b))
            except F.Fsb5Error as exc:
                rep.problems.append(f"fsb{k}:{i} decode: {exc}")
                continue
            decoded[(k, i)] = data
            wav = expected.get((k, i))
            if wav is not None and wav.is_file():
                info = sf.info(str(wav))
                rate = int(rate_opt) if rate_opt else sub_o.rate
                want = pcm.resampled_length(info.frames, int(info.samplerate), rate)
                if (sub_b.nsamples, sub_b.rate, sub_b.channels) != (want, rate, sub_o.channels):
                    bad_len.append(i)
                    continue
                ref, _, _ = pcm.load_for_subsound(wav, sub_o.channels, rate)
                if fb.codec == F.CODEC_PCM16:
                    ok = np.array_equal(ref, data)
                else:
                    ok = _snr_db(ref[:, 0], data[:, 0]) >= _MIN_SNR_DB or float(np.abs(ref).max()) < 64
                if not ok:
                    bad_audio.append(f"{i}")
            elif data.shape[0] != sub_b.nsamples:
                bad_len.append(i)
        rep.check(f"fsb{k}_replaced_length", not bad_len, f"length/format mismatch at {bad_len[:5]}")
        rep.check(
            f"fsb{k}_replaced_audio",
            not bad_audio,
            f"decoded audio differs from replacement at {bad_audio[:5]}",
        )
        # a few kept subsounds: decodable and the right length
        kept = [s.index for s in fb.subsounds if (k, s.index) not in expected and s.index not in inferred]
        sample = sorted(
            set(kept[:1] + kept[-1:] + kept[:: max(1, len(kept) // _KEPT_SAMPLES)][:_KEPT_SAMPLES])
        )
        bad_kept = []
        for i in sample:
            sub_b = fb.subsounds[i]
            try:
                data = F.decode_native(fb, sub_b, built.read_data(k, sub_b))
                if data.shape[0] != sub_b.nsamples:
                    bad_kept.append(i)
                decoded[(k, i)] = data
                kept_keys.append((k, i))
            except F.Fsb5Error:
                bad_kept.append(i)
        rep.check(f"fsb{k}_kept_decode", not bad_kept, f"kept subsounds failed to decode: {bad_kept}")
        summary[-1].update(replaced=len(targets), kept_checked=len(sample))
    rep.details["fsbs"] = summary
    return decoded


def _vgm_count(exe: Path, path: Path) -> int | None:
    proc = run([exe, "-m", path], timeout=120)
    m = re.search(r"stream count:\s*(\d+)", proc.stdout)
    return int(m.group(1)) if m else (1 if "sample rate" in proc.stdout else None)


def _check_vgmstream(
    orig: BankInfo, built: BankInfo, decoded: dict[tuple[int, int], np.ndarray], rep: _Report
) -> None:
    exe = find_tool("vgmstream-cli")
    if exe is None:
        rep.details["vgmstream"] = "skipped (vgmstream-cli not found)"
        return
    total = sum(len(f.subsounds) for f in orig.fsbs)
    try:
        count = _vgm_count(exe, built.path)
    except BabelDubError as exc:
        rep.check("vgmstream_opens", False, str(exc))
        return
    rep.check("vgmstream_stream_count", count == total, f"{count} != {total}")
    probes = list(decoded)[:6]
    if not probes:
        return
    with tempfile.TemporaryDirectory(prefix=".babeldub-vgm-") as tmp_name:
        files = vgm.decode_subsongs(built.path, [built.subsong(k, i) for k, i in probes], Path(tmp_name))
        differ = []
        for k, i in probes:
            data, _ = sf.read(str(files[built.subsong(k, i)]), dtype="int16", always_2d=True)
            if not np.array_equal(data, decoded[(k, i)]):
                differ.append(f"fsb{k}:{i}")
    rep.check("vgmstream_matches_decoder", not differ, f"vgmstream decodes differently: {differ}")


def _check_kept_content(
    orig: BankInfo,
    built: BankInfo,
    decoded: dict[tuple[int, int], np.ndarray],
    kept_keys: list[tuple[int, int]],
    use_vgmstream: bool,
    rep: _Report,
) -> None:
    """Kept subsounds of a rewritten FSB still carry the original audio (catches misordering)."""
    if not kept_keys:
        return
    originals: dict[tuple[int, int], np.ndarray] = {}
    foreign = [(k, i) for k, i in kept_keys if orig.fsbs[k].codec not in F.NATIVE_DECODE]
    for k, i in kept_keys:
        if (k, i) not in foreign:
            sub = orig.subsound(k, i)
            originals[(k, i)] = F.decode_native(orig.fsbs[k], sub, orig.read_data(k, sub))
    if foreign and use_vgmstream and find_tool("vgmstream-cli") is not None:
        with tempfile.TemporaryDirectory(prefix=".babeldub-vgm-") as tmp_name:
            files = vgm.decode_subsongs(orig.path, [orig.subsong(k, i) for k, i in foreign], Path(tmp_name))
            for k, i in foreign:
                originals[(k, i)], _ = sf.read(str(files[orig.subsong(k, i)]), dtype="int16", always_2d=True)
    bad = []
    for key, ref in originals.items():
        new = decoded[key]
        if built.fsbs[key[0]].codec == F.CODEC_PCM16:
            ok = np.array_equal(ref, new)
        else:
            ok = float(np.abs(ref).max(initial=0)) < 64 or _snr_db(ref[:, 0], new[:, 0]) >= _MIN_SNR_DB
        if not ok:
            bad.append(f"fsb{key[0]}:{key[1]}")
    rep.details["kept_compared"] = len(originals)
    if originals:
        rep.check("kept_audio_matches_original", not bad, f"kept subsounds differ from the original: {bad}")


def _runtime_banks(orig_path: Path, strings_banks: list[Path]) -> list[Path]:
    """Banks to load before the checked one: string banks and master banks next to it."""
    folder = orig_path.parent
    pre = [p for p in strings_banks if p.exists()]
    for p in sorted(folder.glob("*.bank")):
        stem = p.name[: -len(".bank")]
        if p.resolve() == orig_path.resolve() or p in pre:
            continue
        if stem.lower().endswith(".strings"):
            pre.insert(0, p)
        elif stem.lower().startswith("master"):
            pre.append(p)
    return pre


def _check_game_runtime(
    game_dir: Path,
    orig: BankInfo,
    built: BankInfo,
    decoded: dict[tuple[int, int], np.ndarray],
    events: EventIndex,
    strings_banks: list[Path],
    rep: _Report,
) -> None:
    lib = gameruntime.find_fmod_library(game_dir)
    if lib is None:
        rep.check("game_runtime", False, "no fmodstudio library for this platform in the game folder")
        return
    keys = programmer_keys(orig.container) if orig.container.kind == "bank" else []
    with tempfile.TemporaryDirectory(prefix=".babeldub-fmodrt-") as tmp_name:
        tmp = Path(tmp_name)
        copy = tmp / orig.path.name
        shutil.copyfile(built.path, copy)
        preload = [str(p) for p in _runtime_banks(orig.path, strings_banks)]
        base_spec = {"dll": str(lib), "preload": preload, "count_keys": keys}
        baseline = gameruntime.run_check({**base_spec, "bank": str(orig.path), "probes": []}, tmp)
        probes = []
        for n, ((k, i), data) in enumerate(decoded.items()):
            if n >= _MAX_RUNTIME_PROBES:
                break
            sub = built.subsound(k, i)
            name = sub.name or ""
            _, table_key, _ = events.resolve(name)
            cand = [key for key in (table_key, name) if key]
            if not cand:
                continue
            raw = tmp / f"expect_{k}_{i}.pcm"
            raw.write_bytes(np.ascontiguousarray(data, dtype="<i2").tobytes())
            probes.append(
                {
                    "index": i,
                    "name": name,
                    "keys": cand,
                    # without an audio-table key the subsound may be played by a regular event only
                    "required": table_key is not None,
                    "nsamples": sub.nsamples,
                    "channels": sub.channels,
                    "rate": sub.rate,
                    "expect": str(raw),
                }
            )
        result = gameruntime.run_check({**base_spec, "bank": str(copy), "probes": probes}, tmp)
    rows = result.get("probes", [])
    rep.details["game_runtime"] = {
        "library": str(lib),
        "fmod_dll": result.get("fmod_dll_version"),
        "keys": result.get("keys"),
        "keys_original": baseline.get("keys"),
        "sample_data": result.get("sample_data"),
        "probes": len(probes),
        "opened": sum(1 for p in rows if "key" in p),
        "exact": sum(1 for p in rows if p.get("exact")),
        "lines": [
            {"i": p.get("index"), "len": p.get("length"), "type": p.get("type"), "exact": p.get("exact")}
            for p in rows
            if "key" in p
        ][:_MAX_RUNTIME_PROBES],
    }
    target = (result.get("loaded") or {}).get(copy.name) or {}
    rep.check(
        "game_runtime_loads",
        target.get("result") == 0 and target.get("state") == gameruntime.LOADED,
        "; ".join(result.get("problems", [])[:3]) or f"loadBankFile -> {target}",
    )
    if baseline.get("sample_data") == gameruntime.LOADED:
        rep.check(
            "game_runtime_sample_data",
            result.get("sample_data") == gameruntime.LOADED,
            f"event sample data state {result.get('sample_data')} (original: loaded)",
        )
    if keys:
        rep.check(
            "game_runtime_keys",
            result.get("keys") == baseline.get("keys"),
            f"audio-table keys resolve differently: {result.get('keys')} vs original {baseline.get('keys')}",
        )
    if probes:
        rep.check(
            "game_runtime_probes", not result.get("problems"), "; ".join(result.get("problems", [])[:5])
        )


def verify_build(
    game_dir: Path,
    orig_path: Path,
    built_path: Path,
    relpath: str,
    options: dict[str, Any],
    events: EventIndex,
    strings_banks: list[Path],
) -> dict[str, Any]:
    rep = _Report()
    if not built_path.is_file():
        rep.check("exists", False, f"{built_path} not found")
        return rep.result()
    orig = load_bank(orig_path)
    try:
        built = load_bank(built_path)
    except BabelDubError as exc:
        rep.check("parse", False, str(exc))
        return rep.result()
    rep.checks.append("parse")
    _check_structure(orig, built, rep)
    expected = _expected_replacements(options, relpath)
    kept_keys: list[tuple[int, int]] = []
    decoded = _check_fsbs(orig, built, expected, options, rep, kept_keys)
    use_vgm = bool(options.get("vgmstream", True))
    if use_vgm:
        _check_vgmstream(orig, built, decoded, rep)
    _check_kept_content(orig, built, decoded, kept_keys, use_vgm, rep)
    if options.get("game_runtime"):
        _check_game_runtime(game_dir, orig, built, decoded, events, strings_banks, rep)
    return rep.result()
