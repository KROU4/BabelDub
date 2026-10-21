"""inventory/report.md: human-readable recon summary (in Russian, for the user and the agent)."""

from __future__ import annotations

from typing import Any

from babeldub.adapters.base import Asset

LEVEL_TEXT = {
    "A": "A — полностью автоматически: извлечение и нативная сборка без проприетарных программ",
    "B": "B — полуавтомат: извлечение автоматически, для сборки нужен инструмент, который ставит пользователь",
    "C": "C — только чтение/инструкции: сборку агент делает по справочнику вручную",
}
ROLE_TEXT = {"voice": "голос", "text": "текст", "audio": "аудио (музыка/эффекты)", "other": "прочее"}


def _size(n: int | None) -> str:
    if not n:
        return "—"
    value = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


def _esc(text: Any) -> str:
    return str(text).replace("|", "\\|")


def _engine_lines(engine: dict[str, Any]) -> list[str]:
    if engine.get("id") == "unknown":
        return [
            "Движок по сигнатурам **не определён**. Нужна ручная разведка по справочникам `skills/babeldub-recon/references`."
        ]
    head = f"**{engine.get('name')}**"
    if engine.get("version"):
        head += f" {engine['version']}"
    if engine.get("backend"):
        head += f", скриптовый бэкенд **{str(engine['backend']).upper()}**"
        if engine.get("metadata_version"):
            head += f" (metadata v{engine['metadata_version']})"
    if engine.get("variant"):
        head += f", вариант {engine['variant']}"
    out = [head + "."]
    aa = engine.get("addressables")
    if aa:
        line = f"Addressables {aa.get('version') or '?'}: каталог `{aa.get('catalog')}`, бандлов {aa.get('bundles')}"
        if aa.get("crc_entries"):
            line += f", ненулевой CRC у {aa.get('crc_nonzero')} из {aa.get('crc_entries')}"
            if aa.get("crc_nonzero"):
                line += " (изменённый бандл потребует обнулить `m_Crc` в каталоге)"
        out.append(line + ".")
    if engine.get("iostore") is not None:
        out.append(f"Паков: {engine.get('paks')}, IoStore: {'да' if engine.get('iostore') else 'нет'}.")
    if engine.get("pck"):
        out.append(f"PCK: `{engine['pck']}`.")
    if engine.get("evidence"):
        out.append("Признаки: " + ", ".join(f"`{_esc(e)}`" for e in engine["evidence"][:5]) + ".")
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
            line += f" (версия банков {item['bank_version']})"
        if item.get("evidence"):
            line += " — " + ", ".join(f"`{_esc(e)}`" for e in item["evidence"][:3])
        out.append(line)
    return out


def _asset_table(assets: list[Asset], limit: int = 40, short: bool = False) -> list[str]:
    """Markdown table of assets; `short` shows only file names (the folder is printed above)."""
    rows = ["| Ассет | Адаптер | Локаль | Уровень | Размер | Детали |", "|---|---|---|---|---|---|"]
    for asset in assets[:limit]:
        shown = asset.relpath.rsplit("/", 1)[-1] if short else asset.relpath
        info = asset.info or {}
        details = []
        for key in ("container", "kind", "media", "files", "sounds", "tables", "collections", "bank_id"):
            if info.get(key) not in (None, "", 0):
                details.append(f"{key}={info[key]}")
        if info.get("typetree") is False:
            details.append("нет TypeTree")
        rows.append(
            f"| `{_esc(shown)}` | {asset.adapter} | {asset.locale or '—'} | {asset.level} | "
            f"{_size(asset.size)} | {_esc(', '.join(details)) or '—'} |"
        )
    if len(assets) > limit:
        rows.append(f"| … ещё {len(assets) - limit} | | | | | |")
    return rows


def render_report(recon: dict[str, Any], assets: list[Asset], project: dict[str, Any]) -> str:
    game = project.get("game_name") or recon.get("game_dir")
    voice = [a for a in assets if a.role == "voice"]
    text = [a for a in assets if a.role == "text"]
    audio = [a for a in assets if a.role == "audio"]
    lines: list[str] = [
        f"# Разведка: {game}",
        "",
        f"- Папка игры: `{recon.get('game_dir')}`",
        f"- Дата: {recon.get('scanned')}",
        f"- Целевой язык: {project.get('target_lang') or '—'}",
        "",
        "## Движок",
        "",
        *_engine_lines(recon.get("engine", {})),
        "",
        "## Аудио-middleware",
        "",
        *_named_list(recon.get("middleware", []), "Не найдено (звук движка или отдельные файлы)."),
        "",
        "## Системы текста и локализации",
        "",
        *_named_list(recon.get("text_systems", []), "По сигнатурам не найдены."),
        "",
        "## Голосовые ассеты",
        "",
    ]
    if voice:
        lines += _asset_table(voice)
    else:
        lines.append(
            "Встроенные адаптеры голосовых ассетов **не нашли**. Озвучку придётся искать вручную по справочнику "
            "движка или использовать рантайм-подход."
        )
    lines += ["", "## Текстовые ассеты", ""]
    if text:
        locales = sorted({a.locale for a in text if a.locale})
        folders = sorted({a.relpath.rsplit("/", 1)[0] if "/" in a.relpath else "." for a in text})
        lines.append(f"Найдено {len(text)}; локали: {', '.join(locales) if locales else '—'}.")
        lines.append("Папки: " + ", ".join(f"`{_esc(f)}`" for f in folders[:5]) + ".")
        lines.append("")
        lines += _asset_table(text, limit=30, short=True)
    else:
        lines.append(
            "Не найдены. Исходный текст реплик можно получить распознаванием речи (ASR) на стадии link/qa."
        )
    if audio:
        lines += ["", "## Прочее аудио (музыка, эффекты)", "", f"Ассетов: {len(audio)} (в дубляж не идут)."]
    level = recon.get("level", "C")
    lines += ["", "## Уровень поддержки", "", f"**{LEVEL_TEXT.get(level, level)}**."]
    adapters = recon.get("adapters", {})
    if adapters:
        lines.append("")
        for name, rep in adapters.items():
            if "error" in rep:
                lines.append(f"- адаптер `{name}`: ошибка — {_esc(rep['error'])}")
            else:
                lines.append(f"- адаптер `{name}` (уровень {rep.get('level')}): ассетов {rep.get('assets')}")
    anticheat = recon.get("anticheat", [])
    warnings = recon.get("warnings", [])
    lines += ["", "## Предупреждения", ""]
    if anticheat:
        lines.append(
            "**Найден античит.** Менять файлы можно только в одиночных офлайн-играх; в онлайн-играх это грозит баном."
        )
    if warnings:
        lines += [f"- {_esc(w)}" for w in warnings]
    elif not anticheat:
        lines.append("Нет.")
    refs = recon.get("refs", {})
    lines += ["", "## Что читать дальше", ""]
    for ref in refs.get("recon", []):
        lines.append(f"- разведка: `{ref}`")
    for ref in refs.get("pack", []):
        lines.append(f"- сборка: `{ref}`")
    if not refs.get("recon") and not refs.get("pack"):
        lines.append("- `skills/babeldub-recon/SKILL.md` — общий порядок ручной разведки")
    stats = recon.get("stats", {})
    lines += [
        "",
        "## Статистика файлов",
        "",
        f"Файлов {stats.get('files')}, папок {stats.get('dirs')}, объём {_size(stats.get('bytes'))}"
        + (" (обход прерван по лимиту)" if stats.get("truncated") else "")
        + ".",
        "",
        "| Расширение | Файлов | Объём |",
        "|---|---|---|",
    ]
    for row in stats.get("top_ext", [])[:15]:
        lines.append(f"| `{row['ext']}` | {row['files']} | {_size(int(row['bytes']))} |")
    lines += ["", "Следующий шаг: `babeldub extract`, затем `babeldub link`.", ""]
    return "\n".join(lines)
