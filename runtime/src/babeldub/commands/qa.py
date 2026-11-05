"""`babeldub qa`, `decide apply`, `report`: automatic quality control of the dub.

For the latest post-processed file of every line: ASR in the target language -> CER against
the dub text (digits spelled out on both sides; for Russian also vowel-folded and by a second ASR,
GigaAM, the lowest counts); speaker similarity (ECAPA) of the dub to the hero's original line and to his
reference, judged against the character's own baseline (with a lower ratio for a dub in
another language than the original audio, and an absolute floor), for a short or atypical
original also relative to the original's own similarity; the identity margin (the
dub must sound like its own character more than like any other); duration ratio; peaks and
silence; naturalness (Distill-MOS or UTMOS22) against the median MOS of the character's own originals
(qa/mos_baseline.json); prosody similarity to the original (F0 range ratio and contour
correlation) with a flatness check and a register check of the median F0; onset/offset deviations
of fixed_length lines. Lines flagged `nonverbal` skip the voice, CER, MOS and prosody checks. When
post.match changed the take (`pre_match_wav`), the voice similarity, the identity margin, MOS and prosody
are measured on the take before the channel match (a radio chain must not count as another voice),
ASR/CER, levels and the channel distance `ltas_db` (informational) on the final file. Auto-decisions
follow project.json `qa` thresholds; agents add theirs via `decide apply`.
"""

from __future__ import annotations

import argparse
import hashlib
import statistics
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from babeldub.analysis.store import line_classes, load_analysis
from babeldub.audio.io import read_audio
from babeldub.cast.characters import UNKNOWN_ID, wav_path
from babeldub.cast.cluster import EMBEDDINGS, MERGE_THRESHOLD, Embedder, EmbeddingStore
from babeldub.commands.cast import sub_common
from babeldub.core import layout
from babeldub.core.ids import uid as uid_of
from babeldub.core.jsonl import (
    append_jsonl,
    latest_by,
    now_iso,
    read_jsonl,
    write_jsonl,
)
from babeldub.core.output import EXIT_ATTENTION, EXIT_OK, EXIT_USAGE, BabelDubError, clip, emit
from babeldub.core.project import Project
from babeldub.core.takes import follower_map, project_followers
from babeldub.qa.baseline import (
    BASELINE,
    MIN_SIM_S,
    MOS_BASELINE,
    MosBaseline,
    char_centroids,
    char_f0_hints,
    clean_original,
    compute_baselines,
    load_baseline,
    save_baseline,
    sim_ref_for,
)
from babeldub.qa.decisions import (
    DECISIONS,
    MIN_REL_ORIG_SIM,
    Thresholds,
    auto_decide,
    evaluate,
    needs_attention,
    qa_is_current,
    validate_agent_row,
)
from babeldub.qa.metrics import (
    audio_levels,
    cosine,
    duration_ratio,
    f0_shift_st,
    identity_margin,
    ltas_distance_db,
    prosody_similarity,
    voiced_seconds,
)
from babeldub.qa.qc import QcItemError, QcSession, cer_report

QA_SCHEMA = "babeldub.qa/1"
DECISIONS_SCHEMA = "babeldub.decisions/1"
FLUSH_EVERY = 25  # QA rows are appended in chunks, so an interrupted run keeps its progress


def _r(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _target_id(kind: str, cid: str, vec: np.ndarray) -> str:
    """`against` of a cached orig_sim: what it was measured against (`ref:<ref_id>` or `centroid`), the
    character and a sha1 of the embedding rounded to 4 decimals."""
    digest = hashlib.sha1(np.round(np.asarray(vec, dtype=np.float64), 4).tobytes()).hexdigest()[:12]
    return f"{kind}:{cid}:{digest}"


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
        if not qa_is_current(qa_row, post):
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


def _waiting(project: Project) -> int:
    """Lines `synth` left for bootstrap phase 2 (they wait for a target-language prompt)."""
    return int((project.progress().get("synth") or {}).get("bootstrap_wait") or 0)


def _next(state: dict[str, Any], waiting: int = 0) -> str:
    dec = state["decisions"]
    if dec.get("regen"):
        return "`babeldub synth --attempt-next`, then `babeldub post`, `babeldub vc` and `babeldub qa`"
    if waiting:
        return (f"`babeldub synth`: {waiting} lines wait for their character's target-language prompt "
                "(bootstrap phase 2), then `babeldub post`, `babeldub vc` and `babeldub qa`")
    if dec.get("shorten"):
        return "translator: shorten the dub text of lines with decision=shorten, then synth/post/qa them again"
    if state["attention"]:
        return "review attention lines in qa/qa.jsonl, write qa/decisions.agent.jsonl, run `babeldub decide apply`"
    if state["pending"]:
        return "`babeldub qa` (some lines are not checked yet)"
    return "`babeldub report`, then `babeldub build`"


def _ltas(root: Path, line: dict[str, Any], wav: Path | None) -> float | None:
    """Channel distance (dB) of the final take to its original line; None when either is missing or silent."""
    orig = wav_path(root, line) if line else None
    if wav is None or orig is None or not orig.exists():
        return None
    try:
        dub, dub_sr = read_audio(wav)
        ref, ref_sr = read_audio(orig)
    except (RuntimeError, OSError):
        return None
    return ltas_distance_db(dub, dub_sr, ref, ref_sr)


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
        uid_to_id = {r.get("uid"): i for i, r in post_latest.items() if r.get("uid")}
        uid_to_id.update({uid_of(i): i for i in post_latest})
        # accept short uids too, like synth/batch --ids do
        want = [uid_to_id.get(t, t) for t in (t.strip() for t in args.ids.split(",")) if t]
        followers = [t for t in want if t in follows]
        if followers:
            raise BabelDubError("these lines follow another take and are not checked", code=EXIT_USAGE,
                                follows={t: follows[t] for t in followers[:10]}, n_follows=len(followers),
                                hint="check their leaders; the build gives followers the leader's accepted audio")
        unknown = [t for t in want if t not in post_latest]
        if unknown:
            raise BabelDubError("ids are not in post/post.jsonl", code=EXIT_USAGE, unknown=clip(unknown))
        ids = want
    qa_latest = latest_by(read_jsonl(project.path(layout.QA)))
    to_check = [i for i in ids if args.force or not qa_is_current(qa_latest.get(i), post_latest[i])]
    if args.limit is not None:
        to_check = to_check[: max(args.limit, 0)]
    decisions_all = read_jsonl(project.path(layout.DECISIONS))
    dec_latest = latest_by(decisions_all)
    if not to_check:
        state = _state(post_latest, qa_latest, dec_latest)
        payload: dict[str, Any] = {"checked": 0, "attention": clip(state["attention"]), "pending": state["pending"],
                                   "decisions": state["decisions"], "next": _next(state, _waiting(project))}
        if len(post_all) > len(post_latest):
            payload["follows"] = len(post_all) - len(post_latest)
        return emit(payload, EXIT_ATTENTION if state["attention"] else EXIT_OK)

    translations = latest_by(read_jsonl(project.path(layout.TRANSLATIONS)))
    assign = {r["id"]: r["char_id"] for r in read_jsonl(project.path(layout.ASSIGN))}
    refs = [r for r in read_jsonl(project.path(layout.REFS)) if r.get("chosen")]
    chosen_ref = {r["char_id"]: r for r in refs}
    shortened = Counter(r["id"] for r in decisions_all if r.get("decision") == "shorten")
    lang = project.data.get("target_lang")
    chars = latest_by(read_jsonl(project.path(layout.CHARACTERS)), "char_id")
    same_voice = float(project.setting("cast", "merge_threshold", MERGE_THRESHOLD))
    analysis = load_analysis(project)
    classes = line_classes(analysis)
    short_attempts = int(project.setting("tts", "short_word_attempts", 2) or 2)

    session = QcSession.open(
        root, project.data, need_asr=True, need_sv=True, need_mos=True, need_asr2=True,
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
        # voices a dub must be told apart from: the checked characters plus every selected or main one
        voices = (needed | {cid for cid, c in chars.items() if c.get("selected") or c.get("tier") == "main"}) - {UNKNOWN_ID}
        for lid, cid in assign.items():
            if (cid in needed or cid in voices) and lid in lines:
                char_lines.setdefault(cid, []).append(lines[lid])
        baseline = compute_baselines(
            root, {cid: v for cid, v in char_lines.items() if cid in needed}, embedder,
            None if args.rebaseline else load_baseline(project.path(BASELINE)), force=args.rebaseline,
        )
        save_baseline(project.path(BASELINE), baseline)
        centroids = char_centroids(root, {cid: v for cid, v in char_lines.items() if cid in voices}, embedder)

        mos_base = MosBaseline(root, project.path(MOS_BASELINE), session, char_lines, rebaseline=args.rebaseline)
        f0_hints = char_f0_hints(analysis, assign)
        own_sims: dict[str, Any] = baseline.setdefault("lines", {})

        def orig_emb(line_id: str, min_s: float = MIN_SIM_S) -> np.ndarray | None:
            line = lines.get(line_id)
            wav = clean_original(root, line, min_s) if line else None
            return embedder.embed_one(line_id, wav) if wav is not None else None

        def ref_vec_of(ref: dict[str, Any]) -> np.ndarray | None:
            vec = orig_emb(ref["line_id"])
            if vec is None and (ref_wav := _resolve(root, ref.get("wav"))) is not None:
                vec = embedder.embed_one(f"ref:{ref['ref_id']}", ref_wav)
            return vec

        def voice_target(cid: str, lid: str) -> tuple[np.ndarray | None, str | None]:
            """The voice the relative SIM rule measures the original and the dub against: the chosen reference
            (another line), else the character's centroid; with its identity `against` (kind, character and a
            fingerprint of the embedding: a re-cast, a new reference or a moved centroid misses the cache)."""
            ref = chosen_ref.get(cid)
            if ref is not None and ref.get("line_id") != lid and (vec := ref_vec_of(ref)) is not None:
                return vec, _target_id(f"ref:{ref['ref_id']}", cid, vec)
            return (centroids[cid], _target_id("centroid", cid, centroids[cid])) if cid in centroids else (None, None)

        def orig_own_sim(lid: str, target: np.ndarray, against: str) -> float | None:
            """The original line's own SIM to `target` (any length; cached in qa/baseline.json `lines`)."""
            cached = own_sims.get(lid) or {}
            if cached.get("against") == against and cached.get("orig_sim") is not None:
                return float(cached["orig_sim"])
            vec = orig_emb(lid, 0.0)
            if vec is None:
                return None
            own_sims[lid] = {"orig_sim": round(cosine(vec, target), 4), "against": against}
            return float(own_sims[lid]["orig_sim"])

        for lid in to_check:
            post = post_latest[lid]
            attempt = _attempt(post)
            line = lines.get(lid) or {}
            cid = assign.get(lid, UNKNOWN_ID)
            dub_text = (translations.get(lid) or {}).get("dub_text")
            wav = _resolve(root, post.get("wav"))
            exists = wav is not None and wav.exists()
            pre_match = _resolve(root, post.get("pre_match_wav"))
            voice_wav = pre_match if pre_match is not None and pre_match.exists() else wav  # before post.match
            levels = None
            if exists:
                try:
                    levels = audio_levels(wav)
                except RuntimeError as exc:
                    errors.append(f"{lid}: unreadable post file ({exc})")
            asr_text: str | None = None
            asr_raw: str | None = None
            cer_v: float | None = None
            sims: dict[str, float] = {}
            synth: np.ndarray | None = None
            qc_error = False
            audible = levels is not None and levels.dur_s >= 0.1 and levels.active_rms_db >= th.silence_db
            if audible:
                assert wav is not None
                try:
                    heard = session.asr(lid, wav, lang, text=dub_text)
                    asr_text, asr_raw = heard["text"], heard["text_raw"]
                    synth = session.embed(f"{lid}#a{attempt}", voice_wav)
                except QcItemError as exc:
                    qc_error = True
                    errors.append(f"{lid}: {exc}")
                else:
                    own = orig_emb(lid)
                    if own is not None:
                        sims["line"] = cosine(synth, own)
                    ref = chosen_ref.get(cid)
                    if ref is not None and ref.get("line_id") != lid:
                        ref_vec = ref_vec_of(ref)
                        if ref_vec is not None:
                            sims["char"] = cosine(synth, ref_vec)
            sim = float(np.mean(list(sims.values()))) if sims else None
            sim_ref = sim_ref_for(baseline, cid)
            # short or atypical originals: the dub is also judged against the original's own SIM to the same voice
            orig_sim = dub_cos = None
            if synth is not None:
                target, against = voice_target(cid, lid)
                if target is not None and against is not None:
                    orig_sim = orig_own_sim(lid, target, against)
                    dub_cos = cosine(synth, target)
            orig_speech = (analysis.get(lid) or {}).get("speech_s") or (line.get("audio") or {}).get("dur_s")
            sim_rule = th.sim_rule(orig_speech, orig_sim, sim_ref)
            # relative: orig_sim > MIN_REL_ORIG_SIM > 0, so the ratio is defined
            sim_rel = dub_cos / orig_sim if sim_rule == "relative" and dub_cos is not None and orig_sim else None
            margin, closest = identity_margin(synth, cid, centroids, same_voice) if synth is not None else (None, None)
            nonverbal = "nonverbal" in (line.get("flags") or [])  # a scream: voice and words are not comparable
            cers: dict[str, Any] = {}
            asr2_text: str | None = None
            if asr_text is not None and dub_text:
                if not nonverbal:
                    assert wav is not None
                    asr2_text = session.asr2(f"{lid}#a{attempt}", wav)
                cers = cer_report(session, dub_text, asr_text, lang, asr2_text=asr2_text,
                                  disagree=th.max_asr_disagreement)
                cer_v = cers["cer"]
            mos_v: float | None = None
            pros_dub: dict[str, Any] | None = None
            pros_orig: dict[str, Any] | None = None
            if audible and not qc_error and not nonverbal:
                assert voice_wav is not None
                if session.has_mos:
                    try:
                        mos_v = session.mos(f"{lid}#a{attempt}", voice_wav)
                    except QcItemError:
                        mos_v = None
                hint = f0_hints.get(cid)  # both F0 passes are bounded around the character's voice
                try:
                    pros_dub = session.prosody(f"{lid}#a{attempt}", voice_wav, f0_hint=hint)
                except QcItemError:
                    pros_dub = None
                # any length: the register check needs short originals too
                orig_wav = clean_original(root, line, 0.0) if line and pros_dub is not None else None
                if orig_wav is not None:
                    try:
                        pros_orig = session.prosody(lid, orig_wav, f0_hint=hint)
                    except QcItemError:
                        pros_orig = None
                if pros_dub is not None and pros_orig is None:
                    pros_orig = (analysis.get(lid) or {}).get("prosody")
            psim = prosody_similarity(pros_dub, pros_orig)
            ltas = _ltas(root, line, wav) if audible else None
            dub_range = (pros_dub or {}).get("f0_range_st")
            orig_range = (pros_orig or {}).get("f0_range_st")
            dub_f0 = (pros_dub or {}).get("f0_median_hz")
            orig_f0 = (pros_orig or {}).get("f0_median_hz")
            voiced = (voiced_seconds(pros_dub, levels.dur_s if levels else None),
                      voiced_seconds(pros_orig, (line.get("audio") or {}).get("dur_s")))
            # the register check needs enough voiced speech on both sides: a median of a few frames is noise
            shift = f0_shift_st(dub_f0, orig_f0) if all(v is not None and v >= th.pitch_min_voiced_s
                                                        for v in voiced) else None
            orig_mos = mos_base.median(cid) if mos_v is not None else None
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
                margin=margin,
                nonverbal=nonverbal,
                low_mos=th.low_mos(mos_v, orig_mos),
                flat=th.flat(dub_range, orig_range),
                off_time=th.off_time(post.get("onset_deltas"), post.get("offset_deltas")),
                pitch=th.off_pitch(shift),
                sim_rule=sim_rule,
                sim_rel=sim_rel,
            )
            new_qa.append(
                {
                    "id": lid,
                    "attempt": attempt,
                    "char_id": cid,
                    "wav": post.get("wav"),
                    "src_key": post.get("src_key"),
                    "post_ts": post.get("ts"),
                    "asr_text": asr_text,
                    "asr_text_raw": asr_raw,
                    "cer": None if cer_v is None else round(cer_v, 4),
                    "cer_raw": _r(cers.get("cer_raw")),
                    "cer_folded": _r(cers.get("cer_folded")),
                    "asr2_text": asr2_text,
                    "cer_asr2": _r(cers.get("cer_asr2")),
                    "asr_disagree": cers.get("asr_disagree"),
                    "cer_skipped": "nonverbal" if nonverbal else None,
                    "sim": None if sim is None else round(sim, 4),
                    "sim_line": None if "line" not in sims else round(sims["line"], 4),
                    "sim_char": None if "char" not in sims else round(sims["char"], 4),
                    "sim_ref": round(sim_ref, 4),
                    "sim_ratio": th.sim_ratio,
                    "sim_threshold": round(th.sim_threshold(sim_ref), 4),
                    "sim_rule": sim_rule,
                    "orig_sim": _r(orig_sim),
                    "sim_rel": _r(sim_rel),
                    "sim_skipped": "nonverbal" if nonverbal else None,
                    "mos": _r(mos_v, 3),
                    "orig_mos": _r(orig_mos, 3),
                    "prosody_sim": _r(psim["sim"]),
                    "f0_range_st": _r(dub_range, 2),
                    "orig_f0_range_st": _r(orig_range, 2),
                    "contour_corr": _r(psim["contour_corr"]),
                    "f0_median_hz": _r(dub_f0, 1),
                    "orig_f0_median_hz": _r(orig_f0, 1),
                    "pitch_shift_st": _r(shift, 2),
                    "margin": None if margin is None else round(margin, 4),
                    "closest_char": closest,
                    "ratio": None if ratio is None else round(float(ratio), 4),
                    "dur_s": None if levels is None else round(levels.dur_s, 3),
                    "peak_db": None if levels is None else round(levels.peak_db, 2),
                    "ltas_db": _r(ltas, 2),
                    "voice_wav": post.get("pre_match_wav") if voice_wav is not wav else None,
                    "class": classes.get(lid),
                    "verdict": verdict,
                    "reasons": reasons,
                    "ts": now_iso(),
                }
            )
            prev = dec_latest.get(lid)
            if prev and prev.get("by") in ("agent", "human") and _attempt(prev) == attempt:
                continue  # a decision for this attempt was already made by an agent/human
            cls = classes.get(lid)
            auto = auto_decide(verdict, reasons, attempt, th, shortened.get(lid, 0), line_class=cls,
                               max_attempts=min(th.max_attempts, short_attempts) if cls == "short_word" else None)
            if auto.decision is not None:
                new_dec.append(
                    {"id": lid, "decision": auto.decision, "attempt": attempt, "by": "auto",
                     "note": auto.note, "attention": auto.attention, "ts": now_iso()}
                )
            if len(new_qa) - flushed[0] >= FLUSH_EVERY:
                flush()
        save_baseline(project.path(BASELINE), baseline)  # with the originals' own SIM measured on the way
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
    mos_values = [r["mos"] for r in new_qa if r.get("mos") is not None]
    pros_values = [r["prosody_sim"] for r in new_qa if r.get("prosody_sim") is not None]
    shifts = [r["pitch_shift_st"] for r in new_qa if r.get("pitch_shift_st") is not None]
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
            "mos_median": round(statistics.median(mos_values), 3) if mos_values else None,
            "prosody_median": round(statistics.median(pros_values), 4) if pros_values else None,
            "pitch": {"checked": len(shifts), "over": sum(1 for s in shifts if abs(s) > th.max_pitch_shift_st),
                      "median_abs_st": round(statistics.median(abs(s) for s in shifts), 2) if shifts else None},
            "sim_relative": sum(1 for r in new_qa if r.get("sim_rule") == "relative"),
            "asr2": sum(1 for r in new_qa if r.get("cer_asr2") is not None),
            "asr_disagree": sum(1 for r in new_qa if r.get("asr_disagree")),
            "device": session.device,
            "errors": clip(errors, 5),
            "warnings": clip(warnings, 3),
            "file": layout.QA,
            "next": _next(state, _waiting(project)),
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
    th = Thresholds.from_project(project.data)
    lang_note = "the dub is in another language than the original audio" if th.cross_lang else "same-language dub"

    per: dict[str, dict[str, Any]] = {}
    for lid, post in post_latest.items():
        cid = assign.get(lid, UNKNOWN_ID)
        agg = per.setdefault(cid, {"lines": 0, "checked": 0, "pass": 0, "fail": 0, "dec": Counter(),
                                   "attention": 0, "cer": [], "sim": []})
        agg["lines"] += 1
        qa_row = qa_latest.get(lid)
        if not qa_is_current(qa_row, post):
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
        "character's baseline (how similar the character's own original lines are to each other). Checks:",
        f"`sim ≥ max({th.sim_ratio} × sim_ref, {th.min_sim_abs})` ({lang_note}); both are line-to-line cosines. An original",
        f"with under {th.short_sim_s} s of speech, or below that bar itself, relaxes it (`sim_rule: relative`): the dub",
        f"passes at the bar, or when `sim_rel` (its cosine to the voice target / the original's own, `orig_sim`) ≥ {th.short_sim_ratio};",
        f"an `orig_sim` ≤ {MIN_REL_ORIG_SIM} keeps the absolute rule (`sim_rel` is null under it). Identity margin ≥ {th.min_identity_margin}",
        f"(the dub is closer to its own character's voice than to any other); CER ≤ {th.max_cer} (the lowest of Whisper,",
        "Whisper with Russian vowels folded, and GigaAM for Russian). Register: the dub's median F0 within",
        f"±{th.max_pitch_shift_st} semitones of the original line's (`pitch`, when both have ≥ {th.pitch_min_voiced_s} s voiced).",
        f"Naturalness: MOS ({project.setting('qa', 'mos_model') or 'utmos22'}) ≥ the character's originals' median − {th.mos_drop}; monotony: the dub's F0 range",
        f"≥ {th.min_f0_range_ratio} × the original's (originals moving ≥ {th.flat_min_orig_st} st); fixed_length lines: every",
        f"segment starts within ±{int(th.max_onset_s * 1000)} ms of the original and ends at most {int(th.max_onset_s * 1000)} ms late.",
        "Lines flagged `nonverbal` (screams, laughs) skip the voice, CER, MOS and prosody checks.",
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
