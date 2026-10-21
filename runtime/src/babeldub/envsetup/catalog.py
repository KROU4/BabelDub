"""Model and engine catalog (data/models.yaml, CONTRACT.md §9) and where things live on disk.

Other modules use it to find an engine's venv/worker and a model's weights:
    cat = load_catalog()
    eng = cat.engine("qwen3tts")              # EngineSpec
    m = default_model("qwen3tts", "8gb", "ru")  # ModelSpec | None (auto model of the tier, general first)
    model_dir(m)                              # BABELDUB_HOME/models/<name> (or a recognised alias folder)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from babeldub.core.jsonl import read_json
from babeldub.core.output import EXIT_ERROR, EXIT_USAGE, BabelDubError
from babeldub.core.paths import data_dir, engines_dir, env_python, envs_dir, models_dir

TIERS = ["cpu", "8gb", "12gb", "24gb"]
MODEL_MARKER = ".babeldub-model.json"
ENV_MARKER = "babeldub-env.json"
_WEIGHT_SUFFIXES = {".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".onnx", ".gguf"}


def _primary(lang: str | None) -> str | None:
    if not lang:
        return None
    return lang.replace("_", "-").split("-")[0].lower()


def _langs_support(languages: list[str], lang: str | None) -> bool:
    code = _primary(lang)
    return "*" in languages or code is None or code in languages


@dataclass
class EngineSpec:
    name: str
    env: str | None
    worker: str | None
    python: str = "3.12"
    torch: dict[str, Any] | None = None
    packages: list[str] = field(default_factory=list)
    gpu_packages: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=lambda: ["*"])
    license: str | None = None
    size_gb: float = 0.0
    notes: str = ""
    models_of: str | None = None  # runs the catalog models of another engine
    cuda_only: bool = False  # never installed or used without an NVIDIA GPU (CPU tier)
    fallback: str | None = None  # engine used instead when this one cannot run
    role: str = "tts"  # tts | qc | vc
    git: dict[str, Any] | None = None  # {url, commit}: source checkout under BABELDUB_HOME/engines/<name>
    requirements: str | None = None  # requirements file inside the checkout (torch lines are dropped)
    post_install: list[list[str]] = field(default_factory=list)  # env-python commands run in the checkout

    def supports(self, lang: str | None) -> bool:
        return _langs_support(self.languages, lang)

    def env_dir(self) -> Path | None:
        return envs_dir() / self.env if self.env else None

    def source_dir(self) -> Path | None:
        return engines_dir() / self.name if self.git else None

    def python_path(self) -> Path | None:
        return env_python(self.env) if self.env else None

    def needs_git(self) -> bool:
        return any("git+" in p for p in self.packages + self.gpu_packages)


@dataclass
class ModelSpec:
    name: str
    repo: str
    engine: str
    role: str = "tts"
    license: str = "unknown"
    commercial: bool = False
    opt_in: bool = False
    size_gb: float = 0.0
    vram_gb: float = 0.0
    tiers: list[str] = field(default_factory=list)
    auto_tiers: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=lambda: ["*"])
    include: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    revision: str | None = None
    warning: str | None = None
    notes: str = ""

    def supports(self, lang: str | None) -> bool:
        return _langs_support(self.languages, lang)

    @property
    def language_specific(self) -> bool:
        return "*" not in self.languages and len(self.languages) <= 2

    def summary(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "name": self.name,
            "engine": self.engine,
            "role": self.role,
            "license": self.license,
            "commercial": self.commercial,
            "size_gb": self.size_gb,
            "vram_gb": self.vram_gb,
            "tiers": self.tiers,
        }
        if self.opt_in:
            row["opt_in"] = True
        if self.language_specific:
            row["languages"] = self.languages
        if self.warning:
            row["warning"] = self.warning
        return row


@dataclass
class Catalog:
    engines: dict[str, EngineSpec]
    models: list[ModelSpec]
    source: Path | None = None

    def engine(self, name: str) -> EngineSpec:
        if name not in self.engines:
            raise BabelDubError(f"unknown engine '{name}'", code=EXIT_USAGE, known=sorted(self.engines))
        return self.engines[name]

    def model(self, name: str) -> ModelSpec:
        for spec in self.models:
            if spec.name == name or name in spec.aliases or spec.repo == name:
                return spec
        lowered = name.lower()
        for spec in self.models:
            if spec.name.lower() == lowered:
                return spec
        raise BabelDubError(
            f"unknown model '{name}'", code=EXIT_USAGE, known=[m.name for m in self.models], hint="see `babeldub models list`"
        )

    def model_owner(self, engine: str) -> str:
        """Engine whose catalog models `engine` runs (itself unless it declares `models_of`)."""
        spec = self.engines.get(engine)
        return spec.models_of if spec is not None and spec.models_of else engine

    def models_for_engine(self, engine: str) -> list[ModelSpec]:
        owner = self.model_owner(engine)
        return [m for m in self.models if m.engine == owner]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def parse_catalog(data: dict[str, Any], source: Path | None = None) -> Catalog:
    if not isinstance(data, dict):
        raise BabelDubError("model catalog must be a mapping", code=EXIT_ERROR, where=str(source))
    engines: dict[str, EngineSpec] = {}
    for name, raw in (data.get("engines") or {}).items():
        raw = raw or {}
        engines[name] = EngineSpec(
            name=name,
            env=raw.get("env"),
            worker=raw.get("worker"),
            python=str(raw.get("python") or "3.12"),
            torch=raw.get("torch"),
            packages=_as_list(raw.get("packages")),
            gpu_packages=_as_list(raw.get("gpu_packages")),
            imports=_as_list(raw.get("import")),
            languages=_as_list(raw.get("languages")) or ["*"],
            license=raw.get("license"),
            size_gb=float(raw.get("size_gb") or 0.0),
            notes=str(raw.get("notes") or ""),
            models_of=raw.get("models_of"),
            cuda_only=bool(raw.get("cuda_only", False)),
            fallback=raw.get("fallback"),
            role=str(raw.get("role") or "tts"),
            git=raw.get("git"),
            requirements=raw.get("requirements"),
            post_install=[_as_list(cmd) for cmd in raw.get("post_install") or []],
        )
        if engines[name].git and not (engines[name].git or {}).get("url"):
            raise BabelDubError(f"catalog engine '{name}' has git without url", code=EXIT_ERROR)
    for name, spec in engines.items():
        for ref in (spec.models_of, spec.fallback):
            if ref and ref not in engines:
                raise BabelDubError(f"catalog engine '{name}' refers to unknown engine '{ref}'", code=EXIT_ERROR)
    models: list[ModelSpec] = []
    seen: set[str] = set()
    for raw in data.get("models") or []:
        missing = [k for k in ("name", "repo", "engine") if not raw.get(k)]
        if missing:
            raise BabelDubError(f"catalog model entry lacks {missing}", code=EXIT_ERROR, entry=raw)
        if raw["engine"] not in engines:
            raise BabelDubError(f"catalog model '{raw['name']}' uses unknown engine '{raw['engine']}'", code=EXIT_ERROR)
        if raw["name"] in seen:
            raise BabelDubError(f"duplicate catalog model '{raw['name']}'", code=EXIT_ERROR)
        seen.add(raw["name"])
        tiers = _as_list(raw.get("tiers"))
        auto_tiers = _as_list(raw.get("auto_tiers"))
        bad = [t for t in tiers + auto_tiers if t not in TIERS]
        if bad:
            raise BabelDubError(f"catalog model '{raw['name']}' has unknown tiers {bad}", code=EXIT_ERROR, known=TIERS)
        commercial = bool(raw.get("commercial", False))
        models.append(
            ModelSpec(
                name=str(raw["name"]),
                repo=str(raw["repo"]),
                engine=str(raw["engine"]),
                role=str(raw.get("role") or "tts"),
                license=str(raw.get("license") or "unknown"),
                commercial=commercial,
                # non-commercial weights are always opt-in, whatever the file says
                opt_in=bool(raw.get("opt_in", False)) or not commercial,
                size_gb=float(raw.get("size_gb") or 0.0),
                vram_gb=float(raw.get("vram_gb") or 0.0),
                tiers=tiers,
                auto_tiers=auto_tiers,
                languages=_as_list(raw.get("languages")) or ["*"],
                include=_as_list(raw.get("include")),
                aliases=_as_list(raw.get("aliases")),
                revision=raw.get("revision"),
                warning=raw.get("warning"),
                notes=str(raw.get("notes") or ""),
            )
        )
    return Catalog(engines=engines, models=models, source=source)


@lru_cache(maxsize=4)
def _load_cached(path_str: str, mtime: float) -> Catalog:
    path = Path(path_str)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return parse_catalog(data, source=path)


def catalog_path() -> Path:
    return data_dir() / "models.yaml"


def load_catalog(path: Path | None = None) -> Catalog:
    path = path or catalog_path()
    if not path.exists():
        raise BabelDubError(f"model catalog not found: {path}", code=EXIT_ERROR)
    return _load_cached(str(path), path.stat().st_mtime)


# ---- weights on disk ----------------------------------------------------------------------------


def model_dir(spec: ModelSpec | str, catalog: Catalog | None = None) -> Path:
    """Folder with the model weights; an existing alias folder wins over a missing canonical one."""
    if isinstance(spec, str):
        spec = (catalog or load_catalog()).model(spec)
    canonical = models_dir() / spec.name
    if canonical.exists():
        return canonical
    for alias in spec.aliases:
        candidate = models_dir() / alias
        if candidate.exists():
            return candidate
    return canonical


def _has_incomplete(folder: Path) -> bool:
    cache = folder / ".cache" / "huggingface" / "download"
    return cache.exists() and any(cache.rglob("*.incomplete"))


def is_model_downloaded(spec: ModelSpec, folder: Path | None = None) -> bool:
    folder = folder or model_dir(spec)
    if not folder.is_dir():
        return False
    if (folder / MODEL_MARKER).exists():
        return True
    if _has_incomplete(folder):
        return False
    for path in folder.rglob("*"):
        if path.suffix in _WEIGHT_SUFFIXES and ".cache" not in path.parts and path.is_file() and path.stat().st_size > 0:
            return True
    return False


def model_status(spec: ModelSpec) -> dict[str, Any]:
    folder = model_dir(spec)
    marker = read_json(folder / MODEL_MARKER, default=None) if folder.exists() else None
    return {
        "downloaded": is_model_downloaded(spec, folder),
        "dir": str(folder),
        "revision": (marker or {}).get("revision"),
    }


# ---- selection ----------------------------------------------------------------------------------


def tier_for_vram(total_gb: float | None) -> str:
    """Hardware tier from the VRAM of the largest NVIDIA GPU (None = no CUDA GPU)."""
    if total_gb is None or total_gb < 5.5:
        return "cpu"
    if total_gb < 11:
        return "8gb"
    if total_gb < 20:
        return "12gb"
    return "24gb"


def auto_models(
    catalog: Catalog, engines: list[str], tier: str, lang: str | None, accepted: set[str] | None = None
) -> list[ModelSpec]:
    """Models `setup` installs by default: auto for this tier, for a selected engine and the language.

    Language-specific models (e.g. ru-stress) are only picked when the language is known. Opt-in models
    (non-commercial) are picked only when their license was accepted explicitly.
    """
    accepted = accepted or set()
    owners = {catalog.model_owner(e) for e in engines}
    picked: list[ModelSpec] = []
    for spec in catalog.models:
        if spec.engine not in owners or tier not in spec.auto_tiers:
            continue
        if spec.language_specific and (lang is None or not spec.supports(lang)):
            continue
        if not spec.supports(lang):
            continue
        if spec.opt_in and spec.name not in accepted:
            continue
        picked.append(spec)
    return picked


def default_model(engine: str, tier: str, lang: str | None, catalog: Catalog | None = None,
                  downloaded_only: bool = False) -> ModelSpec | None:
    """Best model of an engine for a tier and language: auto for the tier first, then general before
    language-specific (fine-tunes kept the original actor's voice worse in tests), then larger.
    Mirrors the default of `synth` (commands/voice.py)."""
    catalog = catalog or load_catalog()
    candidates = [
        m
        for m in catalog.models_for_engine(engine)
        if (tier in m.tiers or not m.tiers) and m.supports(lang) and not m.opt_in and m.role == "tts"
    ]
    if downloaded_only:
        candidates = [m for m in candidates if is_model_downloaded(m)]
    if not candidates:
        return None
    candidates.sort(key=lambda m: (0 if tier in m.auto_tiers else 1, 1 if m.language_specific else 0, -m.size_gb))
    return candidates[0]


def model_for_role(role: str, tier: str, lang: str | None = None, catalog: Catalog | None = None) -> ModelSpec | None:
    """First catalog model with this role (asr | sv | tts) that runs on the tier and speaks the language."""
    catalog = catalog or load_catalog()
    for spec in catalog.models:
        if spec.role == role and (tier in spec.tiers or not spec.tiers) and spec.supports(lang) and not spec.opt_in:
            return spec
    return None


DEFAULT_TTS_PREFERENCE = ("qwen3tts-fast", "qwen3tts", "chatterbox")
AUTO_ENGINE = "auto"  # tts.engine=auto: PREMIUM_TTS on an 8 GB+ GPU when it speaks the language, else the preference
PREMIUM_TTS = "voxcpm"
VC_ENGINE = "applio"
GPU_TIERS = ("8gb", "12gb", "24gb")
MIN_BIG_GPU_GB = 7.5  # an "8 GB" card reports ~7.6-8.0 GB


def engine_for_language(lang: str, catalog: Catalog | None = None,
                        preference: tuple[str, ...] = DEFAULT_TTS_PREFERENCE) -> str | None:
    """First preferred TTS engine that speaks `lang` (qwen3tts-fast falls back to qwen3tts off CUDA)."""
    catalog = catalog or load_catalog()
    for name in preference:
        if name in catalog.engines and catalog.engines[name].supports(lang):
            return name
    return None


def runnable_engine(name: str, tier: str, catalog: Catalog | None = None) -> str:
    """`name`, or the first engine of its fallback chain that is not CUDA only when the tier is `cpu`."""
    catalog = catalog or load_catalog()
    seen: set[str] = set()
    while name not in seen:
        seen.add(name)
        spec = catalog.engines.get(name)
        if spec is None or not (spec.cuda_only and tier == "cpu" and spec.fallback):
            return name
        name = spec.fallback
    return name


def big_gpu(tier: str, vram_gb: float | None = None) -> bool:
    """An NVIDIA GPU with 8 GB+ (VoxCPM2 peaks at 6.5 GB; RVC training needs the GPU too).
    vram_gb None = only the tier is known (e.g. `setup --tier 8gb`)."""
    return tier in GPU_TIERS and (vram_gb is None or vram_gb >= MIN_BIG_GPU_GB)


def setup_tts_engine(configured: str | None, lang: str | None, tier: str, catalog: Catalog | None = None,
                     vram_gb: float | None = None) -> str | None:
    """TTS engine `setup` installs: tts.engine=auto -> PREMIUM_TTS on an 8 GB+ GPU when it speaks the
    language, else the first preferred engine for the language (its fallback on the cpu tier)."""
    catalog = catalog or load_catalog()
    name = configured if configured in catalog.engines else AUTO_ENGINE
    if name != AUTO_ENGINE and lang and not catalog.engines[name].supports(lang):
        name = AUTO_ENGINE
    if name == AUTO_ENGINE:
        premium = catalog.engines.get(PREMIUM_TTS)
        if premium is not None and big_gpu(tier, vram_gb) and premium.supports(lang):
            return PREMIUM_TTS
        found = engine_for_language(lang or "en", catalog)
        return runnable_engine(found, tier, catalog) if found else None
    runnable = runnable_engine(name, tier, catalog)
    if lang and not catalog.engines[runnable].supports(lang):  # e.g. voxcpm -> qwen3tts on a cpu tier for pl
        found = engine_for_language(lang, catalog)
        return runnable_engine(found, tier, catalog) if found else None
    return runnable


def env_marker(engine: EngineSpec) -> dict[str, Any] | None:
    folder = engine.env_dir()
    if folder is None:
        return None
    return read_json(folder / ENV_MARKER, default=None)
