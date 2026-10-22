"""Recon commands: scan, extract, link (docs/dev/CONTRACT.md section 5)."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from babeldub.core import layout
from babeldub.core.output import EXIT_ATTENTION, EXIT_ENV, EXIT_OK, clip, emit
from babeldub.core.project import Project
from babeldub.recon.kinds import VOICED_KINDS

Common = Callable[[argparse.ArgumentParser], None]


def _csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


# ---- scan -------------------------------------------------------------------
def cmd_scan(args: argparse.Namespace) -> int:
    from babeldub.recon.scan import run_scan

    project = Project.load(args.project)
    project.mark_stage("scan", "running")
    try:
        _recon, _assets, summary = run_scan(project, force=args.force)
    except Exception as exc:
        project.mark_stage("scan", "failed", error=str(exc))
        raise
    attention = (
        not summary["voice_assets"]
        or bool(summary["anticheat"])
        or any(w.startswith("adapter ") for w in summary["warnings"])
    )
    status = "attention" if attention else "done"
    project.mark_stage(
        "scan",
        status,
        engine=summary["engine"],
        level=summary["level"],
        voice_assets=len(summary["voice_assets"]),
        text_assets=len(summary["text_assets"]),
        warnings=len(summary["warnings"]),
    )
    payload: dict[str, Any] = {
        "engine": summary["engine"],
        "middleware": summary["middleware"],
        "text_systems": summary["text_systems"],
        "voice_assets": clip(summary["voice_assets"], 6),
        "text_assets": clip(summary["text_assets"], 4),
        "counts": summary["counts"],
        "level": summary["level"],
        "warnings": clip(summary["warnings"], 6),
        "report": project.rel(project.path(layout.INVENTORY_REPORT)),
        "next": "babeldub extract"
        if summary["voice_assets"]
        else "read inventory/report.md: no voice assets found",
    }
    if summary["anticheat"]:
        payload["anticheat"] = summary["anticheat"]
    return emit(payload, EXIT_ATTENTION if attention else EXIT_OK)


# ---- extract ----------------------------------------------------------------
def cmd_extract(args: argparse.Namespace) -> int:
    from babeldub.recon.extract import run_extract

    project = Project.load(args.project)
    project.require_stage("scan")
    if not args.dry_run:
        project.mark_stage("extract", "running")
    try:
        summary, code = run_extract(
            project,
            kinds=_csv(args.kinds),
            limit=args.limit,
            ids=_csv(args.ids),
            locales=args.locales,
            force=args.force,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        if not args.dry_run:
            project.mark_stage("extract", "failed", error=str(exc))
        raise
    if not args.dry_run:
        status = {EXIT_OK: "done", EXIT_ATTENTION: "attention"}.get(code, "failed")
        project.mark_stage(
            "extract",
            status,
            lines=summary["lines"],
            minutes=summary["minutes"],
            failed=summary["failed"],
            texts=summary["texts"],
        )
    summary["warnings"] = clip(summary["warnings"], 5)
    summary["locales"] = clip(summary["locales"], 12)
    if code == EXIT_OK:
        summary["next"] = "babeldub link"
    elif code == EXIT_ENV:
        summary["next"] = "babeldub doctor"
    return emit(summary, code)


# ---- link -------------------------------------------------------------------
def cmd_link(args: argparse.Namespace) -> int:
    from babeldub.recon.link import run_link

    project = Project.load(args.project)
    project.require_stage("extract")
    summary = run_link(project, dry_run=args.dry_run)
    code = EXIT_ATTENTION if summary["unlinked"] else EXIT_OK
    if not args.dry_run:
        project.mark_stage(
            "link",
            "attention" if summary["unlinked"] else "done",
            linked=summary["linked"],
            unlinked=summary["unlinked"],
            with_official_target=summary["with_official_target"],
            src_lang=summary["src_lang"],
        )
    summary["next"] = (
        "babeldub analyze"
        if not summary["unlinked"]
        else "unlinked lines are flagged no_text and keep their original voice "
        "(see `babeldub query --where flags=no_text`), then `babeldub analyze`"
    )
    return emit(summary, code)


# ---- registration -----------------------------------------------------------
def register(sub: Any, common: Common) -> None:
    p = sub.add_parser(
        "scan", help="detect engine/middleware, find voice and text assets, write the inventory"
    )
    common(p)
    p.add_argument("--force", action="store_true", help="recompute sha1 of every voice/text asset")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser(
        "extract",
        help="list voice lines, decode originals to audio/orig/*.wav, read text tables",
        description=(
            "lines.jsonl gets the selected lines (kinds, voice locale, --ids, --limit): a run with --limit N "
            "makes an N-line trial project, a later run without it extends the project. Existing WAVs are reused."
        ),
    )
    common(p)
    p.add_argument(
        "--kinds", default=",".join(VOICED_KINDS), help="comma list of kinds or 'all' (default: %(default)s)"
    )
    p.add_argument("--limit", type=int, default=None, help="keep only the first N selected lines")
    p.add_argument("--ids", default=None, help="comma list of line ids")
    p.add_argument(
        "--locales",
        default=None,
        help="voice locales to dub from (e.g. en-US), 'all', or auto: one locale, preferring source_lang, then English",
    )
    p.add_argument("--force", action="store_true", help="decode again even if the WAV exists")
    p.add_argument("--dry-run", action="store_true", help="list and count only, write nothing")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("link", help="link lines with text rows (key -> normalized key -> file name)")
    common(p)
    p.add_argument("--dry-run", action="store_true", help="compute the summary without writing lines.jsonl")
    p.set_defaults(func=cmd_link)
