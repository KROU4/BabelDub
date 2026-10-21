"""Environment commands: doctor, setup, models list|fetch."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from babeldub.core.jsonl import now_iso, write_json
from babeldub.core.output import (
    EXIT_ATTENTION,
    EXIT_ENV,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    BabelDubError,
    clip,
    emit,
)
from babeldub.core.paths import IS_WINDOWS, babeldub_home
from babeldub.core.project import Project
from babeldub.envsetup import installer
from babeldub.envsetup.catalog import (
    TIERS,
    Catalog,
    ModelSpec,
    auto_models,
    engine_for_language,
    is_model_downloaded,
    load_catalog,
    model_dir,
    tier_for_vram,
)
from babeldub.envsetup.probe import (
    OPTIONAL_TOOLS,
    REQUIRED_TOOLS,
    envs_report,
    free_space_gb,
    gpu_report,
    home_warnings,
    system_info,
    tools_report,
)

Common = Callable[[argparse.ArgumentParser], None]

INSTALL_HINTS: dict[str, dict[str, str]] = {
    "uv": {"windows": "winget install astral-sh.uv", "linux": "curl -LsSf https://astral.sh/uv/install.sh | sh"},
    "ffmpeg": {"windows": "winget install Gyan.FFmpeg", "linux": "sudo apt install ffmpeg"},
    "ffprobe": {"windows": "winget install Gyan.FFmpeg", "linux": "sudo apt install ffmpeg"},
    "vgmstream-cli": {"windows": "babeldub setup --tools", "linux": "babeldub setup --tools"},
    "rubberband": {"windows": "winget install BreakfastQuay.RubberBand", "linux": "sudo apt install rubberband-cli"},
    "git": {"windows": "winget install Git.Git", "linux": "sudo apt install git"},
}
MIN_FREE_GB = 20.0


def _csv(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    return [part.strip() for item in items for part in item.split(",") if part.strip()]


def _try_project(project_arg: str | None) -> Project | None:
    try:
        return Project.load(project_arg)
    except BabelDubError:
        if project_arg:
            raise
        return None


def default_engines(catalog: Catalog, project: Project | None, lang: str | None) -> list[str]:
    """TTS engine of the project (or the first engine that speaks its language) + the QC engine."""
    tts = project.setting("tts", "engine") if project else None
    if tts not in catalog.engines or (lang and not catalog.engines[tts].supports(lang)):
        tts = engine_for_language(lang, catalog) if lang else "qwen3tts"
    names = [n for n in (tts, "qc") if n and n in catalog.engines and catalog.engines[n].env]
    return list(dict.fromkeys(names))


def _detect_tier() -> tuple[str, dict[str, Any] | None]:
    gpus = gpu_report()
    gpu = max(gpus, key=lambda g: g["vram_gb"]) if gpus else None
    return tier_for_vram(gpu["vram_gb"] if gpu else None), gpu


def _resolve_tier(arg: str | None, project: Project | None) -> tuple[str, dict[str, Any] | None]:
    wanted = arg or (project.setting("tts", "tier") if project else None) or "auto"
    detected, gpu = _detect_tier()
    if wanted == "auto":
        return detected, gpu
    if wanted not in TIERS:
        raise BabelDubError(f"unknown tier '{wanted}'", code=EXIT_USAGE, known=TIERS)
    return wanted, gpu


# ---- doctor -------------------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    project = _try_project(args.project)
    lang = project.target_lang if project else None
    tier, gpu = _detect_tier()
    home = babeldub_home()
    tools = tools_report()
    env_engines = [e for e in catalog.engines.values() if e.env]
    envs = envs_report(env_engines, import_check=not args.no_import_check)
    wanted_engines = default_engines(catalog, project, lang)
    wanted_models = auto_models(catalog, wanted_engines, tier, lang)
    downloaded = [m.name for m in catalog.models if is_model_downloaded(m)]
    free_gb = free_space_gb(home)

    missing = [t for t in REQUIRED_TOOLS if not tools[t]["found"]]
    optional_missing = [t for t in OPTIONAL_TOOLS if not tools[t]["found"]]
    to_install = [f"env:{n}" for n in wanted_engines if envs.get(n, {}).get("status") not in ("ok", "present")]
    to_install += [f"model:{m.name}" for m in wanted_models if m.name not in downloaded]
    warnings = home_warnings(home)
    if gpu is None:
        warnings.append("no NVIDIA GPU found: CPU tier, dubbing will be slow")
    elif tier == "8gb" and gpu["vram_gb"] < 7.5:
        warnings.append("less than 8 GB VRAM: prefer Qwen3-TTS-12Hz-0.6B-Base")
    if free_gb is not None and free_gb < MIN_FREE_GB:
        warnings.append(f"only {free_gb} GB free in {home}; engines and models need about {MIN_FREE_GB:.0f} GB")
    broken = [n for n, row in envs.items() if row.get("status") == "broken"]
    for name in broken:
        warnings.append(f"venv '{name}' does not import its packages: babeldub setup --engines {name} --force")

    os_key = "windows" if IS_WINDOWS else "linux"
    report = {
        "created": now_iso(),
        "home": str(home),
        "system": system_info(),
        "gpu": gpu,
        "tier": tier,
        "tools": tools,
        "envs": envs,
        "models": {m.name: {"downloaded": m.name in downloaded, "dir": str(model_dir(m))} for m in catalog.models},
        "wanted_engines": wanted_engines,
        "wanted_models": [m.name for m in wanted_models],
        "missing": missing,
        "optional_missing": optional_missing,
        "to_install": to_install,
        "warnings": warnings,
        "free_gb": free_gb,
    }
    report_path = installer.logs_dir() / "doctor.json"
    write_json(report_path, report)

    payload: dict[str, Any] = {
        "os": report["system"]["os"],
        "python": report["system"]["python"],
        "home": str(home),
        "free_gb": free_gb,
        "gpu": {k: gpu[k] for k in ("name", "vram_gb", "used_gb", "driver")} if gpu else None,
        "tier": tier,
        "tools": {name: (info.get("version") or True) if info["found"] else None for name, info in tools.items()},
        "envs": {name: row["status"] for name, row in envs.items()},
        "models": downloaded,
        "missing": missing,
        "optional_missing": optional_missing,
        "to_install": to_install,
    }
    if warnings:
        payload["warnings"] = clip(warnings, 5)
    payload["report"] = str(report_path)
    if missing:
        payload["install"] = {t: INSTALL_HINTS[t][os_key] for t in missing if t in INSTALL_HINTS}
        payload["next"] = "install the missing tools, then run `babeldub doctor` again"
        return emit(payload, EXIT_ENV)
    if to_install:
        payload["next"] = "babeldub setup --dry-run, then babeldub setup"
        return emit(payload, EXIT_ATTENTION)
    payload["next"] = "babeldub status" if project else "babeldub init <game_dir> --lang <code>"
    return emit(payload, EXIT_OK)


# ---- setup --------------------------------------------------------------------------------------


def _accepted(args: argparse.Namespace) -> set[str]:
    return set(_csv(getattr(args, "accept_license", None)))


def _select_models(catalog: Catalog, arg: str | None, engines: list[str], tier: str, lang: str | None,
                   accepted: set[str]) -> list[ModelSpec]:
    if not arg or arg == "none":
        return []
    if arg == "auto":
        return auto_models(catalog, engines, tier, lang, accepted)
    return [catalog.model(name) for name in _csv(arg)]


def _plan_payload(items: list[installer.PlanItem]) -> dict[str, Any]:
    need = round(sum(i.size_gb or 0 for i in items if i.action == "install"), 2)
    return {
        "planned": [i.summary() for i in items],
        "download_gb": need,
        "blocked": [i.name for i in items if i.action == "blocked"],
    }


def cmd_setup(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    project = _try_project(args.project)
    lang = args.lang or (project.target_lang if project else None)
    tier, gpu = _resolve_tier(args.tier, project)
    accepted = _accepted(args)
    explicit = bool(args.engines or args.models or args.tools)

    base_engines = default_engines(catalog, project, lang)
    engines = _csv(args.engines) if args.engines else ([] if explicit else base_engines)
    for name in engines:
        catalog.engine(name)  # validates the name
    models_arg = args.models or ("auto" if (not explicit or args.engines) else "none")
    models = _select_models(catalog, models_arg, engines or base_engines, tier, lang, accepted)
    tools = bool(args.tools) or not explicit

    items = installer.build_plan(catalog, engines, models, tools, tier, accepted, args.force,
                                 import_check=not args.no_import_check)
    plan_file = installer.write_plan_file(items, {"tier": tier, "lang": lang, "engines": engines,
                                                  "models": [m.name for m in models]})
    payload: dict[str, Any] = {"tier": tier, "lang": lang, "gpu": gpu["name"] if gpu else None,
                               **_plan_payload(items), "plan": str(plan_file)}
    free_gb = free_space_gb(babeldub_home())
    payload["free_gb"] = free_gb
    if args.dry_run:
        payload["dry_run"] = True
        payload["next"] = "babeldub setup" + _replay_flags(args)
        return emit(payload, EXIT_ATTENTION if payload["blocked"] else EXIT_OK)

    if free_gb is not None and payload["download_gb"] > free_gb:
        raise BabelDubError(
            f"not enough disk space: need ~{payload['download_gb']} GB, {free_gb} GB free in {babeldub_home()}",
            code=EXIT_ENV,
            hint="free some space or point BABELDUB_HOME to a larger disk",
        )
    results = installer.execute_plan(items, catalog, tier, args.force)
    payload["done"] = [r["name"] for r in results if r["status"] in ("installed", "adopted")]
    payload["skipped"] = [i.name for i in items if i.action == "skip"]
    failed = [r for r in results if r["status"] == "failed"]
    payload["failed"] = clip(failed, 5)
    payload.pop("planned")
    payload["planned"] = [i.name for i in items if i.action in ("install", "adopt")]
    if failed:
        payload["next"] = "read the log of the failed step, fix the cause, run `babeldub setup` again"
        return emit(payload, EXIT_ERROR)
    if payload["blocked"]:
        payload["next"] = "resolve the blocked items (see `reason` in the plan file), then run `babeldub setup`"
        return emit(payload, EXIT_ATTENTION)
    payload["next"] = "babeldub doctor"
    return emit(payload, EXIT_OK)


def _replay_flags(args: argparse.Namespace) -> str:
    parts = []
    for flag in ("engines", "models", "tier", "lang"):
        value = getattr(args, flag, None)
        if value:
            parts.append(f" --{flag} {value}")
    if args.tools:
        parts.append(" --tools")
    for name in sorted(_accepted(args)):
        parts.append(f" --accept-license {name}")
    return "".join(parts)


# ---- models -------------------------------------------------------------------------------------


def cmd_models_list(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    rows = []
    for spec in catalog.models:
        if args.engine and spec.engine != args.engine:
            continue
        row = spec.summary()
        row["downloaded"] = is_model_downloaded(spec)
        rows.append(row)
    return emit({"models": rows, "dir": str(babeldub_home() / "models"), "catalog": str(catalog.source)})


def cmd_models_fetch(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    specs = [catalog.model(name) for name in _csv(args.names)]
    if not specs:
        raise BabelDubError("no model name given", code=EXIT_USAGE, hint="see `babeldub models list`")
    accepted = _accepted(args)
    items = [installer.plan_model(spec, args.force, accepted) for spec in specs]
    payload: dict[str, Any] = _plan_payload(items)
    if args.dry_run:
        payload["dry_run"] = True
        return emit(payload, EXIT_ATTENTION if payload["blocked"] else EXIT_OK)
    results = installer.execute_plan(items, catalog, "8gb", args.force)
    payload["done"] = [r["name"] for r in results if r["status"] == "installed"]
    payload["skipped"] = [i.name for i in items if i.action == "skip"]
    failed = [r for r in results if r["status"] == "failed"]
    payload["failed"] = clip(failed, 5)
    payload.pop("planned")
    if failed:
        return emit(payload, EXIT_ERROR)
    return emit(payload, EXIT_ATTENTION if payload["blocked"] else EXIT_OK)


# ---- registration -------------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction, common: Common) -> None:
    p = sub.add_parser("doctor", help="check tools, GPU, disk, engine venvs and models; recommend a tier")
    p.add_argument("--no-import-check", action="store_true", help="do not start engine venvs to test imports")
    common(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("setup", help="create engine venvs, download models and tools (idempotent)")
    p.add_argument("--engines", help="comma list of engines (default: project TTS engine + qc)")
    p.add_argument("--models", help="auto | none | comma list of catalog names (default: auto)")
    p.add_argument("--tools", action="store_true", help="install external tools (vgmstream-cli)")
    p.add_argument("--tier", choices=["auto", *TIERS], help="hardware tier (default: detected from the GPU)")
    p.add_argument("--lang", help="target language for language-specific models (default: active project)")
    p.add_argument("--accept-license", action="append", metavar="MODEL",
                   help="consent to the license of an opt-in (non-commercial) model")
    p.add_argument("--force", action="store_true", help="re-run installation steps even if satisfied")
    p.add_argument("--dry-run", action="store_true", help="print the plan with sizes and licenses only")
    p.add_argument("--no-import-check", action="store_true", help="trust existing venv markers without importing")
    common(p)
    p.set_defaults(func=cmd_setup)

    models = sub.add_parser("models", help="model catalog: list | fetch")
    msub = models.add_subparsers(dest="models_command", metavar="<action>")
    msub.required = True
    p = msub.add_parser("list", help="catalog with licenses, sizes and download status")
    p.add_argument("--engine", help="only models of this engine")
    common(p)
    p.set_defaults(func=cmd_models_list)
    p = msub.add_parser("fetch", help="download model weights (hf download --local-dir)")
    p.add_argument("names", help="comma list of catalog names")
    p.add_argument("--accept-license", action="append", metavar="MODEL")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    common(p)
    p.set_defaults(func=cmd_models_fetch)
