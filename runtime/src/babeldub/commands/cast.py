"""`babeldub cast`, `cast apply-labels`, `cast select`: who speaks, who gets dubbed, which references.

Characters come from speaker metadata (`speaker_hint`) first; lines without it are grouped
by voice (speaker embeddings from the qc worker, agglomerative clustering). For every
selected character the cleanest, most typical original lines become voice references, so
the dub keeps the hero's own timbre.
"""

from __future__ import annotations

import argparse
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from babeldub.cast.characters import (
    POLICIES,
    UNKNOWN_ID,
    apply_label,
    apply_policy,
    assign_tiers,
    carry_labels,
    char_id_for,
    hint_name,
    line_duration,
    line_text,
    match_char,
    new_character,
    render_casting_sheet,
    validate_label,
    voiced_lines,
    wav_path,
)
from babeldub.cast.cluster import (
    DEFAULT_THRESHOLD,
    EMBEDDINGS,
    UNCERTAIN_CONF,
    Embedder,
    EmbeddingStore,
    cluster_voices,
)
from babeldub.cast.refs import candidate_pool, install_refs, rank_candidates, ref_rows
from babeldub.core import layout
from babeldub.core.jsonl import latest_by, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_OK, EXIT_USAGE, BabelDubError, clip, emit
from babeldub.core.project import Project
from babeldub.qa.qc import QcSession, qc_missing

DEFAULT_PARAMS: dict[str, Any] = {"refs": 3, "min_ref_s": 3.0, "max_ref_s": 12.0}


def sub_common(parser: argparse.ArgumentParser) -> None:
    """--project/--pretty on a sub-action without overwriting values given before it."""
    parser.add_argument("--project", default=argparse.SUPPRESS, help="project folder or slug")
    parser.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS, help="indent JSON output")


def _device_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default=None,
                        help="device for speaker embeddings (default: project compute.device)")
    parser.add_argument("--wait-lock", type=float, default=0.0, metavar="SECONDS",
                        help="wait for the project GPU lock instead of failing when it is busy")
    parser.add_argument("--no-embed", action="store_true", help="do not start the qc worker even if it is installed")


def register(sub: Any, common: Callable[[argparse.ArgumentParser], None]) -> None:
    p = sub.add_parser("cast", help="characters, tiers, selection and voice references (casting sheet)")
    common(p)
    p.add_argument("--refs", type=int, default=DEFAULT_PARAMS["refs"], help="reference candidates per character (1..3)")
    p.add_argument("--min-ref-s", type=float, default=DEFAULT_PARAMS["min_ref_s"], help="shortest reference, seconds")
    p.add_argument("--max-ref-s", type=float, default=DEFAULT_PARAMS["max_ref_s"], help="longest reference, seconds")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help="voice clustering threshold: average cosine similarity to merge two groups")
    _device_args(p)
    p.add_argument("--force", action="store_true", help="re-copy reference files")
    p.set_defaults(func=cmd_cast)
    actions = p.add_subparsers(dest="cast_action", metavar="<action>")

    lab = actions.add_parser("apply-labels", help="merge cast/labels.agent.jsonl into characters.jsonl")
    sub_common(lab)
    lab.set_defaults(func=cmd_apply_labels)

    sel = actions.add_parser("select", help="change which characters get dubbed")
    sub_common(sel)
    group = sel.add_mutually_exclusive_group(required=True)
    group.add_argument("--chars", help="comma-separated character ids or names (policy=list)")
    group.add_argument("--policy", choices=("all", "main"), help="select all characters or the main ones")
    _device_args(sel)
    sel.set_defaults(func=cmd_select)


# ---- helpers ---------------------------------------------------------------------------------
def _load_lines(project: Project) -> list[dict[str, Any]]:
    return list(latest_by(read_jsonl(project.path(layout.LINES))).values())


def _load_chars(project: Project) -> list[dict[str, Any]]:
    rows = list(latest_by(read_jsonl(project.path(layout.CHARACTERS)), "char_id").values())
    if not rows:
        raise BabelDubError("no characters yet", code=EXIT_USAGE, hint="run `babeldub cast` first")
    return rows


def _sort_chars(chars: list[dict[str, Any]]) -> None:
    chars.sort(key=lambda c: (c["char_id"] == UNKNOWN_ID, -c["speech_s"], c["char_id"]))


def _uncertain(chars: list[dict[str, Any]]) -> list[str]:
    """Voice groups whose identity still needs a human/agent look (labels resolve them)."""
    out = []
    for c in chars:
        if c.get("labeled"):
            continue
        if c["char_id"] == UNKNOWN_ID:
            out.append(c["char_id"])
        elif c["source"] == "cluster" and c.get("conf") is not None and c["conf"] < UNCERTAIN_CONF:
            out.append(c["char_id"])
    return out


def _samples(root: Path, lines: list[dict[str, Any]], assign: dict[str, str], per_char: int = 8) -> dict[str, list[dict[str, Any]]]:
    """Example texts per character for the casting sheet: lines with text, 2-12 s first."""
    by_char: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    for line in lines:
        cid = assign.get(line["id"])
        if cid is None:
            continue
        text = line_text(line)
        dur = line_duration(root, line)
        rank = (0 if text else 1) + (0 if 2.0 <= dur <= 12.0 else 0.5)
        by_char.setdefault(cid, []).append((rank, {"id": line["id"], "text": text, "dur_s": dur}))
    out: dict[str, list[dict[str, Any]]] = {}
    for cid, items in by_char.items():
        items.sort(key=lambda t: (t[0], -t[1]["dur_s"], t[1]["id"]))
        out[cid] = [s for _, s in items[:per_char]]
    return out


def _opener(project: Project, args: argparse.Namespace) -> Callable[[], QcSession] | None:
    if getattr(args, "no_embed", False) or qc_missing(project.data, need_asr=False, need_sv=True):
        return None

    def open_session() -> QcSession:
        return QcSession.open(
            project.root,
            project.data,
            need_asr=False,
            need_sv=True,
            device=getattr(args, "device", None),
            wait_lock=float(getattr(args, "wait_lock", 0.0) or 0.0),
            owner="cast",
        )

    return open_session


def _pick_refs(
    project: Project,
    targets: list[dict[str, Any]],
    lines_by_char: dict[str, list[dict[str, Any]]],
    params: dict[str, Any],
    embedder: Embedder,
    force: bool,
) -> tuple[list[dict[str, Any]], list[str], dict[str, str]]:
    """Reference rows for the given characters; returns (rows, no_ref, relaxed{char_id: why})."""
    rows: list[dict[str, Any]] = []
    no_ref: list[str] = []
    relaxed: dict[str, str] = {}
    default_lang = project.data.get("source_lang")
    for c in targets:
        cid = c["char_id"]
        pool, relax = candidate_pool(project.root, lines_by_char.get(cid, []), params["min_ref_s"], params["max_ref_s"])

        def embed(cand: Any) -> Any:
            return embedder.embed_one(cand.line_id, cand.wav)

        ranked = rank_candidates(pool, embed if embedder.opener is not None or len(embedder.store) else None)
        chosen = ranked[: int(params["refs"])]
        if not chosen:
            no_ref.append(cid)
            folder = project.path(layout.REFS_DIR, cid)
            if folder.is_dir():
                shutil.rmtree(folder)
            continue
        if relax:
            relaxed[cid] = relax
        install_refs(project.root, cid, chosen, force=force)
        rows.extend(ref_rows(cid, chosen, default_lang))
    return rows, no_ref, relaxed


def _write_sheet(
    project: Project,
    chars: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    assign: dict[str, str],
    refs: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    cast_cfg = project.data.get("cast") or {}
    refs_by_char: dict[str, list[dict[str, Any]]] = {}
    for r in refs:
        refs_by_char.setdefault(r["char_id"], []).append(r)
    text = render_casting_sheet(
        game_name=str(project.data.get("game_name") or project.data.get("slug") or "game"),
        target_lang=str(project.data.get("target_lang") or "?"),
        policy=str(cast_cfg.get("policy", "main")),
        policy_list=[str(x) for x in cast_cfg.get("list") or []],
        main_share=float(cast_cfg.get("main_share", 0.7)),
        chars=chars,
        refs_by_char=refs_by_char,
        samples=_samples(project.root, lines, assign),
        uncertain=_uncertain(chars),
        warnings=warnings,
    )
    path = project.path(layout.CASTING_SHEET)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _cleanup_ref_dirs(project: Project, keep: set[str]) -> None:
    base = project.path(layout.REFS_DIR)
    if not base.is_dir():
        return
    for folder in base.iterdir():
        if folder.is_dir() and folder.name not in keep:
            shutil.rmtree(folder)


def _stage_summary(project: Project) -> dict[str, Any]:
    old = dict(project.progress().get("cast") or {})
    old.pop("status", None)
    old.pop("updated", None)
    return old


def _next_hint(uncertain: list[str], no_ref: list[str], has_clusters: bool) -> str:
    if uncertain or has_clusters:
        return "label voices in cast/labels.agent.jsonl (see cast/casting_sheet.md), then `babeldub cast apply-labels`"
    if no_ref:
        return "characters without references will use their own lines only (ref_strategy=line); check cast/casting_sheet.md"
    return "`babeldub translate plan`"


# ---- commands --------------------------------------------------------------------------------
def cmd_cast(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    project.require_stage("extract")
    if not 1 <= args.refs <= 3:
        raise BabelDubError("--refs must be between 1 and 3", code=EXIT_USAGE)
    if args.min_ref_s <= 0 or args.max_ref_s <= args.min_ref_s:
        raise BabelDubError("need 0 < --min-ref-s < --max-ref-s", code=EXIT_USAGE)
    if not 0.0 < args.threshold < 1.0:
        raise BabelDubError("--threshold must be between 0 and 1", code=EXIT_USAGE)
    cast_cfg = project.data.get("cast") or {}
    policy = str(cast_cfg.get("policy", "main"))
    if policy not in POLICIES:
        raise BabelDubError(f"unknown cast.policy '{policy}'", code=EXIT_USAGE, hint="`babeldub set cast.policy=main`")
    main_share = float(cast_cfg.get("main_share", 0.7))
    root = project.root
    lines = voiced_lines(_load_lines(project))
    if not lines:
        raise BabelDubError("no voiced lines with audio", code=EXIT_USAGE, hint="run `babeldub extract` first")
    params = {"refs": args.refs, "min_ref_s": args.min_ref_s, "max_ref_s": args.max_ref_s, "threshold": args.threshold}
    project.mark_stage("cast", "running")

    warnings: list[str] = []
    durations = {line["id"]: line_duration(root, line) for line in lines}
    groups: dict[str, dict[str, Any]] = {}
    unhinted: list[dict[str, Any]] = []
    for line in lines:
        name = hint_name(line)
        if name is None:
            unhinted.append(line)
            continue
        cid = char_id_for(name)
        groups.setdefault(cid, {"name": name, "ids": []})["ids"].append(line["id"])

    opener = _opener(project, args)
    if opener is None and not args.no_embed:
        missing = qc_missing(project.data, need_asr=False, need_sv=True)
        warnings.append(
            f"qc worker unavailable ({', '.join(missing)}): voices are not compared; "
            "run `babeldub setup --engines qc` for clustering and typical-timbre references"
        )
    embedder = Embedder(EmbeddingStore(project.path(EMBEDDINGS)), opener)
    chars: list[dict[str, Any]] = []
    assign_rows: list[dict[str, Any]] = []
    n_clusters = 0
    try:
        for cid, g in groups.items():
            ids = g["ids"]
            chars.append(new_character(cid, g["name"], "metadata", len(ids), sum(durations[i] for i in ids), 1.0))
            assign_rows += [{"id": i, "char_id": cid, "conf": 1.0} for i in ids]

        embs: dict[str, Any] = {}
        if unhinted and opener is not None:
            items = [(line["id"], wav) for line in unhinted if (wav := wav_path(root, line)) is not None]
            embs = embedder.embed(items)
        if embs:
            clusters = cluster_voices(embs, {k: durations[k] for k in embs}, threshold=args.threshold)
            clusters.sort(key=lambda cl: (-sum(durations[k] for k in cl.members), cl.members[0]))
            width = max(2, len(str(len(clusters))))
            for n, cl in enumerate(clusters, 1):
                cid = f"cluster_{n:0{width}d}"
                speech = sum(durations[k] for k in cl.members)
                chars.append(new_character(cid, cid, "cluster", len(cl.members), speech, cl.conf))
                assign_rows += [{"id": k, "char_id": cid, "conf": round(cl.member_cos[k], 3)} for k in cl.members]
            n_clusters = len(clusters)
        rest = [line for line in unhinted if line["id"] not in embs]
        if rest:
            speech = sum(durations[line["id"]] for line in rest)
            chars.append(new_character(UNKNOWN_ID, "unknown", "unknown", len(rest), speech, None))
            assign_rows += [{"id": line["id"], "char_id": UNKNOWN_ID, "conf": 0.0} for line in rest]
            if opener is not None:
                warnings.append(f"{len(rest)} lines without speaker could not be embedded: grouped as 'unknown'")
            else:
                warnings.append(f"{len(rest)} lines have no speaker in metadata: grouped as 'unknown'")

        previous = latest_by(read_jsonl(project.path(layout.CHARACTERS)), "char_id")
        carry_labels(chars, previous)
        assign_tiers(chars, main_share)
        unmatched = apply_policy(chars, policy, cast_cfg.get("list") or [])
        if unmatched:
            warnings.append(f"cast.list names not found among characters: {', '.join(unmatched)}")
        _sort_chars(chars)

        assign = {r["id"]: r["char_id"] for r in assign_rows}
        lines_by_char: dict[str, list[dict[str, Any]]] = {}
        for line in lines:
            lines_by_char.setdefault(assign[line["id"]], []).append(line)
        selected = [c for c in chars if c["selected"]]
        # the mixed `unknown` group has no single voice: its lines are dubbed from themselves (line refs)
        voiced = [c for c in selected if c["char_id"] != UNKNOWN_ID]
        refs, no_ref, relaxed = _pick_refs(project, voiced, lines_by_char, params, embedder, args.force)
        warnings += embedder.warnings
    finally:
        embedder.close()
    if embedder.errors:
        warnings.append(f"{len(embedder.errors)} files could not be embedded, e.g. {embedder.errors[0]}")
    for cid, why in relaxed.items():
        warnings.append(f"{cid}: no ideal reference, rules relaxed ({why})")

    _cleanup_ref_dirs(project, {r["char_id"] for r in refs})
    write_jsonl(project.path(layout.CHARACTERS), chars, schema="babeldub.characters/1")
    write_jsonl(project.path(layout.ASSIGN), assign_rows, schema="babeldub.assign/1")
    write_jsonl(project.path(layout.REFS), refs, schema="babeldub.refs/1")
    _write_sheet(project, chars, lines, assign, refs, warnings)

    uncertain = _uncertain(chars)
    attention = bool(uncertain or no_ref)
    summary = {
        "characters": len(chars),
        "selected": len(selected),
        "clusters": n_clusters,
        "uncertain": len(uncertain),
        "no_ref": len(no_ref),
        "embedded": embedder.computed,
        "params": params,
    }
    project.mark_stage("cast", "attention" if attention else "done", **summary)
    return emit(
        {
            "characters": len(chars),
            "selected": len(selected),
            "main": clip([c["name"] for c in chars if c["tier"] == "main"]),
            "uncertain": clip(uncertain),
            "no_ref": clip(no_ref),
            "clusters": n_clusters,
            "refs": len(refs),
            "sheet": layout.CASTING_SHEET,
            "warnings": clip(warnings, 5),
            "next": _next_hint(uncertain, no_ref, n_clusters > 0),
        },
        EXIT_ATTENTION if attention else EXIT_OK,
    )


def cmd_apply_labels(args: argparse.Namespace) -> int:
    project = Project.load(getattr(args, "project", None))
    project.require_stage("cast")
    chars = _load_chars(project)
    path = project.path(layout.CAST_LABELS_AGENT)
    if not path.exists():
        raise BabelDubError(
            f"{layout.CAST_LABELS_AGENT} not found",
            code=EXIT_USAGE,
            hint='write one JSON object per line: {"char_id", "name", "name_tgt", "gender", "age", "notes"}',
        )
    try:
        rows = read_jsonl(path)
    except ValueError as exc:
        raise BabelDubError(str(exc), code=EXIT_USAGE, hint="fix the JSON syntax of labels.agent.jsonl") from exc
    known = {c["char_id"] for c in chars}
    errors: list[str] = []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f"row {n}: not a JSON object")
            continue
        errors += [f"row {n}: {p}" for p in validate_label(row, known)]
    if errors:
        raise BabelDubError(
            f"{layout.CAST_LABELS_AGENT} is invalid; nothing was merged",
            code=EXIT_USAGE,
            errors=clip(errors),
            known=clip(sorted(known), 20),
        )
    by_id = {c["char_id"]: c for c in chars}
    updated = 0
    for row in rows:
        if apply_label(by_id[row["char_id"]], row):
            updated += 1
    _sort_chars(chars)
    write_jsonl(project.path(layout.CHARACTERS), chars, schema="babeldub.characters/1")
    lines = voiced_lines(_load_lines(project))
    assign = {r["id"]: r["char_id"] for r in read_jsonl(project.path(layout.ASSIGN))}
    refs = read_jsonl(project.path(layout.REFS))
    _write_sheet(project, chars, lines, assign, refs, [])
    uncertain = _uncertain(chars)
    no_ref = sorted({c["char_id"] for c in chars if c.get("selected")} - {r["char_id"] for r in refs} - {UNKNOWN_ID})
    status = "attention" if uncertain or no_ref else "done"
    project.mark_stage("cast", status, **{**_stage_summary(project), "uncertain": len(uncertain), "labeled": sum(1 for c in chars if c.get("labeled"))})
    return emit(
        {
            "updated": updated,
            "labeled": sum(1 for c in chars if c.get("labeled")),
            "uncertain": clip(uncertain),
            "sheet": layout.CASTING_SHEET,
        },
        EXIT_ATTENTION if uncertain else EXIT_OK,
    )


def cmd_select(args: argparse.Namespace) -> int:
    project = Project.load(getattr(args, "project", None))
    project.require_stage("cast")
    chars = _load_chars(project)
    if args.chars is not None:
        names = [t.strip() for t in args.chars.split(",") if t.strip()]
        if not names:
            raise BabelDubError("--chars is empty", code=EXIT_USAGE)
        unknown = [t for t in names if match_char(chars, t) is None]
        if unknown:
            raise BabelDubError(
                "unknown characters",
                code=EXIT_USAGE,
                unknown=unknown,
                known=clip([c["char_id"] for c in chars], 20),
                hint="use char_id or name from cast/casting_sheet.md",
            )
        policy = "list"
    else:
        names = []
        policy = args.policy
    apply_policy(chars, policy, names)
    cast_cfg = dict(project.data.get("cast") or {})
    cast_cfg["policy"] = policy
    if policy == "list":
        cast_cfg["list"] = names
    project.data["cast"] = cast_cfg
    project.save()

    refs = read_jsonl(project.path(layout.REFS))
    have = {r["char_id"] for r in refs}
    targets = [c for c in chars if c["selected"] and c["char_id"] not in have and c["char_id"] != UNKNOWN_ID]
    warnings: list[str] = []
    no_ref: list[str] = []
    new_rows: list[dict[str, Any]] = []
    if targets:
        params = {**DEFAULT_PARAMS, **(_stage_summary(project).get("params") or {})}
        lines = voiced_lines(_load_lines(project))
        assign = {r["id"]: r["char_id"] for r in read_jsonl(project.path(layout.ASSIGN))}
        lines_by_char: dict[str, list[dict[str, Any]]] = {}
        for line in lines:
            if line["id"] in assign:
                lines_by_char.setdefault(assign[line["id"]], []).append(line)
        embedder = Embedder(EmbeddingStore(project.path(EMBEDDINGS)), _opener(project, args))
        try:
            new_rows, no_ref, relaxed = _pick_refs(project, targets, lines_by_char, params, embedder, False)
            warnings += embedder.warnings
        finally:
            embedder.close()
        warnings += [f"{cid}: no ideal reference, rules relaxed ({why})" for cid, why in relaxed.items()]
        refs = refs + new_rows
        write_jsonl(project.path(layout.REFS), refs, schema="babeldub.refs/1")
    _sort_chars(chars)
    write_jsonl(project.path(layout.CHARACTERS), chars, schema="babeldub.characters/1")
    lines = voiced_lines(_load_lines(project))
    assign = {r["id"]: r["char_id"] for r in read_jsonl(project.path(layout.ASSIGN))}
    _write_sheet(project, chars, lines, assign, refs, warnings)
    selected = [c for c in chars if c["selected"]]
    missing_refs = sorted({c["char_id"] for c in selected} - {r["char_id"] for r in refs} - {UNKNOWN_ID})
    project.mark_stage(
        "cast",
        "attention" if (_uncertain(chars) or missing_refs) else "done",
        **{**_stage_summary(project), "selected": len(selected), "no_ref": len(missing_refs)},
    )
    return emit(
        {
            "policy": policy,
            "selected": clip([c["name"] for c in selected], 20),
            "count": len(selected),
            "new_refs": len(new_rows),
            "no_ref": clip(missing_refs),
            "warnings": clip(warnings, 5),
        },
        EXIT_ATTENTION if missing_refs else EXIT_OK,
    )

