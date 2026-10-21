"""`babeldub extract`: list voice lines, decode originals to WAV, read text tables.

lines.jsonl holds the selected lines (kinds filter, voice locale, --ids, --limit) and is written
atomically; a trial run with --limit N yields a coherent N-line project, a later run without the
limit extends it. Decoding is idempotent: an existing audio/orig/<uid>.wav is reused unless
--force. Fields added by later stages (text, flags) survive a re-run.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

from babeldub.adapters.base import Adapter, Asset, get_adapter
from babeldub.core import layout
from babeldub.core.jsonl import latest_by, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_ENV, EXIT_OK, EXIT_USAGE, BabelDubError
from babeldub.core.project import Project
from babeldub.recon.langs import lang_of, locale_from_token

KEEP_FLAGS_FROM_STAGES = ("no_text", "duplicate", "alt_take", "nonverbal", "has_bed")


def _log(message: str) -> None:
    print(f"[extract] {message}", file=sys.stderr, flush=True)


def choose_locales(
    available: Counter[str], requested: str | None, source_lang: str | None
) -> set[str] | None:
    """Voice locales to keep (None = keep all). Auto: one source language, preferring source_lang, then English."""
    if requested and requested.strip().lower() == "all":
        return None
    if requested:
        wanted = [t.strip() for t in requested.split(",") if t.strip()]
        out: set[str] = set()
        for token in wanted:
            norm = locale_from_token(token) or token
            exact = {loc for loc in available if loc.lower() == norm.lower()}
            out |= exact or {loc for loc in available if lang_of(loc) == lang_of(norm)}
        if not out:
            raise BabelDubError(
                f"none of the requested voice locales {wanted} exist",
                code=EXIT_USAGE,
                hint=f"available: {', '.join(sorted(available)) or 'none'}",
            )
        return out
    if len(available) <= 1:
        return None
    for lang in [lang_of(source_lang), "en"]:
        if not lang:
            continue
        same = [loc for loc in available if lang_of(loc) == lang]
        if same:
            return {max(same, key=lambda loc: available[loc])}
    return {available.most_common(1)[0][0]}


def _merge_previous(line: dict[str, Any], old: dict[str, Any] | None) -> dict[str, Any]:
    if not old:
        return line
    if old.get("text") and not line.get("text"):
        line["text"] = old["text"]
    flags = list(line.get("flags") or [])
    for flag in old.get("flags") or []:
        if flag in KEEP_FLAGS_FROM_STAGES and flag not in flags:
            flags.append(flag)
    line["flags"] = flags
    return line


def _wav_rel(project: Project, out_dir: Path, wav: str) -> tuple[str, Path]:
    path = Path(wav)
    if not path.is_absolute():
        for base in (project.root, out_dir):
            if (base / path).exists():
                path = base / path
                break
    return project.rel(path), path


def run_extract(
    project: Project,
    kinds: list[str] | None,
    limit: int | None = None,
    ids: list[str] | None = None,
    locales: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    game_dir = project.game_dir
    assets = [Asset.from_row(r) for r in read_jsonl(project.path(layout.ASSETS)) if "asset_id" in r]
    if not assets:
        raise BabelDubError("inventory is empty", code=EXIT_USAGE, hint="run `babeldub scan` first")
    adapters: dict[str, Adapter] = {}
    warnings: list[str] = []

    def adapter_for(name: str) -> Adapter | None:
        if name not in adapters:
            try:
                adapters[name] = get_adapter(name)
            except Exception as exc:  # optional adapter module missing or broken
                warnings.append(f"adapter {name} unavailable: {type(exc).__name__}: {exc}")
                return None
        return adapters[name]

    # ---- list lines --------------------------------------------------------
    listed: list[tuple[Asset, dict[str, Any]]] = []
    for asset in assets:
        if asset.role != "voice":
            continue
        adapter = adapter_for(asset.adapter)
        if adapter is None:
            continue
        try:
            for line in adapter.list_lines(game_dir, asset):
                listed.append((asset, line))
        except Exception as exc:
            warnings.append(f"{asset.asset_id}: listing failed: {type(exc).__name__}: {exc}")
    available = Counter(line["locale"] for _, line in listed if line.get("locale"))
    keep_locales = choose_locales(available, locales, project.data.get("source_lang"))
    kind_set = None if not kinds or "all" in kinds else set(kinds)
    skipped: Counter[str] = Counter()
    selected: list[tuple[Asset, dict[str, Any]]] = []
    id_set = set(ids) if ids else None
    for asset, line in listed:
        if keep_locales is not None and line.get("locale") and line["locale"] not in keep_locales:
            skipped["locale"] += 1
            continue
        if kind_set is not None and line.get("kind") not in kind_set:
            skipped["kind"] += 1
            continue
        if id_set is not None and line["id"] not in id_set:
            skipped["ids"] += 1
            continue
        selected.append((asset, line))
    if limit is not None and len(selected) > limit:
        skipped["limit"] += len(selected) - limit
        selected = selected[:limit]
    previous = latest_by(read_jsonl(project.path(layout.LINES)))
    out_dir = project.path(layout.ORIG_AUDIO_DIR)

    # ---- decode ------------------------------------------------------------
    to_decode: dict[str, list[dict[str, Any]]] = {}
    asset_by_id = {a.asset_id: a for a in assets}
    reused = 0
    lines: list[dict[str, Any]] = []
    for asset, line in selected:
        line = _merge_previous(dict(line), previous.get(line["id"]))
        line.setdefault("audio", {})
        line["audio"] = dict(line["audio"] or {})
        wav = out_dir / f"{line['uid']}.wav"
        if wav.exists() and not force:
            line["audio"]["wav"] = project.rel(wav)
            line["flags"] = [f for f in line.get("flags") or [] if f != "no_audio"]
            reused += 1
        else:
            to_decode.setdefault(asset.asset_id, []).append(line)
        lines.append(line)
    decoded = 0
    failed: list[str] = []
    env_error: BabelDubError | None = None
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        for aid, group in to_decode.items():
            asset = asset_by_id[aid]
            adapter = adapter_for(asset.adapter)
            results: dict[str, dict[str, Any]] = {}
            if adapter is not None:
                _log(f"{aid}: decoding {len(group)} lines")
                try:
                    results = {r["id"]: r for r in adapter.extract_audio(game_dir, asset, group, out_dir)}
                except BabelDubError as err:
                    if err.code == EXIT_ENV:
                        env_error = env_error or err
                    warnings.append(f"{aid}: extraction failed: {err}")
                except Exception as exc:
                    warnings.append(f"{aid}: extraction failed: {type(exc).__name__}: {exc}")
            for line in group:
                res = results.get(line["id"])
                flags = [f for f in line.get("flags") or [] if f != "no_audio"]
                if res and not res.get("error") and res.get("wav"):
                    rel, path = _wav_rel(project, out_dir, str(res["wav"]))
                    if path.exists():
                        line["audio"]["wav"] = rel
                        if res.get("dur_s"):
                            line["audio"]["dur_s"] = res["dur_s"]
                        line["flags"] = flags
                        decoded += 1
                        continue
                line["audio"].pop("wav", None)
                line["flags"] = flags + ["no_audio"]
                failed.append(line["id"])

    # ---- texts -------------------------------------------------------------
    texts: list[dict[str, Any]] = []
    for asset in assets:
        if asset.role != "text":
            continue
        adapter = adapter_for(asset.adapter)
        if adapter is None:
            continue
        try:
            texts.extend(adapter.read_texts(game_dir, asset))
        except Exception as exc:
            warnings.append(f"{asset.asset_id}: reading texts failed: {type(exc).__name__}: {exc}")
    dedup: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for row in texts:
        dedup[(row.get("key"), row.get("table"), row.get("locale"))] = row
    texts = list(dedup.values())
    if not dry_run:
        write_jsonl(project.path(layout.TEXTS), texts, schema="babeldub.texts/1")
        write_jsonl(project.path(layout.LINES), lines, schema="babeldub.lines/1")

    by_kind = Counter(line.get("kind") or "unknown" for line in lines)
    seconds = sum(float((line.get("audio") or {}).get("dur_s") or 0) for line in lines)
    text_locales = sorted({str(r.get("locale")) for r in texts if r.get("locale")})
    summary: dict[str, Any] = {
        "lines": len(lines),
        "by_kind": dict(by_kind),
        "minutes": round(seconds / 60, 1),
        "decoded": decoded,
        "reused": reused,
        "failed": len(failed),
        "failed_ids": failed[:5],
        "texts": len(texts),
        "text_keys": len({r.get("key") for r in texts}),
        "locales": text_locales,
        "voice_locales": sorted(keep_locales) if keep_locales is not None else sorted(available),
        "voice_locales_available": dict(available),
        "skipped": dict(skipped),
        "warnings": warnings,
    }
    if dry_run:
        summary["dry_run"] = True
        summary["would_decode"] = sum(len(g) for g in to_decode.values())
    if env_error is not None:
        summary["error"] = str(env_error)
        if env_error.hint:
            summary["hint"] = env_error.hint
        return summary, EXIT_ENV
    if not lines or failed or warnings:
        return summary, EXIT_ATTENTION
    return summary, EXIT_OK
