"""inventory/report.md: human-readable recon summary (for the user and the agent)."""

from __future__ import annotations

from typing import Any

from babeldub.adapters.base import Asset

LEVEL_TEXT = {
    "A": "A — fully automatic: extraction and native repacking without proprietary software",
    "B": "B — semi-automatic: extraction is automatic, repacking needs a tool that the user installs",
    "C": "C — read-only/instructions: the agent repacks by hand, following the reference",
}
ROLE_TEXT = {"voice": "voice", "text": "text", "audio": "audio (music/effects)", "other": "other"}


def _size(n: int | None) -> str:
    if not n:
        return "—"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _esc(text: Any) -> str:
    return str(text).replace("|", "\\|")


def _engine_lines(engine: dict[str, Any]) -> list[str]:
    if engine.get("id") == "unknown":
        return [
            "The engine was **not identified** by signatures. "
            "Do a manual recon with the references in `skills/babeldub-recon/references`."
        ]
    head = f"**{engine.get('name')}**"
    if engine.get("version"):
        head += f" {engine['version']}"
    if engine.get("backend"):
        head += f", scripting backend **{str(engine['backend']).upper()}**"
        if engine.get("metadata_version"):
            head += f" (metadata v{engine['metadata_version']})"
    if engine.get("variant"):
        head += f", variant {engine['variant']}"
    out = [head + "."]
    aa = engine.get("addressables")
    if aa:
        line = f"Addressables {aa.get('version') or '?'}: catalog `{aa.get('catalog')}`, bundles: {aa.get('bundles')}"
        if aa.get("crc_entries"):
            line += f", non-zero CRC in {aa.get('crc_nonzero')} of {aa.get('crc_entries')}"
            if aa.get("crc_nonzero"):
                line += " (a modified bundle requires zeroing `m_Crc` in the catalog)"
        out.append(line + ".")
    if engine.get("iostore") is not None:
        out.append(f"Paks: {engine.get('paks')}, IoStore: {'yes' if engine.get('iostore') else 'no'}.")
    if engine.get("pck"):
        out.append(f"PCK: `{engine['pck']}`.")
    if engine.get("evidence"):
        out.append("Evidence: " + ", ".join(f"`{_esc(e)}`" for e in engine["evidence"][:5]) + ".")
    return out


def _named_list(items: list[dict[str, Any]], empty: str) -> list[str]:
    if not items:
        return [empty]
    out = []
    for item in items:
        line = f"- **{item.get('name', item.get('id'))}**"
        if item.get("version"):
            line += f" {item['version']}"
        if item.get("bank_version"):
            line += f" (bank version {item['bank_version']})"
        if item.get("evidence"):
            line += " — " + ", ".join(f"`{_esc(e)}`" for e in item["evidence"][:3])
        out.append(line)
    return out


def _asset_table(assets: list[Asset], limit: int = 40, short: bool = False) -> list[str]:
    """Markdown table of assets; `short` shows only file names (the folder is printed above)."""
    rows = ["| Asset | Adapter | Locale | Level | Size | Details |", "|---|---|---|---|---|---|"]
    for asset in assets[:limit]:
        shown = asset.relpath.rsplit("/", 1)[-1] if short else asset.relpath
        info = asset.info or {}
        details = []
        for key in ("container", "kind", "media", "files", "sounds", "tables", "collections", "bank_id"):
            if info.get(key) not in (None, "", 0):
                details.append(f"{key}={info[key]}")
        if info.get("typetree") is False:
            details.append("no TypeTree")
        rows.append(
            f"| `{_esc(shown)}` | {asset.adapter} | {asset.locale or '—'} | {asset.level} | "
            f"{_size(asset.size)} | {_esc(', '.join(details)) or '—'} |"
        )
    if len(assets) > limit:
        rows.append(f"| … {len(assets) - limit} more | | | | | |")
    return rows


def render_report(recon: dict[str, Any], assets: list[Asset], project: dict[str, Any]) -> str:
    game = project.get("game_name") or recon.get("game_dir")
    voice = [a for a in assets if a.role == "voice"]
    text = [a for a in assets if a.role == "text"]
    audio = [a for a in assets if a.role == "audio"]
    lines: list[str] = [
        f"# Recon: {game}",
        "",
        f"- Game folder: `{recon.get('game_dir')}`",
        f"- Date: {recon.get('scanned')}",
        f"- Target language: {project.get('target_lang') or '—'}",
        "",
        "## Engine",
        "",
        *_engine_lines(recon.get("engine", {})),
        "",
        "## Audio middleware",
        "",
        *_named_list(recon.get("middleware", []), "None found (engine audio or loose files)."),
        "",
        "## Text and localization systems",
        "",
        *_named_list(recon.get("text_systems", []), "None found by signatures."),
        "",
        "## Voice assets",
        "",
    ]
    if voice:
        lines += _asset_table(voice)
    else:
        lines.append(
            "The built-in adapters found **no** voice assets. Find the voice-over by hand with the engine "
            "reference, or use the runtime approach."
        )
    lines += ["", "## Text assets", ""]
    if text:
        locales = sorted({a.locale for a in text if a.locale})
        folders = sorted({a.relpath.rsplit("/", 1)[0] if "/" in a.relpath else "." for a in text})
        lines.append(f"Found: {len(text)}; locales: {', '.join(locales) if locales else '—'}.")
        lines.append("Folders: " + ", ".join(f"`{_esc(f)}`" for f in folders[:5]) + ".")
        lines.append("")
        lines += _asset_table(text, limit=30, short=True)
    else:
        lines.append(
            "None found. The source text of the lines can be recovered with speech recognition (ASR) "
            "at the link/qa stage."
        )
    if audio:
        lines += ["", "## Other audio (music, effects)", "", f"Assets: {len(audio)} (not dubbed)."]
    level = recon.get("level", "C")
    lines += ["", "## Support level", "", f"**{LEVEL_TEXT.get(level, level)}**."]
    adapters = recon.get("adapters", {})
    if adapters:
        lines.append("")
        for name, rep in adapters.items():
            if "error" in rep:
                lines.append(f"- adapter `{name}`: error — {_esc(rep['error'])}")
            else:
                lines.append(f"- adapter `{name}` (level {rep.get('level')}): assets: {rep.get('assets')}")
    anticheat = recon.get("anticheat", [])
    warnings = recon.get("warnings", [])
    lines += ["", "## Warnings", ""]
    if anticheat:
        lines.append(
            "**Anti-cheat found.** Modify files only in single-player offline games; "
            "in online games this risks a ban."
        )
    if warnings:
        lines += [f"- {_esc(w)}" for w in warnings]
    elif not anticheat:
        lines.append("None.")
    refs = recon.get("refs", {})
    lines += ["", "## What to read next", ""]
    for ref in refs.get("recon", []):
        lines.append(f"- recon: `{ref}`")
    for ref in refs.get("pack", []):
        lines.append(f"- repacking: `{ref}`")
    if not refs.get("recon") and not refs.get("pack"):
        lines.append("- `skills/babeldub-recon/SKILL.md` — the general manual recon procedure")
    stats = recon.get("stats", {})
    lines += [
        "",
        "## File statistics",
        "",
        f"Files: {stats.get('files')}, folders: {stats.get('dirs')}, size: {_size(stats.get('bytes'))}"
        + (" (walk stopped at the limit)" if stats.get("truncated") else "")
        + ".",
        "",
        "| Extension | Files | Size |",
        "|---|---|---|",
    ]
    for row in stats.get("top_ext", [])[:15]:
        lines.append(f"| `{row['ext']}` | {row['files']} | {_size(int(row['bytes']))} |")
    lines += ["", "Next step: `babeldub extract`, then `babeldub link`.", ""]
    return "\n".join(lines)
