"""`babeldub analyze`: what the original lines really contain (stage between `link` and `cast`).

For every voiced line with audio (followers excluded) the qc worker measures the original:
Silero VAD speech segments, the ASR transcript with word times and confidences
(faster-whisper), prosody (F0 median and range, active RMS, voiced ratio) and, when the opt-in
emotion2vec+ model is installed, an emotion label and embedding. Rows go to
inventory/analysis.jsonl, vectors to inventory/analysis.npz. The CLI then derives the
syllable rate, the CER of the ASR against the subtitle and the line class (analysis/classes.py),
and sets the `subtitle_mismatch` flag in lines.jsonl (no other line field is touched).
Measurements are reused while the wav (size+mtime, then sha1) and the model set are unchanged;
derived fields are recomputed on every run, so a new subtitle or a re-run of `link` is picked up.
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from pathlib import Path
from typing import Any

from babeldub.analysis.classes import CLASSES, SUBTITLE_MISMATCH_CER, classify
from babeldub.analysis.store import (
    load_analysis,
    load_vectors,
    save_vectors,
    vector_key,
    write_analysis,
)
from babeldub.commands.project import csv_list, load_lines, src_text, voiced_lines
from babeldub.core import layout
from babeldub.core.ids import uid as uid_of
from babeldub.core.jsonl import append_jsonl, now_iso, write_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_OK, EXIT_USAGE, BabelDubError, clip, emit
from babeldub.core.project import Project
from babeldub.core.takes import follower_map
from babeldub.qa.metrics import cer
from babeldub.qa.qc import QcItemError, QcSession, asr_model_dir, emotion_model_dir
from babeldub.recon.scan import file_sha1
from babeldub.text.syllables import count_syllables

MISMATCH_FLAG = "subtitle_mismatch"
WORDY_CLASSES = ("speech", "short_word", "mixed")  # a subtitle mismatch only means something for words
FLUSH_EVERY = 25


def _wav_path(project: Project, line: dict[str, Any]) -> Path | None:
    wav = (line.get("audio") or {}).get("wav")
    if not wav or "no_audio" in (line.get("flags") or []):
        return None
    path = project.path(wav)
    return path if path.exists() else None


def _models_key(project: Project, with_emotion: bool) -> str:
    emotion = emotion_model_dir(project.data) if with_emotion else None
    return f"asr={asr_model_dir(project.data).name};emotion={emotion.name if emotion else 'none'}"


def _unchanged(row: dict[str, Any] | None, path: Path, models: str) -> tuple[bool, dict[str, Any]]:
    """(row still valid, wav identity {wav_size, wav_mtime_ns, wav_sha1}); sha1 only when size/mtime moved."""
    st = path.stat()
    ident: dict[str, Any] = {"wav_size": st.st_size, "wav_mtime_ns": st.st_mtime_ns}
    if not row or row.get("models") != models:
        return False, ident
    if row.get("wav_size") == st.st_size and row.get("wav_mtime_ns") == st.st_mtime_ns:
        ident["wav_sha1"] = row.get("wav_sha1")
        return True, ident
    ident["wav_sha1"] = file_sha1(path)
    return ident["wav_sha1"] == row.get("wav_sha1"), ident


def _lang_of(project: Project, line: dict[str, Any]) -> str | None:
    return (line.get("text") or {}).get("src_lang") or project.data.get("source_lang") or None


def measure(session: QcSession, project: Project, line: dict[str, Any], path: Path) -> tuple[dict[str, Any], Any]:
    """Worker measurements of one original: (row fields, emotion vector or None)."""
    key = line["id"]
    vad = session.vad(key, path)
    asr = session.asr_words(key, path, _lang_of(project, line))
    prosody = session.prosody(key, path)
    words = asr.get("words") or []
    probs = [float(w.get("prob") or 0.0) for w in words]
    row: dict[str, Any] = {
        "dur_s": vad.get("dur_s"),
        "vad_segments": vad.get("segments") or [],
        "speech_s": vad.get("speech_s") or 0.0,
        "asr_text": asr.get("text") or "",
        "asr_lang": asr.get("lang"),
        "asr_conf": round(sum(probs) / len(probs), 4) if probs else None,
        "asr_avg_logprob": asr.get("avg_logprob"),
        "asr_no_speech_prob": asr.get("no_speech_prob"),
        "words": words,
        "prosody": {k: prosody.get(k) for k in ("f0_median_hz", "f0_range_st", "rms_db", "voiced_ratio")},
        "emotion": None,
    }
    vector = None
    if session.has_emotion:
        emo = session.emotion(key, path)
        row["emotion"] = {"label": emo["label"], "score": emo["score"]}
        vector = emo["emb"] if emo["emb"].size else None
    return row, vector


def derive(row: dict[str, Any], line: dict[str, Any]) -> dict[str, Any]:
    """Fields computed from the measurements and the current line: syllable rate, CER vs subtitle, class."""
    text = src_text(line)
    lang = (line.get("text") or {}).get("src_lang") or row.get("asr_lang") or "en"
    speech_s = float(row.get("speech_s") or 0.0)
    spoken = text or row.get("asr_text") or ""
    syl = count_syllables(spoken, lang) if spoken else 0
    prosody = dict(row.get("prosody") or {})
    prosody["syl_rate"] = round(syl / speech_s, 2) if syl and speech_s >= 0.2 else None
    row["prosody"] = prosody
    row["asr_vs_sub_cer"] = round(cer(text, row.get("asr_text")), 4) if text else None
    cls, reason = classify(text, line.get("flags") or [], speech_s, row.get("asr_text"), row.get("asr_conf"))
    row["class"], row["class_reason"] = cls, reason
    row["subtitle_mismatch"] = bool(
        cls in WORDY_CLASSES and row["asr_vs_sub_cer"] is not None and row["asr_vs_sub_cer"] > SUBTITLE_MISMATCH_CER
    )
    return row


def _sync_flags(project: Project, lines: list[dict[str, Any]], analysis: dict[str, dict[str, Any]]) -> int:
    """Set/clear `subtitle_mismatch` in lines.jsonl for analyzed lines; returns how many lines changed."""
    changed = 0
    for line in lines:
        row = analysis.get(line["id"])
        if row is None:
            continue
        flags = list(line.get("flags") or [])
        want = bool(row.get("subtitle_mismatch"))
        if want and MISMATCH_FLAG not in flags:
            line["flags"] = [*flags, MISMATCH_FLAG]
            changed += 1
        elif not want and MISMATCH_FLAG in flags:
            line["flags"] = [f for f in flags if f != MISMATCH_FLAG]
            changed += 1
    if changed:
        write_jsonl(project.path(layout.LINES), lines, schema="babeldub.lines/1")
    return changed


def _select(project: Project, lines: list[dict[str, Any]], ids_arg: str | None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    follows = follower_map(lines)
    voiced = voiced_lines(project, lines)
    skipped = {"follows": 0, "no_audio": 0}
    by_id = {ln["id"]: ln for ln in voiced}
    if ids_arg:
        uid_to_id = {ln.get("uid") or uid_of(ln["id"]): ln["id"] for ln in voiced}
        want = [uid_to_id.get(t, t) for t in csv_list(ids_arg)]
        unknown = [t for t in want if t not in by_id]
        if unknown:
            raise BabelDubError("ids are not voiced lines of the project", code=EXIT_USAGE, unknown=clip(unknown))
        voiced = [by_id[t] for t in dict.fromkeys(want)]
    picked = []
    for line in voiced:
        if line["id"] in follows:
            skipped["follows"] += 1
        elif _wav_path(project, line) is None:
            skipped["no_audio"] += 1
        else:
            picked.append(line)
    return picked, skipped


def cmd_analyze(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    project.require_stage("link")
    t0 = time.time()
    lines = load_lines(project)
    picked, skipped = _select(project, lines, args.ids)
    analysis = load_analysis(project)
    with_emotion = emotion_model_dir(project.data) is not None
    models = _models_key(project, with_emotion)
    todo: list[tuple[dict[str, Any], Path, dict[str, Any]]] = []
    up_to_date = 0
    for line in picked:
        path = _wav_path(project, line)
        assert path is not None
        ok, ident = _unchanged(analysis.get(line["id"]), path, models)
        if ok and not args.force:
            analysis[line["id"]].update(ident)
            up_to_date += 1
        else:
            todo.append((line, path, ident))
    if args.limit is not None:
        todo = todo[: max(args.limit, 0)]

    errors: list[str] = []
    device = None
    warnings: list[str] = []
    measured = 0
    if todo:
        session = QcSession.open(
            project.root, project.data, need_asr=True, need_sv=False, need_emotion=with_emotion,
            device=args.device, wait_lock=float(args.wait_lock or 0.0), owner="analyze",
        )
        if not args.ids:  # a run over chosen lines leaves the stage status alone
            project.mark_stage("analyze", "running", to_analyze=len(todo))
        device, warnings = session.device, list(session.warnings)
        npz = project.path(layout.ANALYSIS_NPZ)
        vectors = load_vectors(npz)
        pending: list[dict[str, Any]] = []
        try:
            with session:
                for line, path, ident in todo:
                    try:
                        fields, vector = measure(session, project, line, path)
                    except QcItemError as exc:
                        errors.append(f"{line['id']}: {exc}")
                        continue
                    if not ident.get("wav_sha1"):
                        ident["wav_sha1"] = file_sha1(path)
                    row = {"id": line["id"], "uid": line.get("uid") or uid_of(line["id"]), "wav": project.rel(path),
                           **ident, "models": models, **fields, "ts": now_iso()}
                    analysis[line["id"]] = derive(row, line)
                    if vector is not None:
                        vectors[vector_key("emotion", row["uid"])] = vector
                    pending.append(analysis[line["id"]])
                    measured += 1
                    if len(pending) >= FLUSH_EVERY:  # crash safety: a re-run resumes from here
                        append_jsonl(project.path(layout.ANALYSIS), pending)
                        pending = []
        finally:
            if pending:
                append_jsonl(project.path(layout.ANALYSIS), pending)
            if vectors:
                save_vectors(npz, vectors)

    current = {ln["id"]: ln for ln in lines}
    analysis = {i: derive(r, current[i]) for i, r in analysis.items() if i in current}
    write_analysis(project, analysis)
    flags_changed = _sync_flags(project, lines, analysis)

    scope = {ln["id"] for ln in picked}
    rows = [r for i, r in analysis.items() if i in scope]
    classes = Counter(r.get("class") for r in rows)
    mismatch = [r["id"] for r in rows if r.get("subtitle_mismatch")]
    elapsed = round(time.time() - t0, 1)
    summary: dict[str, Any] = {
        "analyzed": measured,
        "up_to_date": up_to_date,
        "failed": len(errors),
        "lines": len(rows),
        "classes": {c: classes.get(c, 0) for c in CLASSES},
        "subtitle_mismatch": len(mismatch),
    }
    payload: dict[str, Any] = {
        **summary,
        "mismatch_ids": clip(mismatch),
        "flags_changed": flags_changed,
        "skipped": {k: v for k, v in skipped.items() if v},
        "emotion": "emotion2vec+" if with_emotion else "prosody only (emotion2vec+ is opt-in)",
        "elapsed_s": elapsed,
        "file": str(project.path(layout.ANALYSIS)),
    }
    if device:
        payload["device"] = device
    if warnings:
        payload["warnings"] = warnings
    if errors:
        payload["errors"] = clip(errors)
    remaining = sum(1 for ln in picked if ln["id"] not in analysis)
    if remaining:
        payload["remaining"] = summary["remaining"] = remaining
    code = EXIT_ATTENTION if errors or remaining else EXIT_OK
    if not args.ids:
        project.mark_stage("analyze", "attention" if errors or remaining else "done", **summary)
    payload["next"] = "`babeldub analyze` again for the remaining lines" if remaining else "babeldub cast"
    return emit(payload, code)


def register(sub: argparse._SubParsersAction, common: Any) -> None:
    p = sub.add_parser("analyze", help="VAD, ASR, prosody and line class of the original lines (qc worker)")
    p.add_argument("--ids", help="comma list of line ids or uids (default: every voiced line with audio)")
    p.add_argument("--limit", type=int, help="analyze at most N lines that need it")
    p.add_argument("--force", action="store_true", help="measure again even when the wav did not change")
    p.add_argument("--device", choices=("auto", "cuda", "cpu"), default=None,
                   help="device for the qc worker (default: project compute.device)")
    p.add_argument("--wait-lock", type=float, default=0.0, metavar="SECONDS",
                   help="wait for the project GPU lock instead of failing when it is busy")
    common(p)
    p.set_defaults(func=cmd_analyze)
