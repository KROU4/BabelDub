"""`babeldub qa`, `decide apply`, `report`: automatic quality control of the dub.

For the latest post-processed file of every line: ASR in the target language -> CER against
the dub text; speaker similarity (ECAPA) of the dub to the hero's original line and to his
reference, judged against the character's own baseline; duration ratio; peaks and silence.
Auto-decisions follow project.json `qa` thresholds; agents add theirs via `decide apply`.
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from babeldub.cast.characters import UNKNOWN_ID
from babeldub.cast.cluster import EMBEDDINGS, Embedder, EmbeddingStore
from babeldub.commands.cast import sub_common
from babeldub.core import layout
from babeldub.core.jsonl import append_jsonl, latest_by, now_iso, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_OK, EXIT_USAGE, BabelDubError, clip, emit
from babeldub.core.project import Project
from babeldub.core.takes import follower_map, project_followers
from babeldub.qa.baseline import (
    BASELINE,
    clean_original,
    compute_baselines,
    load_baseline,
    save_baseline,
    sim_ref_for,
)
from babeldub.qa.decisions import (
    DECISIONS,
    Thresholds,
    auto_decide,
    evaluate,
    needs_attention,
    validate_agent_row,
)
from babeldub.qa.metrics import audio_levels, cer, cosine, duration_ratio
from babeldub.qa.qc import QcItemError, QcSession

QA_SCHEMA = "babeldub.qa/1"
DECISIONS_SCHEMA = "babeldub.decisions/1"
FLUSH_EVERY = 25  # QA rows are appended in chunks, so an interrupted run keeps its progress


def register(sub: Any, common: Callable[[argparse.ArgumentParser], None]) -> None:
    p = sub.add_parser("qa", help="automatic QA: ASR/CER, voice similarity to the original, length, peaks")
    common(p)
    p.add_argument("--ids", help="comma-separated line ids (default: every post-processed line)")
    p.add_argument("--limit", type=int, help="check at most N lines")
    p.add_argument("--force", action="store_true", help="re-check lines already checked for their attempt")
    p.add_argument("--rebaseline", action="store_true", help="recompute qa/baseline.json")
    p.add_argument("--device", choices=("auto", "cuda", "cpu"), default=None,
                   help="device of the qc worker (default: project compute.device)")
    p.add_argument("--wait-lock", type=float, default=0.0, metavar="SECONDS",
                   help="wait for the project GPU lock instead of failing when it is busy")
    p.set_defaults(func=cmd_qa)

    d = sub.add_parser("decide", help="agent/human decisions on QA results")
    common(d)
    dsub = d.add_subparsers(dest="decide_action", metavar="<action>")
    dsub.required = True
    apply = dsub.add_parser("apply", help="validate and merge qa/decisions.agent.jsonl")
    sub_common(apply)
    apply.add_argument("--by", choices=("agent", "human"), default="agent", help="who made these decisions")
    apply.set_defaults(func=cmd_decide_apply)

    r = sub.add_parser("report", help="write qa/report.md with results per character")
    common(r)
    r.set_defaults(func=cmd_report)


# ---- helpers ---------------------------------------------------------------------------------
def _resolve(root: Path, rel: str | None) -> Path | None:
    if not rel:
        return None
    p = Path(rel)
    return p if p.is_absolute() else root / p


def _append(path: Path, rows: list[dict[str, Any]], schema: str) -> None:
    if not rows:
        return
    if path.exists():
        append_jsonl(path, rows)
    else:
        write_jsonl(path, rows, schema=schema)


def _attempt(row: dict[str, Any] | None) -> int:
    try:
        return int((row or {}).get("attempt") or 1)
    except (TypeError, ValueError):
        return 1


def _without_followers(post: dict[str, dict[str, Any]], follows: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Alt takes/duplicates are not checked: the build gives them their leader's accepted audio."""
    return {lid: row for lid, row in post.items() if lid not in follows}


def _is_current(qa_row: dict[str, Any] | None, post_row: dict[str, Any]) -> bool:
    return bool(qa_row) and _attempt(qa_row) == _attempt(post_row) and qa_row.get("wav") == post_row.get("wav")


def _state(
    post_latest: dict[str, dict[str, Any]],
    qa_latest: dict[str, dict[str, Any]],
    dec_latest: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Outstanding state over every post-processed line (latest attempt only)."""
    attention: list[str] = []
    pending = 0
    decisions: Counter[str] = Counter()
    for lid, post in post_latest.items():
        qa_row = qa_latest.get(lid)
        if not _is_current(qa_row, post):
            pending += 1
            continue
        dec = dec_latest.get(lid)
        if dec is not None and _attempt(dec) != _attempt(qa_row):
            dec = None
        if dec is not None:
            decisions[dec["decision"]] += 1
        if needs_attention(dec, qa_row.get("verdict")):
            attention.append(lid)
    return {"attention": attention, "pending": pending, "decisions": dict(decisions)}


def _next(state: dict[str, Any]) -> str:
    dec = state["decisions"]
    if dec.get("regen"):
        return "`babeldub synth --attempt-next`, then `babeldub post` and `babeldub qa`"
    if dec.get("shorten"):
        return "translator: shorten the dub text of lines with decision=shorten, then synth/post/qa them again"
    if state["attention"]:
        return "review attention lines in qa/qa.jsonl, write qa/decisions.agent.jsonl, run `babeldub decide apply`"
    if state["pending"]:
        return "`babeldub qa` (some lines are not checked yet)"
    return "`babeldub report`, then `babeldub build`"


# ---- qa ----------------------------------------------------------------------------------------
def cmd_qa(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    project.require_stage("post")
    root = project.root
    th = Thresholds.from_project(project.data)
    lines = latest_by(read_jsonl(project.path(layout.LINES)))
    follows = follower_map(lines.values())
    post_all = latest_by(read_jsonl(project.path(layout.POST)))
    post_latest = _without_followers(post_all, follows)
    if not post_latest:
        raise BabelDubError("post/post.jsonl has no lines", code=EXIT_USAGE, hint="run `babeldub post` first")
    ids = list(post_latest)
    if args.ids:
        want = [t.strip() for t in args.ids.split(",") if t.strip()]
        followers = [t for t in want if t in follows]
        if followers:
            raise BabelDubError("these lines follow another take and are not checked", code=EXIT_USAGE,
                                follows={t: follows[t] for t in clip(followers)},
                                hint="check their leaders; the build gives followers the leader's accepted audio")
        unknown = [t for t in want if t not in post_latest]
        if unknown:
            raise BabelDubError("ids are not in post/post.jsonl", code=EXIT_USAGE, unknown=clip(unknown))
        ids = want
    qa_latest = latest_by(read_jsonl(project.path(layout.QA)))
    to_check = [i for i in ids if args.force or not _is_current(qa_latest.get(i), post_latest[i])]
    if args.limit is not None:
        to_check = to_check[: max(args.limit, 0)]
    decisions_all = read_jsonl(project.path(layout.DECISIONS))
    dec_latest = latest_by(decisions_all)
    if not to_check:
        state = _state(post_latest, qa_latest, dec_latest)
        payload: dict[str, Any] = {"checked": 0, "attention": clip(state["attention"]), "pending": state["pending"],
                                   "decisions": state["decisions"], "next": _next(state)}
        if len(post_all) > len(post_latest):
            payload["follows"] = len(post_all) - len(post_latest)
        return emit(payload, EXIT_ATTENTION if state["attention"] else EXIT_OK)

    translations = latest_by(read_jsonl(project.path(layout.TRANSLATIONS)))
    assign = {r["id"]: r["char_id"] for r in read_jsonl(project.path(layout.ASSIGN))}
    refs = [r for r in read_jsonl(project.path(layout.REFS)) if r.get("chosen")]
    chosen_ref = {r["char_id"]: r for r in refs}
    shortened = Counter(r["id"] for r in decisions_all if r.get("decision") == "shorten")
    lang = project.data.get("target_lang")

    session = QcSession.open(
        root, project.data, need_asr=True, need_sv=True,
        device=args.device, wait_lock=float(args.wait_lock or 0.0), owner="qa",
    )
    project.mark_stage("qa", "running", to_check=len(to_check))  # only once the worker is up (EXIT_ENV leaves it)
    embedder = Embedder(EmbeddingStore(project.path(EMBEDDINGS)), lambda: session, owns_session=False)
    new_qa: list[dict[str, Any]] = []
    new_dec: list[dict[str, Any]] = []
    errors: list[str] = []
    flushed = [0, 0]  # rows of new_qa / new_dec already written

    def flush() -> None:
        _append(project.path(layout.QA), new_qa[flushed[0]:], QA_SCHEMA)
        _append(project.path(layout.DECISIONS), new_dec[flushed[1]:], DECISIONS_SCHEMA)
        flushed[0], flushed[1] = len(new_qa), len(new_dec)

    try:
        char_lines: dict[str, list[dict[str, Any]]] = {}
        needed = {assign.get(i, UNKNOWN_ID) for i in to_check}
        for lid, cid in assign.items():
            if cid in needed and lid in lines:
                char_lines.setdefault(cid, []).append(lines[lid])
        baseline = compute_baselines(
            root, char_lines, embedder, None if args.rebaseline else load_baseline(project.path(BASELINE)),
            force=args.rebaseline,
        )
        save_baseline(project.path(BASELINE), baseline)

        def orig_emb(line_id: str) -> np.ndarray | None:
            line = lines.get(line_id)
            wav = clean_original(root, line) if line else None
            return embedder.embed_one(line_id, wav) if wav is not None else None

        for lid in to_check:
            post = post_latest[lid]
            attempt = _attempt(post)
            line = lines.get(lid) or {}
            cid = assign.get(lid, UNKNOWN_ID)
            dub_text = (translations.get(lid) or {}).get("dub_text")
            wav = _resolve(root, post.get("wav"))
            exists = wav is not None and wav.exists()
            levels = None
            if exists:
                try:
                    levels = audio_levels(wav)
                except RuntimeError as exc:
                    errors.append(f"{lid}: unreadable post file ({exc})")
            asr_text: str | None = None
            cer_v: float | None = None
            sims: dict[str, float] = {}
            qc_error = False
            audible = levels is not None and levels.dur_s >= 0.1 and levels.active_rms_db >= th.silence_db
            if audible:
                assert wav is not None
                try:
                    asr_text = session.asr(lid, wav, lang)["text"]
                    synth = session.embed(f"{lid}#a{attempt}", wav)
                except QcItemError as exc:
                    qc_error = True
                    errors.append(f"{lid}: {exc}")
                else:
                    if dub_text:
                        cer_v = cer(dub_text, asr_text)
                    own = orig_emb(lid)
                    if own is not None:
                        sims["line"] = cosine(synth, own)
                    ref = chosen_ref.get(cid)
                    if ref is not None and ref.get("line_id") != lid:
                        ref_vec = orig_emb(ref["line_id"])
                        if ref_vec is None and (ref_wav := _resolve(root, ref.get("wav"))) is not None:
                            ref_vec = embedder.embed_one(f"ref:{ref['ref_id']}", ref_wav)
                        if ref_vec is not None:
                            sims["char"] = cosine(synth, ref_vec)
            sim = float(np.mean(list(sims.values()))) if sims else None
            sim_ref = sim_ref_for(baseline, cid)
            ratio = post.get("ratio")
            if ratio is None and levels is not None:
                target = post.get("target_s") or (line.get("audio") or {}).get("dur_s")
                ratio = duration_ratio(levels.dur_s, target)
            verdict, reasons = evaluate(
                th=th,
                exists=exists and levels is not None,
                dur_s=None if levels is None else levels.dur_s,
                active_rms_db=None if levels is None else levels.active_rms_db,
                peak_db=None if levels is None else levels.peak_db,
                clip_frac=None if levels is None else levels.clip_frac,
                has_text=bool(dub_text),
                cer=cer_v,
                sim=sim,
                sim_ref=sim_ref,
                ratio=ratio,
                fixed_length=bool(line.get("fixed_length")),
                qc_error=qc_error,
            )
            new_qa.append(
                {
                    "id": lid,
                    "attempt": attempt,
                    "char_id": cid,
                    "wav": post.get("wav"),
                    "asr_text": asr_text,
                    "cer": None if cer_v is None else round(cer_v, 4),
                    "sim": None if sim is None else round(sim, 4),
                    "sim_line": None if "line" not in sims else round(sims["line"], 4),
                    "sim_char": None if "char" not in sims else round(sims["char"], 4),
                    "sim_ref": round(sim_ref, 4),
                    "ratio": None if ratio is None else round(float(ratio), 4),
                    "dur_s": None if levels is None else round(levels.dur_s, 3),
                    "peak_db": None if levels is None else round(levels.peak_db, 2),
                    "verdict": verdict,
                    "reasons": reasons,
                    "ts": now_iso(),
                }
            )
            prev = dec_latest.get(lid)
            if prev and prev.get("by") in ("agent", "human") and _attempt(prev) == attempt:
                continue  # a decision for this attempt was already made by an agent/human
            auto = auto_decide(verdict, reasons, attempt, th, shortened.get(lid, 0))
            if auto.decision is not None:
                new_dec.append(
                    {"id": lid, "decision": auto.decision, "attempt": attempt, "by": "auto",
                     "note": auto.note, "attention": auto.attention, "ts": now_iso()}
                )
            if len(new_qa) - flushed[0] >= FLUSH_EVERY:
                flush()
        warnings = list(session.warnings)
    finally:
        flush()  # rows of finished lines survive a crash: a re-run continues where it stopped
        embedder.close()
        session.close()

    qa_latest.update(latest_by(new_qa))
    dec_latest.update(latest_by(new_dec))
    state = _state(post_latest, qa_latest, dec_latest)
    verdicts = Counter(r["verdict"] for r in new_qa)
    this_run = Counter(r["decision"] for r in new_dec)
    summary = {
        "checked": len(new_qa),
        "pass": verdicts.get("pass", 0),
        "fail": verdicts.get("fail", 0),
        "attention": len(state["attention"]),
        "pending": state["pending"],
    }
    attention = bool(state["attention"])
    project.mark_stage("qa", "attention" if attention else "done", **summary)
    cer_values = [r["cer"] for r in new_qa if r["cer"] is not None]
    sim_values = [r["sim"] for r in new_qa if r["sim"] is not None]
    if len(post_all) > len(post_latest):
        summary["follows"] = len(post_all) - len(post_latest)
    return emit(
        {
            **summary,
            "regen": this_run.get("regen", 0),
            "shorten": this_run.get("shorten", 0),
            "keep_original": this_run.get("keep_original", 0),
            "accept": this_run.get("accept", 0),
            "attention": clip(state["attention"]),
            "cer_median": round(statistics.median(cer_values), 4) if cer_values else None,
            "sim_median": round(statistics.median(sim_values), 4) if sim_values else None,
            "device": session.device,
            "errors": clip(errors, 5),
            "warnings": clip(warnings, 3),
            "file": layout.QA,
            "next": _next(state),
        },
        EXIT_ATTENTION if attention else EXIT_OK,
    )


# ---- decide apply ------------------------------------------------------------------------------
def cmd_decide_apply(args: argparse.Namespace) -> int:
    project = Project.load(getattr(args, "project", None))
    path = project.path(layout.DECISIONS_AGENT)
    if not path.exists():
        raise BabelDubError(
            f"{layout.DECISIONS_AGENT} not found",
            code=EXIT_USAGE,
            hint=f'one JSON object per line: {{"id", "decision" ({"|".join(DECISIONS)}), "attempt"?, "note"?}}',
        )
    try:
        rows = read_jsonl(path)
    except ValueError as exc:
        raise BabelDubError(str(exc), code=EXIT_USAGE, hint="fix the JSON syntax of decisions.agent.jsonl") from exc
    known = set(latest_by(read_jsonl(project.path(layout.LINES))))
    post_latest = latest_by(read_jsonl(project.path(layout.POST)))
    errors: list[str] = []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f"row {n}: not a JSON object")
            continue
        errors += [f"row {n}: {p}" for p in validate_agent_row(row, known)]
    if errors:
        raise BabelDubError(f"{layout.DECISIONS_AGENT} is invalid; nothing was applied", code=EXIT_USAGE, errors=clip(errors))
    existing = read_jsonl(project.path(layout.DECISIONS))
    seen = {(r.get("id"), r.get("decision"), r.get("attempt"), r.get("note"), r.get("by")) for r in existing}
    out: list[dict[str, Any]] = []
    for row in rows:
        attempt = row.get("attempt")
        if attempt is None and row["id"] in post_latest:
            attempt = _attempt(post_latest[row["id"]])
        key = (row["id"], row["decision"], attempt, row.get("note"), args.by)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {"id": row["id"], "decision": row["decision"], "attempt": attempt, "by": args.by,
             "note": row.get("note"), "attention": False, "ts": now_iso()}
        )
    _append(project.path(layout.DECISIONS), out, DECISIONS_SCHEMA)
    counts = Counter(r["decision"] for r in out)
    return emit({"applied": len(out), "skipped": len(rows) - len(out), "by_decision": dict(counts), "file": layout.DECISIONS})


# ---- report ------------------------------------------------------------------------------------
def _fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _cell(text: Any, limit: int = 70) -> str:
    if text is None:
        return "—"
    s = " ".join(str(text).split()).replace("|", "\\|")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def cmd_report(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    post_latest = _without_followers(latest_by(read_jsonl(project.path(layout.POST))), project_followers(project))
    qa_latest = latest_by(read_jsonl(project.path(layout.QA)))
    dec_latest = latest_by(read_jsonl(project.path(layout.DECISIONS)))
    if not qa_latest:
        raise BabelDubError("no QA results yet", code=EXIT_USAGE, hint="run `babeldub qa` first")
    chars = latest_by(read_jsonl(project.path(layout.CHARACTERS)), "char_id")
    assign = {r["id"]: r["char_id"] for r in read_jsonl(project.path(layout.ASSIGN))}
    translations = latest_by(read_jsonl(project.path(layout.TRANSLATIONS)))
    baseline = load_baseline(project.path(BASELINE))
    state = _state(post_latest, qa_latest, dec_latest)
    attention_set = set(state["attention"])

    per: dict[str, dict[str, Any]] = {}
    for lid, post in post_latest.items():
        cid = assign.get(lid, UNKNOWN_ID)
        agg = per.setdefault(cid, {"lines": 0, "checked": 0, "pass": 0, "fail": 0, "dec": Counter(),
                                   "attention": 0, "cer": [], "sim": []})
        agg["lines"] += 1
        qa_row = qa_latest.get(lid)
        if not _is_current(qa_row, post):
            continue
        agg["checked"] += 1
        agg[qa_row["verdict"]] += 1
        if qa_row.get("cer") is not None:
            agg["cer"].append(qa_row["cer"])
        if qa_row.get("sim") is not None:
            agg["sim"].append(qa_row["sim"])
        dec = dec_latest.get(lid)
        if dec is not None and _attempt(dec) == _attempt(qa_row):
            agg["dec"][dec["decision"]] += 1
        if lid in attention_set:
            agg["attention"] += 1

    total_checked = sum(a["checked"] for a in per.values())
    total_pass = sum(a["pass"] for a in per.values())
    out = [
        f"# QA report — {project.data.get('game_name') or project.slug}",
        "",
        f"Lines checked: **{total_checked}** of {len(post_latest)}, passed: **{total_pass}**, "
        f"need attention: **{len(state['attention'])}**, not checked yet: {state['pending']}.",
        "",
        "Voice similarity (`sim`) is the ECAPA cosine between the dub and the hero's original voice; `sim_ref` is the",
        "character's baseline (how similar the character's own original lines are to each other). Check:",
        f"`sim ≥ {project.setting('qa', 'min_sim_ratio', 0.75)} × sim_ref`, CER ≤ {project.setting('qa', 'max_cer', 0.15)}.",
        "",
        "| Character | ID | Lines | Checked | Passed | Failed | Accepted | Regenerate | Shorten | Keep original | Attention | CER (median) | sim (median) | sim_ref |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    order = sorted(per, key=lambda c: (c == UNKNOWN_ID, -per[c]["lines"], c))
    for cid in order:
        a = per[cid]
        name = (chars.get(cid) or {}).get("name") or cid
        out.append(
            f"| {_cell(name, 40)} | `{cid}` | {a['lines']} | {a['checked']} | {a['pass']} | {a['fail']} | "
            f"{a['dec'].get('accept', 0)} | {a['dec'].get('regen', 0)} | {a['dec'].get('shorten', 0)} | "
            f"{a['dec'].get('keep_original', 0)} | {a['attention']} | "
            f"{_fmt(statistics.median(a['cer']) if a['cer'] else None, 3)} | "
            f"{_fmt(statistics.median(a['sim']) if a['sim'] else None, 3)} | {_fmt(sim_ref_for(baseline, cid), 3)} |"
        )
    if state["attention"]:
        out += [
            "",
            "## Need a decision",
            "",
            "Write the decision to `qa/decisions.agent.jsonl` (`{\"id\", \"decision\", \"note\"}`), then run `babeldub decide apply`.",
            "",
            "| ID | Character | Attempt | Reasons | CER | sim | Length | Auto decision | Dub text | Recognized |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for lid in state["attention"][:30]:
            q = qa_latest[lid]
            dec = dec_latest.get(lid)
            dec_text = "—" if dec is None or _attempt(dec) != _attempt(q) else f"{dec['decision']}: {dec.get('note') or ''}"
            out.append(
                f"| `{lid}` | `{assign.get(lid, UNKNOWN_ID)}` | {_attempt(q)} | {', '.join(q.get('reasons') or [])} | "
                f"{_fmt(q.get('cer'), 3)} | {_fmt(q.get('sim'), 3)} | {_fmt(q.get('ratio'), 2)} | {_cell(dec_text, 60)} | "
                f"{_cell((translations.get(lid) or {}).get('dub_text'))} | {_cell(q.get('asr_text'))} |"
            )
        if len(state["attention"]) > 30:
            out.append(f"\n… and {len(state['attention']) - 30} more: see `qa/qa.jsonl`.")
    out.append("")
    path = project.path(layout.QA_REPORT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8", newline="\n")
    return emit({"file": layout.QA_REPORT, "lines": len(post_latest), "checked": total_checked,
                 "pass": total_pass, "attention": len(state["attention"]), "characters": len(per)})
