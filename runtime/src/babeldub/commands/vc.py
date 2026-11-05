"""`babeldub vc train` and `babeldub vc`: RVC v2 timbre transfer per character (Applio, MIT).

vc train  For each character: a dataset of its clean original lines (2-20 s, no flags, up to
          vc.minutes), an RVC v2 model trained on it (vc.epochs, 40 kHz, rmvpe, pretrained HiFi-GAN),
          stored in the project as vc/<char_id>/model.pth + model.index + model.json. Train once per
          main character; it works for any target language.
vc        Converts the latest post audio of every line whose character has a model, then re-applies
          the exact-length + loudness pass against the pre-conversion take, matches the channel of the
          original again (post.match, from the take before post's match; with post.match_target=character
          after all takes of the character are converted, since its pair curve is measured on the
          converted takes), writes vc/audio/<uid>.wav
          and appends a post/post.jsonl row (stage "vc"), so QA checks and the build packs the
          converted take. Characters without a model pass through unchanged. Order: synth -> post
          -> vc -> qa. GPU first (Applio uses CUDA when the device is cuda), under the project GPU lock.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import math
import shutil
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from babeldub.analysis.store import load_analysis
from babeldub.audio.dsp import level_db, to_mono
from babeldub.audio.fit import conform, speech_bounds
from babeldub.audio.io import read_audio, write_wav
from babeldub.audio.pitch import f0_median_hz, semitones
from babeldub.cast.characters import match_char
from babeldub.commands import voice
from babeldub.commands.cast import sub_common
from babeldub.core import layout
from babeldub.core.compute import resolve_device
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import append_jsonl, latest_by, now_iso, read_json, read_jsonl, write_json
from babeldub.core.output import EXIT_ATTENTION, EXIT_ERROR, EXIT_OK, EXIT_USAGE, BabelDubError, clip, emit
from babeldub.core.project import Project
from babeldub.core.takes import follower_map
from babeldub.vc import rvc

VOICED_KINDS = ("dialogue", "cutscene", "bark")
DATASET_RANGE = (2.0, 20.0)
DEFAULTS: dict[str, Any] = {"epochs": 20, "minutes": 30.0, "index_rate": 0.75, "protect": 0.33, "f0_method": "rmvpe",
                            "pitch": "line"}
PITCH_MODES = ("keep", "char", "line")
MAX_SHIFT_ST = 12


def _setting(project: Project, key: str) -> Any:
    return project.setting("vc", key, DEFAULTS[key])


def pitch_mode(project: Project, override: str | None = None) -> str:
    """vc.pitch (or the --pitch override); anything outside PITCH_MODES is a usage error."""
    mode = override if override is not None else _setting(project, "pitch")
    if mode not in PITCH_MODES:
        raise BabelDubError(f"vc.pitch must be one of {'|'.join(PITCH_MODES)}, got {mode!r}",
                            code=EXIT_USAGE, hint="babeldub set vc.pitch=line")
    return str(mode)


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _abs(project: Project, rel: str | None) -> Path | None:
    if not rel:
        return None
    path = Path(rel)
    return path if path.is_absolute() else project.path(rel)


def _uid(line: dict[str, Any]) -> str:
    return str(line.get("uid") or make_uid(str(line["id"])))


def _char_map(project: Project, lines: dict[str, dict[str, Any]]) -> dict[str, str]:
    """line id -> char_id (cast assignment, else the speaker hint)."""
    assign = latest_by(read_jsonl(project.path(layout.ASSIGN)))
    if assign:
        return {lid: str(row["char_id"]) for lid, row in assign.items() if row.get("char_id")}
    return {lid: str(ln.get("speaker_hint") or "unknown") for lid, ln in lines.items()}


def _device(project: Project, arg: str | None) -> str:
    return resolve_device(arg or project.setting("compute", "device", "auto"))


def load_models(project: Project) -> dict[str, dict[str, Any]]:
    """char_id -> model.json of every trained character whose files exist."""
    out: dict[str, dict[str, Any]] = {}
    base = project.path(layout.VC_DIR)
    if not base.is_dir():
        return out
    for meta_path in base.glob(f"*/{layout.VC_MODEL_JSON}"):
        meta = read_json(meta_path, default=None) or {}
        folder = meta_path.parent
        if meta.get("char_id") and (folder / "model.pth").is_file() and (folder / "model.index").is_file():
            out[str(meta["char_id"])] = {**meta, "pth": folder / "model.pth", "index": folder / "model.index"}
    return out


# Bumped when the conversion itself changes; "keep-f0": no --proposed-pitch, so older takes are converted again.
CONVERSION_VERSION = "keep-f0"


def conversion_key(model: dict[str, Any], settings: dict[str, Any], char_st: int | None = None) -> str:
    """Identity of a conversion; with vc.pitch=char also the character's shift (`char_st`), so a changed
    shift converts all of the character's takes again instead of mixing two registers."""
    pth: Path = model["pth"]
    stat = pth.stat()
    raw = (f"{model.get('trained')}|{stat.st_size}|{settings['index_rate']}|{settings['protect']}"
           f"|{settings['f0_method']}|{CONVERSION_VERSION}")
    if settings.get("pitch", "keep") != "keep":  # keep: the same key as before vc.pitch existed
        raw += f"|pitch={settings['pitch']}"
    if settings.get("pitch") == "char":
        raw += f"{int(char_st or 0):+d}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# ---- vc train -------------------------------------------------------------------------------
def _resolve_chars(project: Project, wanted: list[str], char_of: dict[str, str]) -> tuple[list[str], list[str]]:
    chars = list(latest_by(read_jsonl(project.path(layout.CHARACTERS)), "char_id").values())
    known = {cid.lower(): cid for cid in char_of.values()}
    found, unknown = [], []
    for token in wanted:
        row = match_char(chars, token) if chars else None
        if row is not None:
            found.append(str(row["char_id"]))
        elif token.lower() in known:
            found.append(known[token.lower()])
        else:
            unknown.append(token)
    return list(dict.fromkeys(found)), unknown


def dataset_lines(project: Project, lines: dict[str, dict[str, Any]], char_of: dict[str, str], char_id: str,
                  minutes: float) -> list[tuple[dict[str, Any], Path]]:
    """Clean original lines of the character (2-20 s, no flags), spread over the game, up to `minutes`."""
    lo, hi = DATASET_RANGE
    pool = []
    for lid, line in lines.items():
        if char_of.get(lid) != char_id or line.get("kind") not in VOICED_KINDS or line.get("flags"):
            continue
        dur = float((line.get("audio") or {}).get("dur_s") or 0.0)
        wav = _abs(project, (line.get("audio") or {}).get("wav"))
        if lo <= dur <= hi and wav is not None and wav.exists():
            pool.append((_uid(line), dur, line, wav))
    pool.sort(key=lambda p: p[0])  # uid order is a stable pseudo-random spread over scenes
    picked, total = [], 0.0
    for _key, dur, line, wav in pool:
        if total >= minutes * 60.0:
            break
        picked.append((line, wav))
        total += dur
    return picked


def cmd_vc_train(args: argparse.Namespace) -> int:
    project = Project.load(getattr(args, "project", None))
    project.require_stage("extract")
    wanted = _csv(args.chars)
    if not wanted:
        raise BabelDubError("--chars is empty", code=EXIT_USAGE, hint="babeldub vc train --chars <char_id,...>")
    epochs = int(args.epochs or _setting(project, "epochs"))
    minutes = float(args.minutes or _setting(project, "minutes"))
    if epochs < 1 or minutes <= 0:
        raise BabelDubError("need --epochs >= 1 and --minutes > 0", code=EXIT_USAGE)
    lines = latest_by(read_jsonl(project.path(layout.LINES)))
    char_of = _char_map(project, lines)
    chars, unknown = _resolve_chars(project, wanted, char_of)
    if unknown:
        raise BabelDubError("unknown characters", code=EXIT_USAGE, unknown=unknown,
                            hint="use char_id or name from cast/casting_sheet.md")
    models = load_models(project)
    plan: list[dict[str, Any]] = []
    for cid in chars:
        data = dataset_lines(project, lines, char_of, cid, minutes)
        speech = sum(float((ln.get("audio") or {}).get("dur_s") or 0.0) for ln, _ in data)
        # explicit --epochs wins; otherwise small datasets get more epochs (similar step count)
        item_epochs = epochs if args.epochs else rvc.auto_epochs(speech / 60.0, epochs)
        item = {"char_id": cid, "files": len(data), "minutes": round(speech / 60.0, 1), "epochs": item_epochs,
                "data": data}
        if cid in models and not args.force:
            item["action"] = "skip"
        elif not data:
            item["action"] = "no_data"
        else:
            item["action"] = "train"
        plan.append(item)
    summary = [{k: v for k, v in it.items() if k != "data"} for it in plan]
    if args.dry_run:
        return emit({"stage": "vc_train", "dry_run": True, "epochs": epochs, "plan": summary,
                     "estimate": "~15 min per character for 20 epochs on 30 min of speech (RTX 3060 Ti)"})
    todo = [it for it in plan if it["action"] == "train"]
    no_data = [it["char_id"] for it in plan if it["action"] == "no_data"]
    skipped = [it["char_id"] for it in plan if it["action"] == "skip"]
    if not todo:
        code = EXIT_ATTENTION if no_data else EXIT_OK
        return emit({"stage": "vc_train", "trained": [], "skipped": skipped, "no_data": no_data,
                     "next": "babeldub vc" if not no_data else "pick characters with clean lines of 2-20 s"}, code)
    device = _device(project, args.device)
    if not device.startswith("cuda"):
        raise BabelDubError("RVC training needs an NVIDIA GPU", code=EXIT_USAGE,
                            hint="train on a machine with CUDA; `babeldub vc` then converts on the CPU too")
    python, applio = rvc.require_applio()
    from babeldub.workers.client import GpuLock

    lock = GpuLock(project.path(layout.GPU_LOCK), owner="vc-train", wait_s=args.wait_lock)
    trained: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    with lock:
        for item in todo:
            cid = item["char_id"]
            started = time.perf_counter()
            result = _train_one(project, python, applio, cid, item["data"], item["epochs"])
            if "error" in result:
                failed.append(result)
                continue
            trained.append({"char_id": cid, "files": item["files"], "minutes": item["minutes"], "epochs": item["epochs"],
                            "elapsed_s": round(time.perf_counter() - started, 1)})
    project.log_event("vc_train", trained=[t["char_id"] for t in trained], failed=[f["char_id"] for f in failed],
                      epochs=epochs)
    payload: dict[str, Any] = {"stage": "vc_train", "epochs": epochs, "trained": trained, "skipped": skipped}
    if no_data:
        payload["no_data"] = no_data
    if failed:
        payload["failed"] = clip(failed, 3)
        payload["next"] = "read the training log of the failed character, fix the cause, run `babeldub vc train` again"
        return emit(payload, EXIT_ERROR)
    payload["next"] = "babeldub vc"
    return emit(payload, EXIT_ATTENTION if no_data else EXIT_OK)


def _train_one(project: Project, python: Path, applio: Path, cid: str, data: list[tuple[dict[str, Any], Path]],
               epochs: int) -> dict[str, Any]:
    folder = project.path(layout.VC_DIR, cid)
    dataset = folder / "dataset"
    if dataset.exists():
        shutil.rmtree(dataset)
    dataset.mkdir(parents=True)
    for line, wav in data:
        shutil.copy2(wav, dataset / f"{_uid(line)}.wav")
    name = rvc.model_name(project.slug, cid)
    work = applio / "logs" / name
    if work.exists():  # a fresh model: Applio would resume from old checkpoints
        shutil.rmtree(work)
    log = project.path(layout.LOGS_DIR, f"vc-train-{name}.log")
    for cmd in rvc.train_commands(python, name, dataset, epochs, "0"):
        code = rvc.run(cmd, applio, log, rvc.TRAIN_STEP_TIMEOUT)
        if code != 0:
            return {"char_id": cid, "error": f"{cmd[2]} failed (exit code {code})", "log": project.rel(log),
                    "tail": rvc.log_tail(log)}
    pth, index = rvc.trained_files(work, name)
    if pth is None or index is None:
        return {"char_id": cid, "error": "Applio wrote no .pth/.index", "log": project.rel(log), "tail": rvc.log_tail(log)}
    shutil.copy2(pth, folder / "model.pth")
    shutil.copy2(index, folder / "model.index")
    write_json(folder / layout.VC_MODEL_JSON, {
        "char_id": cid, "engine": rvc.ENGINE, "applio_name": name, "epochs": epochs, "sample_rate": rvc.SAMPLE_RATE,
        "files": len(data), "minutes": round(sum(float((ln.get("audio") or {}).get("dur_s") or 0) for ln, _ in data) / 60, 2),
        "source_pth": pth.name, "source_index": index.name, "trained": now_iso(), "log": project.rel(log),
    })
    shutil.rmtree(work, ignore_errors=True)  # ~1.4 GB of G/D checkpoints, only useful to resume training
    return {"char_id": cid}


# ---- pitch shift per line -------------------------------------------------------------------
_F0_CACHE: dict[tuple[str, int, int], float | None] = {}


def _median_hz(path: Path | None) -> float | None:
    """Median F0 of a file; None when it is missing or unreadable (left out of the medians, like a file
    without voiced speech). Cached per process by path, size and mtime."""
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _F0_CACHE:
        try:
            data, sr = read_audio(path)
            _F0_CACHE[key] = f0_median_hz(data, sr)
        except (RuntimeError, ValueError, OSError):
            _F0_CACHE[key] = None
    return _F0_CACHE[key]


def _orig_median(project: Project, line: dict[str, Any], analysis: dict[str, dict[str, Any]]) -> float | None:
    measured = ((analysis.get(str(line.get("id"))) or {}).get("prosody") or {}).get("f0_median_hz")
    if isinstance(measured, (int, float)) and measured > 0:
        return float(measured)
    return _median_hz(_abs(project, (line.get("audio") or {}).get("wav")))


def _clip_shift(st: float | None) -> int:
    if st is None or not math.isfinite(st):
        return 0
    return int(max(-MAX_SHIFT_ST, min(MAX_SHIFT_ST, round(st))))


def _pre_vc_source(project: Project, row: dict[str, Any]) -> Path | None:
    """The take RVC converts: the pre-conversion take of a vc row, else post's take before its channel match."""
    return _abs(project, row.get("pre_vc_wav") if row.get("vc") else (row.get("pre_match_wav") or row.get("wav")))


def char_shift(project: Project, cid: str, char_of: dict[str, str], lines: dict[str, dict[str, Any]],
               post_latest: dict[str, dict[str, Any]], follows: dict[str, Any], fallback: list[Path] | None = None) -> int:
    """vc.pitch=char: the character's one shift, the median F0 of its originals over the median F0 of all its
    current pre-conversion takes (every latest post row, followers left out). A character without a measurable
    take yet uses the files in `fallback` (the candidates `synth score` converts)."""
    analysis = load_analysis(project)
    orig_values = [v for lid, ln in lines.items() if char_of.get(lid) == cid
                   if (v := _orig_median(project, ln, analysis))]
    dub_values = [v for lid, row in post_latest.items() if lid in lines and lid not in follows and char_of.get(lid) == cid
                  if (v := _median_hz(_pre_vc_source(project, row)))]
    if not dub_values:
        dub_values = [v for path in fallback or [] if (v := _median_hz(path))]
    if not orig_values or not dub_values:
        return 0
    return _clip_shift(semitones(statistics.median(orig_values), statistics.median(dub_values)))


def pitch_shifts(project: Project, items: list[tuple[dict[str, Any], dict[str, Any], Path]], mode: str,
                 char_st: int = 0) -> list[int]:
    """RVC `--pitch` (semitones) for each take of one character.

    keep  0: the take keeps the F0 the TTS cloned from the character's reference.
    char  `char_st` for every take (`char_shift`: one shift for the whole character).
    line  per take: the median F0 of the take's own original line over the take's median F0; a take or an
          original without enough voiced speech (or unreadable) gets 0."""
    if mode == "keep" or not items:
        return [0] * len(items)
    if mode == "char":
        return [char_st] * len(items)
    analysis = load_analysis(project)
    return [_clip_shift(semitones(_orig_median(project, line, analysis), _median_hz(source)))
            for line, _row, source in items]


# ---- vc (convert) ---------------------------------------------------------------------------
def cmd_vc(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    project.require_stage("post")
    lines = latest_by(read_jsonl(project.path(layout.LINES)))
    follows = follower_map(lines.values())
    char_of = _char_map(project, lines)
    post_latest = latest_by(read_jsonl(project.path(layout.POST)))
    models = load_models(project)
    mode = voice.match_mode(project)
    target = voice.match_target(project)
    settings = {k: _setting(project, k) for k in ("index_rate", "protect", "f0_method")}
    settings["pitch"] = pitch_mode(project, args.pitch)
    if args.index_rate is not None:
        settings["index_rate"] = args.index_rate
    if args.protect is not None:
        settings["protect"] = args.protect
    want_ids = set(_csv(args.ids))
    want_chars = {c.lower() for c in _csv(args.chars)}

    jobs: dict[str, list[tuple[dict[str, Any], dict[str, Any], Path]]] = defaultdict(list)
    passthrough: Counter[str] = Counter()
    up_to_date = 0
    missing: list[str] = []
    char_st: dict[str, int] = {}  # vc.pitch=char: the shift of each character, over all its current takes

    def key_of(cid: str, model: dict[str, Any]) -> str:
        if settings["pitch"] == "char" and cid not in char_st:
            char_st[cid] = char_shift(project, cid, char_of, lines, post_latest, follows)
        return conversion_key(model, settings, char_st.get(cid))

    for lid, row in post_latest.items():
        line = lines.get(lid)
        if line is None or lid in follows:
            continue
        if want_ids and lid not in want_ids and _uid(line) not in want_ids:
            continue
        cid = char_of.get(lid, "unknown")
        if want_chars and cid.lower() not in want_chars:
            continue
        model = models.get(cid)
        if model is None:
            passthrough[cid] += 1
            continue
        key = key_of(cid, model)
        current = _abs(project, row.get("wav"))
        if (not args.force and (row.get("vc") or {}).get("model_key") == key
                and voice.match_current(row, mode, target)
                and current is not None and current.exists()):
            up_to_date += 1
            continue
        # RVC converts the take as post fitted it, before any channel match
        source = _pre_vc_source(project, row)
        if source is None or not source.exists():
            missing.append(lid)
            continue
        jobs[cid].append((line, row, source))
    total = sum(len(v) for v in jobs.values())
    note = None
    if passthrough:
        note = ("characters without an RVC model pass through unchanged: "
                + ", ".join(f"{c} ({n})" for c, n in passthrough.most_common(5))
                + "; train main characters with `babeldub vc train --chars <ids>`")
    if args.dry_run:
        return emit({"stage": "vc", "dry_run": True, "would_convert": {c: len(v) for c, v in jobs.items()},
                     "up_to_date": up_to_date, "passthrough": dict(passthrough), "models": sorted(models),
                     "settings": settings, "note": note})
    if not total:
        status = "attention" if missing else "done"
        project.mark_stage("vc", status, converted=0, up_to_date=up_to_date, passthrough=sum(passthrough.values()))
        payload: dict[str, Any] = {"stage": "vc", "converted": 0, "up_to_date": up_to_date,
                                   "passthrough": dict(passthrough), "next": "babeldub qa"}
        if missing:
            payload["missing"] = clip(missing)
        if note:
            payload["notes"] = [note]
        return emit(payload, EXIT_ATTENTION if missing else EXIT_OK)

    python, applio = rvc.require_applio()
    device = _device(project, args.device)
    from babeldub.workers.client import GpuLock

    lock = GpuLock(project.path(layout.GPU_LOCK), owner="vc", wait_s=args.wait_lock)
    project.mark_stage("vc", "running", to_convert=total)
    converted = 0
    errors: list[str] = []
    ceiling = float(project.setting("post", "true_peak_db", -1.0))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log = project.path(layout.LOGS_DIR, f"vc-{stamp}.log")
    pool = voice.ChannelPool(project, lines, target) if mode != "off" else None
    matched: Counter[str] = Counter()
    with lock if device.startswith("cuda") else contextlib.nullcontext():
        for cid, items in jobs.items():
            model = models[cid]
            key = key_of(cid, model)
            shifts = pitch_shifts(project, items, str(settings["pitch"]), char_st.get(cid, 0))
            groups: dict[int, list[int]] = defaultdict(list)
            for i, shift in enumerate(shifts):
                groups[shift].append(i)
            rows: list[dict[str, Any]] = []
            # pass 1: convert every shift group and conform each take; with a character pair the takes wait in
            # vc/<char_id>/staged/ (the pair's dub side) for pass 2
            staged_dir = project.path(layout.VC_DIR, cid, STAGED_WORK)
            if staged_dir.exists():
                shutil.rmtree(staged_dir)
            staged: list[tuple[int, int, Path, tuple[int, float, float, str]]] = []
            for shift, members in sorted(groups.items()):
                work = project.path(layout.VC_DIR, cid, "batch")
                if work.exists():
                    shutil.rmtree(work)
                in_dir, out_dir = work / "in", work / "out"
                in_dir.mkdir(parents=True)
                out_dir.mkdir(parents=True)
                for i in members:
                    line, _row, source = items[i]
                    shutil.copy2(source, in_dir / f"{_uid(line)}.wav")
                cmd = rvc.convert_command(python, in_dir, out_dir, model["pth"], model["index"],
                                          index_rate=float(settings["index_rate"]),
                                          protect=float(settings["protect"]),
                                          f0_method=str(settings["f0_method"]), pitch=shift)
                code = rvc.run(cmd, applio, log, max(600.0, 30.0 * len(members)), cpu=not device.startswith("cuda"))
                for i in members:
                    line, row, source = items[i]
                    out = out_dir / f"{_uid(line)}_output.wav"
                    if not out.exists():
                        errors.append(f"{_uid(line)}: no output (Applio exit code {code})")
                        continue
                    try:
                        take, sr, peak, level_ref, measure = _conform_take(row, source, out, ceiling)
                        if pool is None or pool.target != "character":  # no pair: matched right away
                            rows.append(_finish(project, line, row, source, take, sr, peak, level_ref, measure, cid,
                                                key, model, settings, ceiling, shift, mode, pool))
                        else:
                            pre = write_wav(staged_dir / f"{_uid(line)}.wav", take, sr, subtype="PCM_16")
                            staged.append((i, shift, pre, (sr, peak, level_ref, measure)))
                    except (RuntimeError, ValueError, OSError) as exc:
                        errors.append(f"{_uid(line)}: {type(exc).__name__}: {exc}")
                shutil.rmtree(work, ignore_errors=True)
            # pass 2: the character's pair from the converted takes, then the channel match of each take
            if pool is not None and staged:
                pool.use_dubs(_pair_dubs(project, cid, key, {items[i][0]["id"]: pre for i, _s, pre, _m in staged},
                                         post_latest, char_of, follows))
                for i, shift, pre, (sr, peak, level_ref, measure) in staged:
                    line, row, source = items[i]
                    try:
                        take, _sr = read_audio(pre)
                        rows.append(_finish(project, line, row, source, take, sr, peak, level_ref, measure, cid,
                                            key, model, settings, ceiling, shift, mode, pool))
                    except (RuntimeError, ValueError, OSError) as exc:
                        errors.append(f"{_uid(line)}: {type(exc).__name__}: {exc}")
            shutil.rmtree(staged_dir, ignore_errors=True)
            append_jsonl(project.path(layout.POST), rows)
            converted += len(rows)
            for new in rows:
                voice.count_match(matched, new.get("match"))
    failed = total - converted
    status = "attention" if (failed or missing) else "done"
    project.mark_stage("vc", status, converted=converted, failed=failed, up_to_date=up_to_date,
                       passthrough=sum(passthrough.values()))
    payload = {"stage": "vc", "converted": converted, "failed": failed, "up_to_date": up_to_date,
               "passthrough": dict(passthrough), "device": device, "log": project.rel(log), "next": "babeldub qa"}
    if mode != "off":
        payload["match"] = voice.match_summary(matched, mode, target)
    if errors:
        payload["errors"] = clip(errors, 5)
        payload["tail"] = rvc.log_tail(log, 4)
    if missing:
        payload["missing"] = clip(missing)
    if note:
        payload["notes"] = [note]
    return emit(payload, EXIT_ATTENTION if (failed or missing) else EXIT_OK)


SCORE_WORK = "score"  # vc/<char_id>/score: candidates converted for `synth score`


def convert_for_scoring(project: Project, jobs: dict[str, list[tuple[str, Path, dict[str, Any]]]],
                        wait_lock: float = 0.0) -> tuple[dict[str, Path], list[str]]:
    """RVC-convert best-of candidates before `synth score` ranks them, so voice similarity (and MOS, prosody)
    is measured on the timbre that ships, with the `vc.pitch` shift `vc` would apply (char: `char_shift` over
    the character's current takes). `jobs`: {char_id: [(name, wav, line)]} of characters with a model. Returns
    ({name: converted wav}, errors); the files stay in vc/<char_id>/score/ until `drop_scoring_files`. An
    invalid vc.pitch is a usage error, as in `vc`; without Applio the BabelDubError of `rvc.require_applio`
    propagates."""
    models = load_models(project)
    jobs = {cid: items for cid, items in jobs.items() if cid in models and items}
    if not jobs:
        return {}, []
    mode = pitch_mode(project)
    python, applio = rvc.require_applio()
    device = _device(project, None)
    settings = {k: _setting(project, k) for k in ("index_rate", "protect", "f0_method")}
    lines = latest_by(read_jsonl(project.path(layout.LINES))) if mode == "char" else {}
    char_of = _char_map(project, lines) if mode == "char" else {}
    post_latest = latest_by(read_jsonl(project.path(layout.POST))) if mode == "char" else {}
    follows = follower_map(lines.values()) if mode == "char" else {}
    from babeldub.workers.client import GpuLock

    lock = GpuLock(project.path(layout.GPU_LOCK), owner="vc:score", wait_s=wait_lock)
    out: dict[str, Path] = {}
    errors: list[str] = []
    log = project.path(layout.LOGS_DIR, f"vc-score-{time.strftime('%Y%m%d-%H%M%S')}.log")
    with lock if device.startswith("cuda") else contextlib.nullcontext():
        for cid, items in jobs.items():
            model = models[cid]
            work = project.path(layout.VC_DIR, cid, SCORE_WORK)
            if work.exists():
                shutil.rmtree(work)
            char_st = (char_shift(project, cid, char_of, lines, post_latest, follows, [wav for _n, wav, _l in items])
                       if mode == "char" else 0)
            shifts = pitch_shifts(project, [(line, {}, wav) for _name, wav, line in items], mode, char_st)
            groups: dict[int, list[int]] = defaultdict(list)
            for i, shift in enumerate(shifts):
                groups[shift].append(i)
            for shift, members in sorted(groups.items()):
                in_dir, out_dir = work / f"in{shift:+d}", work / f"out{shift:+d}"
                in_dir.mkdir(parents=True)
                out_dir.mkdir(parents=True)
                for i in members:
                    shutil.copy2(items[i][1], in_dir / f"{items[i][0]}.wav")
                cmd = rvc.convert_command(python, in_dir, out_dir, model["pth"], model["index"],
                                          index_rate=float(settings["index_rate"]),
                                          protect=float(settings["protect"]),
                                          f0_method=str(settings["f0_method"]), pitch=shift)
                code = rvc.run(cmd, applio, log, max(600.0, 30.0 * len(members)), cpu=not device.startswith("cuda"))
                shutil.rmtree(in_dir, ignore_errors=True)
                for i in members:
                    name = items[i][0]
                    converted = out_dir / f"{name}_output.wav"
                    if converted.exists():
                        out[name] = converted
                    else:
                        errors.append(f"{name}: no RVC output (Applio exit code {code})")
    return out, errors


def drop_scoring_files(project: Project, char_ids: set[str] | list[str]) -> None:
    for cid in char_ids:
        shutil.rmtree(project.path(layout.VC_DIR, cid, SCORE_WORK), ignore_errors=True)


def _rounded(value: float) -> float | None:
    return round(value, 2) if math.isfinite(value) else None


STAGED_WORK = "staged"  # vc/<char_id>/staged: conformed takes between the two passes (post.match_target=character)


def _conform_take(row: dict[str, Any], source: Path, converted: Path,
                  ceiling: float) -> tuple[np.ndarray, int, float, float, str]:
    """Exact length + loudness of the converted take against its pre-conversion post take:
    (audio, sr, true peak, level_ref, measure)."""
    ref, ref_sr = read_audio(source)
    data, sr = read_audio(converted)
    measure = str(row.get("measure") or "lufs")
    level_ref = row.get("level_ref")
    if level_ref is None:  # older rows: the level of the pre-conversion speech
        mono = to_mono(ref)
        bounds = speech_bounds(mono, ref_sr) or (0, len(mono))
        level_ref = level_db(mono[bounds[0] : bounds[1]], ref_sr, measure)
    final, peak = conform(data, sr, ref, ref_sr, level_ref=float(level_ref), measure=measure, ceiling_db=ceiling)
    return final, ref_sr, peak, float(level_ref), measure


def _pair_dubs(project: Project, cid: str, key: str, staged: dict[str, Path], post_latest: dict[str, dict[str, Any]],
               char_of: dict[str, str], follows: dict[str, Any]) -> dict[str, Path | None]:
    """The dub side of the character's post.match pair: the converted takes of this run before the match, and for
    its other lines the latest vc row (same conversion key) before its match (its wav when no match changed it)."""
    dubs: dict[str, Path | None] = dict(staged)
    for lid, row in post_latest.items():
        if lid in dubs or lid in follows or char_of.get(lid) != cid or (row.get("vc") or {}).get("model_key") != key:
            continue
        dubs[lid] = _abs(project, row.get("pre_match_wav") if (row.get("match") or {}).get("applied") else row.get("wav"))
    return dubs


def _finish(project: Project, line: dict[str, Any], row: dict[str, Any], source: Path, final: np.ndarray, sr: int,
            peak: float, level_ref: float, measure: str, cid: str, key: str, model: dict[str, Any],
            settings: dict[str, Any], ceiling: float, pitch_shift: int = 0, mode: str = "off",
            pool: voice.ChannelPool | None = None) -> dict[str, Any]:
    """post.match on a conformed converted take (`_conform_take`), vc/audio/<uid>.wav and its vc row."""
    extra: dict[str, Any] = {}
    if mode != "off":
        orig, o_sr = voice.original_audio(project, line, final.shape[0] / sr, sr)
        matched, m_peak, info = voice.channel_match(line, final, sr, orig, o_sr, mode, pool,
                                                    level_ref=level_ref, measure=measure, ceiling_db=ceiling)
        extra["match"] = info
        if m_peak is not None:
            pre = project.path(layout.VC_PRE_MATCH_DIR, f"{_uid(line)}.wav")
            write_wav(pre, final, sr, subtype="PCM_16")
            extra["pre_match_wav"] = project.rel(pre)
            final, peak = matched, m_peak
    out = project.path(layout.VC_AUDIO_DIR, f"{_uid(line)}.wav")
    write_wav(out, final, sr, subtype="PCM_16")
    mono = to_mono(final)
    bounds = speech_bounds(mono, sr)
    level_out = level_db(mono[bounds[0] : bounds[1]], sr, measure) if bounds else float("nan")
    new = {k: v for k, v in row.items() if k not in ("vc", "pre_vc_wav", "stage", "match", "pre_match_wav")}
    new.update({
        "wav": project.rel(out), "stage": "vc", "pre_vc_wav": project.rel(source),
        "peak_db": _rounded(peak), "lufs_out": _rounded(level_out),
        "frames": int(final.shape[0]), "dur_s": round(final.shape[0] / sr, 4),
        "vc": {"char_id": cid, "engine": rvc.ENGINE, "model_key": key, "trained": model.get("trained"),
               "index_rate": settings["index_rate"], "protect": settings["protect"], "f0_method": settings["f0_method"],
               "pitch": settings.get("pitch", "keep"), "pitch_shift_st": pitch_shift},
        **extra,
        "ts": now_iso(),
    })
    return new


# ---- registration ---------------------------------------------------------------------------
def _device_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default=None,
                        help="device for Applio (default: project compute.device, GPU when present)")
    parser.add_argument("--wait-lock", type=float, default=0.0, metavar="SECONDS",
                        help="wait for the project GPU lock instead of failing when it is busy")


def register(sub: Any, common: Callable[[argparse.ArgumentParser], None]) -> None:
    p = sub.add_parser("vc", help="RVC timbre transfer of the dubbed lines (stage vc; `vc train` trains the models)")
    common(p)
    p.add_argument("--chars", help="only these characters (char_id, comma-separated)")
    p.add_argument("--ids", help="only these line ids or uids (comma-separated)")
    p.add_argument("--index-rate", type=float, help="RVC index rate (default vc.index_rate 0.75)")
    p.add_argument("--protect", type=float, help="RVC consonant protection (default vc.protect 0.33)")
    p.add_argument("--pitch", choices=PITCH_MODES, default=None,
                   help="F0 shift: keep (the TTS's own F0), char (to the character's originals), line (to each "
                        "original line); default vc.pitch line")
    p.add_argument("--force", action="store_true", help="convert again even if up to date")
    p.add_argument("--dry-run", action="store_true", help="show what would be converted")
    _device_args(p)
    p.set_defaults(func=cmd_vc)
    actions = p.add_subparsers(dest="vc_action", metavar="<action>")
    t = actions.add_parser("train", help="train an RVC v2 model per character on its clean original lines")
    sub_common(t)
    t.add_argument("--chars", required=True, help="characters to train (char_id or name, comma-separated)")
    t.add_argument("--epochs", type=int, help="training epochs (default vc.epochs 20)")
    t.add_argument("--minutes", type=float, help="dataset size in minutes of speech (default vc.minutes 30)")
    t.add_argument("--force", action="store_true", help="train again even if a model exists")
    t.add_argument("--dry-run", action="store_true", help="show datasets without training")
    _device_args(t)
    t.set_defaults(func=cmd_vc_train)
