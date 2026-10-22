"""Voice stages: `synth` (dub in the ORIGINAL character's voice) and `post` (fit, level, format).

synth  tts.engine=auto runs VoxCPM2 (voxcpm) on an NVIDIA GPU, else qwen3tts-fast/qwen3tts/chatterbox.
       VoxCPM2 uses ref_strategy=bootstrap: the character's long original timbre reference for every
       line, plus a target-language continuation prompt made of the character's first accepted dubs
       once QA accepted enough of them (cast/bootstrap.py). Other engines use the original line itself
       with its source transcript (line) or the character's best clean reference (char). Every line is
       generated tts.best_of times with different seeds; the take whose speech length is closest to the
       original's wins. One worker process per run (the model loads once), results appended to
       synth/results.jsonl as they finish. ICL needs the exact transcript of the prompt: a line
       transcript with an implausible syllable rate goes to the char reference or timbre-only mode.
       Generation is capped per line; a runaway take is discarded and retried (see _SynthRun).
       Alt takes/duplicates (core/takes.py followers) are skipped: the build reuses their leader.
post   Fits the dub to the original (audio/fit.fit_segments): never slower than recorded (a shorter dub
       keeps its tempo, the rest is silence); a dub that does not fit first loses part of its pauses,
       then speeds up to post.tempo_max (pitch kept); fixed_length lines are placed chunk by chunk on
       the original's speech segments (inventory/analysis.jsonl vad_segments, else the energy VAD);
       exactly the original's sample count, rate and channels; loudness matched to the original speech.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from babeldub.analysis.store import keep_original_classes, line_classes, load_analysis
from babeldub.audio.dsp import fade, speech_seconds, to_mono
from babeldub.audio.fit import fit_segments, length_score, speech_span
from babeldub.audio.io import probe, read_audio, write_wav
from babeldub.audio.stretch import METHODS as STRETCH_METHODS
from babeldub.audio.stretch import time_stretch
from babeldub.cast.bootstrap import Prompt, long_references, phase1_ids, target_bank
from babeldub.cast.refs import Arousal, emotion_banks, line_arousal
from babeldub.core import layout
from babeldub.core.compute import device_warning, resolve_device
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import append_jsonl, latest_by, now_iso, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_ENV, EXIT_OK, EXIT_USAGE, BabelDubError, clip, emit
from babeldub.core.paths import env_python, models_dir
from babeldub.core.project import Project
from babeldub.core.takes import follower_map
from babeldub.text.normalize import acute_to_plus, render_stress
from babeldub.text.syllables import SYLLABLE_RATE, count_syllables
from babeldub.workers.client import GpuLock, WorkerClient, WorkerError

VOICED_KINDS = ("dialogue", "cutscene", "bark")
ENGINES = ("voxcpm", "qwen3tts-fast", "qwen3tts", "chatterbox", "dummy")
AUTO = "auto"  # tts.engine=auto: voxcpm on CUDA when its env exists, else the preference below
AUTO_PREFERENCE = ("qwen3tts-fast", "qwen3tts", "chatterbox")
PREMIUM = "voxcpm"
PROMPT_ENGINES = ("voxcpm", "dummy")  # timbre reference + target-language continuation prompt (bootstrap)
# same models and the same output for one seed: a take of one is "done" for the other
ENGINE_FAMILY = {"qwen3tts-fast": "qwen3tts"}
REF_MAX_S = 15.0  # longer references are cut at a pause (models are trained on 3-15 s prompts)
VOXCPM_REF_MAX_S = 30.0  # VoxCPM2 takes the ~15-20 s long timbre reference as is
DEFAULT_LEVEL_LUFS = -20.0  # loudness target when the original audio is missing
CODEC_HZ = 12.5  # Qwen3-TTS 12Hz tokenizer: 12.5 generated tokens per second of audio
DEFAULT_BATCH = 4  # lines per generate call when the worker supports synth_batch (tts.batch_size)
DEFAULT_SPEECH_RATE = 5.0  # syllables/s for the length estimate when a language has no measured rate
CAP_MIN_S = 6.0  # generation cap never below this many seconds of audio
ICL_RATE_RANGE = (1.2, 8.5)  # syl/s of a line transcript over its original speech that fit an ICL prompt
TOKEN_CAP_ENGINES = ("qwen3tts-fast", "qwen3tts", "dummy")  # engines that take params.max_new_tokens
TRANSCRIPT_ENGINES = ("qwen3tts-fast", "qwen3tts", "dummy")  # voice prompt uses ref_text (ICL vs timbre only)
SYNTH_SCHEMA = "babeldub.synth_results/1"
POST_SCHEMA = "babeldub.post/1"
DECISIONS_SCHEMA = "babeldub.decisions/1"
ANALYSIS = "inventory/analysis.jsonl"  # optional: {id, vad_segments: [[start_s, end_s], ...], ...}
SHORT_WORD_BEST_OF = 6  # tts.short_word_best_of: candidates of a short_word line ("Freeze!")
SHORT_WORD_ATTEMPTS = 2  # tts.short_word_attempts: one regen, then the original is kept

# Fallback when data/models.yaml or babeldub.envsetup is unavailable.
BUILTIN_ENGINES: dict[str, dict[str, Any]] = {
    "voxcpm": {
        "env": "voxcpm", "worker": "tts_voxcpm.py",
        "languages": ["ar", "my", "zh", "da", "nl", "en", "fi", "fr", "de", "el", "he", "hi", "id", "it", "ja",
                      "km", "ko", "lo", "ms", "no", "pl", "pt", "ru", "es", "sw", "sv", "tl", "th", "tr", "vi"],
        "model": "VoxCPM2", "repo": "openbmb/VoxCPM2", "cuda_only": True, "fallback": "qwen3tts-fast",
        "stress": "upper",
    },
    "qwen3tts-fast": {
        "env": "qwen3tts-fast", "worker": "tts_qwen3_fast.py",
        "languages": ["zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"],
        "model": "Qwen3-TTS-12Hz-1.7B-Base", "repo": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "cuda_only": True, "fallback": "qwen3tts",
    },
    "qwen3tts": {
        "env": "qwen3tts", "worker": "tts_qwen3.py",
        "languages": ["zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"],
        "model": "Qwen3-TTS-12Hz-1.7B-Base", "repo": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    },
    "chatterbox": {
        "env": "chatterbox", "worker": "tts_chatterbox.py",
        "languages": ["ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms",
                      "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh"],
        "model": "chatterbox-multilingual", "repo": "ResembleAI/chatterbox",
    },
    "dummy": {"env": None, "worker": "tts_dummy.py", "languages": ["*"], "model": "dummy", "repo": "dummy",
              "stress": "upper"},  # mimics VoxCPM2
}


# ---- small helpers -----------------------------------------------------------------------
def _primary(lang: str | None) -> str:
    return (lang or "").replace("_", "-").split("-")[0].lower()


def _family(engine: Any) -> str:
    return ENGINE_FAMILY.get(str(engine), str(engine))


def _num(value: float | None, digits: int = 3) -> float | None:
    """JSON-safe rounding (-inf/nan -> None)."""
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _sha(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _seed(line_id: str, attempt: int) -> int:
    return int(hashlib.sha1(f"{line_id}#{attempt}".encode()).hexdigest()[:8], 16) & 0x7FFFFFFF


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _ensure_header(path: Path, schema: str) -> None:
    if not path.exists():
        write_jsonl(path, [], schema=schema)


_FALLBACK_MARKUP = re.compile(r"\{[^{}]*\}|<[^<>]*>|\[[^\[\]\n]{0,80}\]")


def _fallback_clean(text: str) -> str:
    text = _FALLBACK_MARKUP.sub(" ", text.replace("\\n", " ").replace("\n", " ").replace("́", ""))
    text = re.sub(r"\s+([,.!?;:…])", r"\1", re.sub(r"\s+", " ", text))
    return re.sub(r"^[\s:;,]+", "", text).strip()


def keeps_stress(model_name: str) -> bool:
    """Only an explicit stress model (e.g. qwen3-tts-...-ru-stress-cf) reads U+0301 marks; VoxCPM2 and
    Qwen3-TTS Base turn them into extra sounds, so every other model gets plain text."""
    return "stress" in model_name.lower()


def _on(value: Any) -> bool:
    return str(value).strip().lower() in ("on", "true", "1", "yes")


def stress_mode(project: Project, engine: EngineChoice, catalog: Any | None) -> str:
    """How stress marks reach the engine: acute (U+0301) for an explicit stress model, else the engine's
    capability (`stress` in models.yaml: VoxCPM2 reads a CAPITAL vowel) with tts.stress=on, else none."""
    model_stress = None
    if catalog is not None:
        try:
            model_stress = catalog.model(engine.model_name).stress
        except BabelDubError:
            model_stress = None
    if model_stress == "acute" or keeps_stress(engine.model_name):
        return "acute"
    if not _on(project.setting("tts", "stress", "off")):
        return "none"
    return str(model_stress or _engine_spec(engine.name, catalog).get("stress") or "none")


def pronounce_map(project: Project) -> dict[str, str]:
    """{glossary translation: pronounce} for terms with a pronunciation."""
    rows = latest_by(read_jsonl(project.path(layout.GLOSSARY)), "term")
    return {str(r["translation"]): str(r["pronounce"]) for r in rows.values() if r.get("translation") and r.get("pronounce")}


def _text_preparer(stress: bool | str = False, pronounce: dict[str, str] | None = None) -> Callable[[str, str, str], str]:
    """babeldub.text.normalize.prepare_for_tts when available, else a markup-only cleaner."""
    try:
        from babeldub.text.normalize import prepare_for_tts
    except ImportError:
        return lambda text, lang, yo: _fallback_clean(text)

    def prepare(text: str, lang: str, yo: str) -> str:
        try:
            return prepare_for_tts(text, lang, yo=yo, stress=stress, pronounce=pronounce)
        except (TypeError, ValueError):
            return _fallback_clean(text)

    return prepare


ACCENT_CACHE = "synth/accent.jsonl"  # {text, marked}: RUAccent marks of prepared texts (tts.stress=on)


class Accenter:
    """Homograph stress marks of prepared texts via the qc worker's `accent` op (RUAccent), cached in
    synth/accent.jsonl so a text keeps its marks (and the line its `done` state) across runs."""

    def __init__(self, project: Project, compute: bool) -> None:
        self.project = project
        self.compute = compute
        self.cache = {str(r["text"]): str(r["marked"]) for r in read_jsonl(project.path(ACCENT_CACHE))
                      if r.get("text") is not None and r.get("marked") is not None}
        self.pending = 0
        self.note: str | None = None

    def __call__(self, texts: list[str]) -> dict[str, str]:
        missing = sorted({t for t in texts if t not in self.cache})
        if missing and self.compute:
            from babeldub.qa.qc import QcItemError, QcSession, qc_missing

            if qc_missing(self.project.data, False, False):
                self.note = "tts.stress=on needs the qc env with ruaccent (`babeldub setup --engines qc`): no marks"
            else:
                try:
                    with QcSession.open(self.project.root, self.project.data, need_asr=False, need_sv=False,
                                        need_accent=True, owner="synth:accent") as session:
                        marked = session.accent([acute_to_plus(t) for t in missing])
                except (BabelDubError, QcItemError) as err:
                    self.note = f"stress marks failed: {err}"
                else:
                    rows = [{"text": t, "marked": m, "ts": now_iso()} for t, m in zip(missing, marked, strict=True)]
                    append_jsonl(self.project.path(ACCENT_CACHE), rows)
                    self.cache.update({r["text"]: r["marked"] for r in rows})
        self.pending = sum(1 for t in set(texts) if t not in self.cache)
        return {t: self.cache[t] for t in texts if t in self.cache}


# ---- engines and models ---------------------------------------------------------------------
@dataclass
class EngineChoice:
    name: str
    python: Path
    worker: str
    languages: list[str]
    model_ref: str  # what the worker loads: local dir or HF repo id
    model_name: str  # what goes into manifests
    model_source: str  # local | hf | none


def _catalog() -> Any | None:
    try:
        from babeldub.envsetup.catalog import load_catalog

        return load_catalog()
    except (ImportError, BabelDubError, OSError, ValueError):
        return None


def _resolve_model(engine: str, wanted: str | None, lang: str, tier: str, catalog: Any | None) -> tuple[str, str, str]:
    builtin = BUILTIN_ENGINES[engine]
    if engine == "dummy":
        return "dummy", "dummy", "none"
    if wanted:
        path = Path(wanted).expanduser()
        if path.is_dir():
            return str(path), path.name, "local"
    spec = None
    if catalog is not None:
        try:
            from babeldub.envsetup.catalog import is_model_downloaded, model_dir

            if wanted:
                spec = catalog.model(wanted)
            else:
                # Default: a downloaded general Base model, larger first. Language-specific fine-tunes
                # (e.g. ru-stress-cf) only when set explicitly: on the LBR test lines they kept the
                # original actor's voice much worse (ECAPA sim 0.05-0.37 vs 0.37-0.51 for 1.7B-Base).
                cands = [
                    m for m in catalog.models_for_engine(engine)
                    if m.role == "tts" and m.supports(lang) and not m.opt_in
                    and (tier in ("", "auto") or not m.tiers or tier in m.tiers)
                ]
                cands.sort(key=lambda m: (not is_model_downloaded(m), m.language_specific, -m.size_gb))
                spec = cands[0] if cands else None
            if spec is not None:
                folder = model_dir(spec)
                if is_model_downloaded(spec, folder):
                    return str(folder), spec.name, "local"
                return spec.repo, spec.name, "hf"
        except BabelDubError:
            spec = None
    name = wanted or builtin["model"]
    folder = models_dir() / name
    if folder.is_dir():
        return str(folder), name, "local"
    repo = name if "/" in name else builtin["repo"]
    return repo, name, "hf"


def _engine_spec(engine: str, catalog: Any | None) -> dict[str, Any]:
    spec = dict(BUILTIN_ENGINES[engine])
    if catalog is not None and engine in getattr(catalog, "engines", {}):
        cat = catalog.engines[engine]
        spec.update(env=cat.env, worker=cat.worker or spec["worker"], languages=list(cat.languages),
                    cuda_only=cat.cuda_only, fallback=cat.fallback, stress=getattr(cat, "stress", "none"))
    return spec


def _speaks(spec: dict[str, Any], lang: str | None) -> bool:
    langs = [str(x) for x in spec.get("languages") or []]
    return not lang or "*" in langs or _primary(lang) in langs


def _env_ready(spec: dict[str, Any]) -> bool:
    return not spec.get("env") or env_python(spec["env"]).exists()


def engine_for_device(engine: str, device: str, catalog: Any | None, lang: str | None = None) -> tuple[str, str | None]:
    """The engine itself, or the first engine of its fallback chain (with a note) that can run here:
    a CUDA-only engine (voxcpm, qwen3tts-fast) off CUDA, or one whose env is missing while a fallback's exists."""
    if engine not in BUILTIN_ENGINES:
        return engine, None
    chain = [engine]
    while (nxt := _engine_spec(chain[-1], catalog).get("fallback")) and nxt in BUILTIN_ENGINES and nxt not in chain:
        chain.append(nxt)
    spec = _engine_spec(engine, catalog)
    on_cuda = str(device).startswith("cuda")

    def runs(name: str) -> bool:
        s = _engine_spec(name, catalog)
        return (on_cuda or not s.get("cuda_only")) and _speaks(s, lang)

    if spec.get("cuda_only") and not on_cuda:
        for name in chain[1:]:
            if runs(name):
                return name, f"{engine} runs on CUDA only: using the {name} engine on {device}"
        return engine, None
    if not _env_ready(spec):
        for name in chain[1:]:
            if runs(name) and _env_ready(_engine_spec(name, catalog)):
                return name, f"{engine} env is not installed (`babeldub setup --engines {engine}`): using the {name} engine"
        # nothing installed: the env error should point to the wanted engine
    return engine, None


def auto_engine(lang: str, device: str, catalog: Any | None) -> tuple[str, str]:
    """tts.engine=auto: voxcpm on CUDA when its env exists and it speaks the language (the default
    dubbing chain), else the first of qwen3tts-fast/qwen3tts/chatterbox that speaks it. Returns (engine, note)."""
    premium = _engine_spec(PREMIUM, catalog)
    if not _speaks(premium, lang):
        why = f"{PREMIUM} does not speak '{_primary(lang)}'"
    elif not str(device).startswith("cuda"):
        why = f"{PREMIUM} needs an NVIDIA GPU (device {device})"
    elif not _env_ready(premium):
        why = f"the {PREMIUM} env is not installed (`babeldub setup --engines {PREMIUM}`)"
    else:
        return PREMIUM, f"tts.engine=auto -> {PREMIUM}"
    for name in AUTO_PREFERENCE:
        if _speaks(_engine_spec(name, catalog), lang):
            return name, f"tts.engine=auto -> {name}: {why}"
    raise BabelDubError(f"no TTS engine speaks '{_primary(lang)}'", code=EXIT_USAGE,
                        hint="see `babeldub models list`; BabelDub v0.1 engines cover the languages in docs/MODELS.md")


def resolve_engine(project: Project, engine: str, catalog: Any | None = None, check_env: bool = True) -> EngineChoice:
    """Engine venv, worker script and model for this project; raises EXIT_ENV if the venv is missing."""
    if engine not in ENGINES:
        raise BabelDubError(f"unknown TTS engine '{engine}'", code=EXIT_USAGE, known=list(ENGINES))
    spec = _engine_spec(engine, catalog)
    lang = _primary(project.target_lang)
    languages = [str(x) for x in spec["languages"]]
    if "*" not in languages and lang not in languages:
        others = [e for e in ("voxcpm", "qwen3tts", "chatterbox") if e != engine and lang in BUILTIN_ENGINES[e]["languages"]]
        raise BabelDubError(
            f"engine '{engine}' does not speak '{lang}'",
            code=EXIT_USAGE,
            hint=(f"use `babeldub set tts.engine={others[0]}` or `babeldub synth --engine {others[0]}`"
                  if others else "no installed engine covers this language in v0.1"),
            languages=languages,
        )
    if spec["env"] is None:
        python = Path(sys.executable)
    else:
        python = env_python(spec["env"])
        if check_env and not python.exists():
            raise BabelDubError(
                f"engine environment '{spec['env']}' is not installed ({python})",
                code=EXIT_ENV,
                hint=f"babeldub setup --engines {engine}",
            )
    model_ref, model_name, source = _resolve_model(
        engine, project.setting("tts", "model"), lang, str(project.setting("tts", "tier", "auto")), catalog
    )
    return EngineChoice(engine, python, spec["worker"], languages, model_ref, model_name, source)


# ---- project data -------------------------------------------------------------------------
def _orig_wav(project: Project, line: dict[str, Any]) -> Path | None:
    rel = (line.get("audio") or {}).get("wav")
    candidates = [Path(rel) if rel and Path(rel).is_absolute() else project.path(rel)] if rel else []
    candidates.append(project.path(layout.ORIG_AUDIO_DIR, f"{_line_uid(line)}.wav"))
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def _line_uid(line: dict[str, Any]) -> str:
    return str(line.get("uid") or make_uid(str(line["id"])))


def _abs(project: Project, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else project.path(value)


def _exists(project: Project, value: str | None) -> bool:
    path = _abs(project, value)
    return path is not None and path.exists()


def _usable(project: Project, row: dict[str, Any]) -> bool:
    """An ok take with its file; a take that ran to the token cap (kept by older versions) is not."""
    return row.get("status") == "ok" and not row.get("hit_token_limit") and _exists(project, row.get("wav"))


@dataclass
class Ref:
    mode: str  # line | char | bootstrap-src | bootstrap-tgt
    ref_id: str
    wav: Path
    text: str  # transcript (may be empty -> speaker-embedding-only cloning)
    lang: str
    dur_s: float


@dataclass
class Job:
    line: dict[str, Any]
    id: str
    uid: str
    char_id: str
    text: str
    attempt: int
    ref: Ref
    reason: str  # new | retranslated | regen | reref | retry | force
    target_s: float
    sort_key: tuple[Any, ...]
    key: str = ""
    seed: int = 0
    out: Path = field(default_factory=Path)
    x_vector_only: bool = False
    ref_trimmed: bool = False
    est_s: float = 0.0  # spoken length estimated from the dub text
    alt_ref: Ref | None = None  # character reference to fall back to when a line prompt runs away
    ref_reason: str | None = None  # why the CLI changed the prompt picked by the strategy
    src_rate: float | None = None  # syllables/s of the line transcript over its original speech
    prompt: Prompt | None = None  # bootstrap-tgt: target-language continuation prompt (wav + exact text)
    target_speech: float | None = None  # speech span of the original (best-of picks the closest)
    line_class: str | None = None  # analyze class of the original (speech, short_word, mixed, ...)
    bucket: str | None = None  # arousal bucket of the bootstrap reference/prompt (calm, animated, loud, soft)
    best_of_min: int = 1  # at least this many candidates (short_word lines: tts.short_word_best_of)

    @property
    def ref_mode(self) -> str:
        """line | line-xvec | char | char-xvec | bootstrap-src | bootstrap-tgt (-xvec: timbre only)."""
        return f"{self.ref.mode}-xvec" if self.x_vector_only else self.ref.mode


def estimate_seconds(text: str, lang: str) -> float:
    """Spoken length of a dub text: syllables over the language's dubbing speech rate."""
    return count_syllables(text, lang) / SYLLABLE_RATE.get(_primary(lang), DEFAULT_SPEECH_RATE)


def token_cap(est_s: float, target_s: float) -> int:
    """max_new_tokens of one line: room for twice the longer of the estimate and the original."""
    cap_s = max(CAP_MIN_S, 2.0 * max(est_s, target_s) + 2.5)
    return math.ceil(CODEC_HZ * cap_s - 1e-9)


def is_runaway(reply: dict[str, Any], est_s: float, target_s: float) -> bool:
    """Generation ran to the token cap or far past any plausible length (a loop, not speech)."""
    dur = float(reply.get("dur_s") or 0.0)
    return bool(reply.get("hit_token_limit")) or dur > 2.2 * max(est_s, target_s) + 2.0


def transcript_rate(ref: Ref) -> float:
    """Syllables/s of a line transcript over the speech span of its audio (inf for silence)."""
    syllables = count_syllables(ref.text, ref.lang)
    try:
        data, sr = read_audio(ref.wav)
        speech = speech_seconds(data, sr)
    except (RuntimeError, OSError, ValueError):
        speech = ref.dur_s
    return syllables / speech if speech > 0 else math.inf


def _char_refs(refs: list[dict[str, Any]], project: Project) -> dict[str, list[dict[str, Any]]]:
    by_char: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in refs:
        wav = _abs(project, row.get("wav"))
        if row.get("char_id") is None or wav is None or not wav.exists():
            continue
        by_char[str(row["char_id"])].append(row)
    for rows in by_char.values():
        rows.sort(key=lambda r: (not r.get("chosen"), -float(r.get("score") or 0.0)))
    return by_char


def _char_ref(
    project: Project,
    char_refs: list[dict[str, Any]],
    src_lang: str,
    avoid_ref: str | None,
    prepare: Callable[[str, str, str], str],
) -> Ref | None:
    """The character's chosen reference (another one first when `avoid_ref` is given)."""
    pool = [r for r in char_refs if r.get("chosen")] or char_refs
    ordered = [r for r in pool if str(r.get("ref_id")) != avoid_ref] + [r for r in pool if str(r.get("ref_id")) == avoid_ref]
    for row in ordered:
        wav = _abs(project, row.get("wav"))
        if wav is None or not wav.exists():
            continue
        lang = str(row.get("lang") or src_lang)
        ref_text = prepare(str(row.get("text") or ""), lang, "keep") if row.get("text") else ""
        return Ref("char", str(row.get("ref_id")), wav, ref_text, lang, float(row.get("dur_s") or 0.0))
    return None


def _choose_ref(
    project: Project,
    line: dict[str, Any],
    char_refs: list[dict[str, Any]],
    strategy: str,
    min_s: float,
    prefer: str | None,
    avoid_ref: str | None,
    prepare: Callable[[str, str, str], str],
) -> Ref | None:
    text = line.get("text") or {}
    src_lang = str(text.get("src_lang") or _primary(line.get("locale")) or "")
    orig = _orig_wav(project, line)
    src = prepare(str(text.get("src") or ""), src_lang, "keep") if text.get("src") else ""
    dur = float((line.get("audio") or {}).get("dur_s") or 0.0)
    if orig is not None and not dur:
        dur = float(probe(orig)["dur_s"])
    line_ok = orig is not None and bool(src)
    flags = set(line.get("flags") or [])
    line_good = line_ok and dur >= min_s and dur <= REF_MAX_S and not flags & {"has_bed", "nonverbal"}

    def line_ref() -> Ref:
        assert orig is not None
        return Ref("line", f"line:{_line_uid(line)}", orig, src, src_lang, dur)

    def char_ref() -> Ref | None:
        return _char_ref(project, char_refs, src_lang, avoid_ref, prepare)

    if prefer == "line" and line_ok:
        return line_ref()
    if prefer == "char":
        return char_ref() or (line_ref() if line_ok else None)
    if strategy == "line":
        return line_ref() if line_ok else char_ref()
    if strategy == "char":
        return char_ref() or (line_ref() if line_ok else None)
    # auto: the line itself when it is a usable prompt, else the character reference,
    # and as a last resort a short/noisy original rather than skipping the line
    if line_good:
        return line_ref()
    return char_ref() or (line_ref() if line_ok else None)


def _cut_reference(src: Path, cache_dir: Path, max_s: float = REF_MAX_S) -> Path:
    """Cut a long reference at the quietest 10 ms frame between 60 % and 100 % of max_s."""
    out = cache_dir / f"{_sha(str(src), src.stat().st_size)[:16]}.wav"
    if out.exists():
        return out
    data, sr = read_audio(src)
    mono = to_mono(data)
    limit = int(max_s * sr)
    lo = int(0.6 * limit)
    hop = max(1, int(sr * 0.01))
    window = mono[lo:limit]
    frames = window[: len(window) // hop * hop].reshape(-1, hop)
    cut = lo + int(np.argmin((frames**2).mean(axis=1))) * hop + hop // 2 if len(frames) else limit
    write_wav(out, fade(data[:cut], sr, 10.0, fade_in=False), sr)
    return out


def _fit_ref(ref: Ref, cache_dir: Path, max_s: float = REF_MAX_S) -> tuple[Ref, bool]:
    """A reference longer than max_s is cut (True: the cut audio no longer matches its text)."""
    if ref.dur_s > max_s or (ref.dur_s == 0 and probe(ref.wav)["dur_s"] > max_s):
        return Ref(ref.mode, ref.ref_id, _cut_reference(ref.wav, cache_dir, max_s), ref.text, ref.lang, max_s), True
    return ref, False


def ref_max_s(engine: str) -> float:
    return VOXCPM_REF_MAX_S if engine == PREMIUM else REF_MAX_S


@dataclass
class _Bootstrap:
    """Per-character state of tts.ref_strategy=bootstrap while planning a synth run."""
    long_refs: dict[str, Prompt]  # arousal bucket -> long original timbre reference (calm always)
    bank: dict[str, Prompt]  # arousal bucket -> target-language prompt (empty until enough accepted dubs)
    phase1: list[str]
    phase1_done: bool
    arousal: dict[str, Arousal] = field(default_factory=dict)  # line id -> where its original sits

    @property
    def long_ref(self) -> Prompt | None:
        return self.long_refs.get("calm")

    def pick(self, line_id: str, avoid: str | None = None) -> tuple[str, Prompt, Prompt | None]:
        """(bucket, long reference, target prompt or None) nearest to the line's original; `avoid` skips
        a bucket when another one is available (a regen after a flat or unnatural take)."""
        arousal = self.arousal.get(line_id) or Arousal(None, None)

        def nearest(pool: dict[str, Prompt]) -> str:
            order = arousal.ranked(pool)
            if avoid in order and len(order) > 1:
                order.remove(avoid)
            return order[0]

        long_bucket = nearest(self.long_refs)
        bucket = nearest(self.bank) if self.bank else long_bucket
        return bucket, self.long_refs[long_bucket], self.bank.get(bucket)


def _bootstrap_states(
    project: Project,
    lines_all: dict[str, dict[str, Any]],
    char_of: dict[str, str],
    translations: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    history: dict[str, list[dict[str, Any]]],
    follows: dict[str, str],
    refs_by_char: dict[str, list[dict[str, Any]]],
    analysis: dict[str, dict[str, Any]] | None = None,
    skip: set[str] | frozenset[str] = frozenset(),
) -> dict[str, _Bootstrap]:
    analysis = analysis or {}
    tts = project.data.get("tts", {})
    k = int(tts.get("bootstrap_lines", 4) or 4)
    need = int(tts.get("bootstrap_min_accepted", 3) or 3)
    max_attempts = int(tts.get("max_attempts", 3))
    max_cer = float(project.setting("qa", "max_cer", 0.15))
    post = latest_by(read_jsonl(project.path(layout.POST)))
    qa = latest_by(read_jsonl(project.path(layout.QA)))
    by_char: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for lid, cid in char_of.items():
        line = lines_all[lid]
        if lid in follows or lid in skip or not str((translations.get(lid) or {}).get("dub_text") or "").strip():
            continue
        by_char[cid].append(line)
    states: dict[str, _Bootstrap] = {}
    for cid, lines in by_char.items():
        all_lines = [lines_all[lid] for lid, c in char_of.items() if c == cid and lid not in follows]
        arousal = line_arousal(all_lines, analysis)
        long_refs = long_references(project.root, cid, all_lines, refs_by_char.get(cid, []),
                                    emotion_banks(all_lines, analysis)) if cid != "unknown" else {}
        texts: dict[str, str] = {}
        for line in lines:
            row = post.get(line["id"])
            for res in history.get(line["id"], []):
                if row and res.get("status") == "ok" and res.get("attempt") == row.get("attempt"):
                    texts[line["id"]] = str(res.get("text") or "")
        nonverbal = {ln["id"] for ln in lines if "nonverbal" in (ln.get("flags") or [])}
        bank = target_bank(project.root, cid, [ln["id"] for ln in lines], post=post, qa=qa, decisions=decisions,
                           texts=texts, min_accepted=need, max_cer=max_cer, nonverbal=nonverbal,
                           buckets={lid: a.bucket for lid, a in arousal.items()}) if long_refs else {}
        first = phase1_ids(lines, k)

        def settled(lid: str) -> bool:
            row, q = post.get(lid), qa.get(lid)
            if row and q and q.get("attempt") == row.get("attempt") and q.get("wav") == row.get("wav"):
                return True
            if (decisions.get(lid) or {}).get("decision") in ("keep_original", "skip"):
                return True
            rows = history.get(lid, [])
            return not any(r.get("status") == "ok" for r in rows) and len(rows) >= max_attempts

        states[cid] = _Bootstrap(long_refs, bank, first, all(settled(lid) for lid in first), arousal)
    return states


# ---- synth --------------------------------------------------------------------------------
REBUCKET_REASONS = frozenset({"flat", "mos"})  # QA reasons a regen answers with another arousal bucket


def _avoid_bucket(reason: str, latest_ok: dict[str, Any] | None, qa_row: dict[str, Any] | None) -> str | None:
    """The bucket of a flat or unnatural take that QA sent back: its regen tries the next nearest one."""
    if reason != "regen" or not latest_ok or not qa_row or qa_row.get("attempt") != latest_ok.get("attempt"):
        return None
    return latest_ok.get("bucket") if REBUCKET_REASONS.intersection(qa_row.get("reasons") or []) else None


def _overridden(decision: dict[str, Any] | None) -> bool:
    """An agent/human asked for a dub of a line whose class would keep the original."""
    return bool(decision) and decision.get("by") in ("agent", "human")         and decision.get("decision") not in ("keep_original", "skip")


def _plan_synth(
    project: Project, args: argparse.Namespace, engine: EngineChoice, strategy: str,
    catalog: Any | None = None, accent: Callable[[list[str]], dict[str, str]] | None = None,
) -> tuple[list[Job], Counter[str], list[str], list[str]]:
    """Jobs of this run, skip counters, lines that need attention and lines that wait for the
    bootstrap target-language prompt (strategy `bootstrap`).

    Line classes (inventory/analysis.jsonl): interjection/nonverbal lines keep the original (an auto
    keep_original decision, skipped.keep_original); a short_word line is spoken with the line itself as
    the reference, tts.short_word_best_of candidates and tts.short_word_attempts attempts."""
    lines_all = latest_by(read_jsonl(project.path(layout.LINES)))
    if not lines_all:
        raise BabelDubError("no lines in inventory/lines.jsonl", code=EXIT_USAGE, hint="run `babeldub extract` and `babeldub link` first")
    translations = latest_by(read_jsonl(project.path(layout.TRANSLATIONS)))
    if not translations:
        raise BabelDubError("no translations yet", code=EXIT_USAGE, hint="run the translate stage first (`babeldub translate plan`)")
    characters = latest_by(read_jsonl(project.path(layout.CHARACTERS)), "char_id")
    assign = latest_by(read_jsonl(project.path(layout.ASSIGN)))
    cast_done = bool(characters) and bool(assign)
    refs_by_char = _char_refs(read_jsonl(project.path(layout.REFS)), project)
    decisions = latest_by(read_jsonl(project.path(layout.DECISIONS)))
    qa_latest = latest_by(read_jsonl(project.path(layout.QA)))
    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(project.path(layout.SYNTH_RESULTS)):
        if "id" in row:
            history[row["id"]].append(row)

    target = project.target_lang
    tts = project.data.get("tts", {})
    min_s = float(tts.get("line_ref_min_s", 1.6))
    max_attempts = int(tts.get("max_attempts", 3))
    yo = str(tts.get("yo") or "keep")
    mode = stress_mode(project, engine, catalog)
    prepare = _text_preparer("acute" if accent is not None else mode, pronounce_map(project))
    analysis = load_analysis(project)
    classes = line_classes(analysis)
    keep_classes = keep_original_classes(project)
    short_best_of = max(1, int(tts.get("short_word_best_of", SHORT_WORD_BEST_OF) or SHORT_WORD_BEST_OF))
    short_attempts = max(1, int(tts.get("short_word_attempts", SHORT_WORD_ATTEMPTS) or SHORT_WORD_ATTEMPTS))
    want_chars = {c.lower() for c in _csv(args.chars)}
    want_ids = set(_csv(args.ids))
    follows = follower_map(lines_all.values())

    def char_of(line_id: str, line: dict[str, Any]) -> str:
        hint = str(line.get("speaker_hint") or "unknown")
        return str((assign.get(line_id) or {}).get("char_id") or "") if cast_done else hint

    skipped: Counter[str] = Counter()
    picked: list[tuple[str, dict[str, Any], str]] = []
    for line_id, line in lines_all.items():
        if line.get("kind") not in VOICED_KINDS:
            continue
        uid = _line_uid(line)
        if want_ids and line_id not in want_ids and uid not in want_ids:
            continue
        if line_id in follows:  # alt take / duplicate: the build reuses its leader's dub
            skipped["follows"] += 1
            continue
        hint = str(line.get("speaker_hint") or "unknown")
        char_id = char_of(line_id, line)
        char = characters.get(char_id, {}) if cast_done else {}
        if want_chars:
            names = {char_id.lower(), str(char.get("name") or "").lower(), hint.lower()}
            if not names & want_chars:
                continue
        elif cast_done:
            if not char_id or char_id not in characters:
                skipped["not_cast"] += 1
                continue
            if not char.get("selected"):
                skipped["not_selected"] += 1
                continue
        picked.append((line_id, line, char_id))

    states: dict[str, _Bootstrap] = {}
    if strategy == "bootstrap":
        chars = {cid for _, _, cid in picked}
        voiced = {lid: char_of(lid, ln) for lid, ln in lines_all.items() if ln.get("kind") in VOICED_KINDS}
        states = _bootstrap_states(project, lines_all, {lid: c for lid, c in voiced.items() if c in chars},
                                   translations, decisions, history, follows, refs_by_char, analysis,
                                   {lid for lid, c in classes.items() if c in keep_classes})

    accent_map: dict[str, str] = {}
    if accent is not None:
        texts = {prepare(str(tr["dub_text"]), target, yo) for lid, _, _ in picked
                 if (tr := translations.get(lid)) and str(tr.get("dub_text") or "").strip()}
        accent_map = accent(sorted(texts))

    attention: list[str] = []
    waiting: list[str] = []
    jobs: list[Job] = []
    keep_rows: list[dict[str, Any]] = []
    for line_id, line, char_id in picked:
        uid = _line_uid(line)
        cls = classes.get(line_id)
        if cls in keep_classes and not _overridden(decisions.get(line_id)):
            skipped["keep_original"] += 1
            if (decisions.get(line_id) or {}).get("decision") != "keep_original":
                keep_rows.append({"id": line_id, "decision": "keep_original", "attempt": None, "by": "auto",
                                  "note": f"line class {cls}: the original audio is kept", "attention": False,
                                  "class": cls, "ts": now_iso()})
            continue
        tr = translations.get(line_id)
        if not tr or not str(tr.get("dub_text") or "").strip() or (tr.get("lang") and _primary(tr["lang"]) != _primary(target)):
            skipped["no_translation"] += 1
            continue
        text = prepare(str(tr["dub_text"]), target, yo)
        if accent is not None:
            text = render_stress(accent_map.get(text, text), mode)
        if not text:
            skipped["empty_text"] += 1
            continue

        rows = sorted(history.get(line_id, []), key=lambda r: int(r.get("attempt") or 0))
        last_attempt = max([int(r.get("attempt") or 0) for r in rows], default=0)
        ok_rows = [r for r in rows if _usable(project, r)]
        latest_ok = ok_rows[-1] if ok_rows else None
        ok_attempt = int(latest_ok.get("attempt") or 0) if latest_ok else 0
        decision = decisions.get(line_id) or {}
        pending = (
            decision.get("decision") in ("regen", "reref")
            and latest_ok is not None
            and int(decision.get("attempt") or ok_attempt) >= ok_attempt
        )
        if args.attempt_next and not pending:
            continue

        prefer: str | None = None
        avoid: str | None = None
        reason = "new" if not rows else "retry"
        if pending and args.attempt_next and latest_ok is not None:
            reason = str(decision["decision"])
            if reason == "reref":  # switch the voice prompt: line -> char, char -> another char ref or the line
                prev_mode = str(latest_ok.get("ref_mode") or "")
                avoid = str(latest_ok.get("ref_id") or "")
                if prev_mode.startswith("line"):
                    prefer = "char"
                elif len(refs_by_char.get(char_id, [])) < 2:
                    prefer = "line"
                else:
                    prefer = "char"
        elif not args.force and latest_ok is not None:
            # done = this text was already spoken by this engine/model; the voice prompt of that
            # attempt stands (it may come from a QA reref), new prompts need --force or a decision
            same = (
                latest_ok.get("text") == text
                and _family(latest_ok.get("engine")) == _family(engine.name)
                and latest_ok.get("model") == engine.model_name
                and _primary(latest_ok.get("lang")) == _primary(target)
            )
            if same:
                skipped["done"] += 1
                continue
            if latest_ok.get("text") != text:
                reason = "retranslated"
        elif args.force and rows:
            reason = "force"
        tries_for_text = sum(1 for r in rows if r.get("text") == text)
        if not args.force and tries_for_text >= (min(max_attempts, short_attempts) if cls == "short_word" else max_attempts):
            skipped["max_attempts"] += 1
            attention.append(line_id)
            continue

        prompt: Prompt | None = None
        ref_reason: str | None = None
        bucket: str | None = None
        avoid_bucket = _avoid_bucket(reason, latest_ok, qa_latest.get(line_id))
        boot = states.get(char_id)
        orig = _orig_wav(project, line) if cls == "short_word" and prefer is None else None
        if orig is not None:  # a short word: the line itself carries the delivery, no target-language prompt
            src_lang = str((line.get("text") or {}).get("src_lang") or _primary(line.get("locale")) or "")
            src = str((line.get("text") or {}).get("src") or "")
            dur = float((line.get("audio") or {}).get("dur_s") or 0.0) or float(probe(orig)["dur_s"])
            ref = Ref("line", f"line:{uid}", orig, prepare(src, src_lang, "keep") if src else "", src_lang, dur)
            ref_reason = "short_word: the line itself is the voice reference"
        elif boot is not None and boot.long_ref is not None and prefer is None:
            src_lang = str((line.get("text") or {}).get("src_lang") or _primary(line.get("locale")) or "")
            bucket, long_ref, tgt = boot.pick(line_id, avoid_bucket)
            if tgt is not None:
                ref = Ref("bootstrap-tgt", long_ref.ref_id, long_ref.wav, "", src_lang, long_ref.dur_s)
                prompt = tgt
            elif line_id in boot.phase1 or boot.phase1_done:
                ref = Ref("bootstrap-src", long_ref.ref_id, long_ref.wav, "", src_lang, long_ref.dur_s)
                if line_id not in boot.phase1:
                    ref_reason = "fewer accepted first lines than tts.bootstrap_min_accepted: no target-language prompt"
            else:
                skipped["bootstrap_wait"] += 1
                waiting.append(line_id)
                continue
        else:
            choice = _choose_ref(project, line, refs_by_char.get(char_id, []), "auto" if strategy == "bootstrap" else strategy,
                                 min_s, prefer, avoid, prepare)
            if choice is None:
                skipped["no_ref"] += 1
                attention.append(line_id)
                continue
            ref = choice
        audio = line.get("audio") or {}
        target_s = float(audio.get("dur_s") or ref.dur_s or 3.0)
        job = Job(line, line_id, uid, char_id, text, last_attempt + 1, ref, reason, target_s,
                  (char_id, str(line.get("scene") or ""), int(line.get("order") or 0), line_id))
        job.prompt, job.ref_reason = prompt, ref_reason
        job.line_class = cls
        job.bucket = bucket
        if cls == "short_word":
            job.best_of_min = short_best_of
        if ref.mode == "line" and engine.name != PREMIUM:  # VoxCPM2 takes the line as timbre only
            char_ref = _char_ref(project, refs_by_char.get(char_id, []), ref.lang, avoid, prepare)
            _check_icl(job, None if prefer == "line" or cls == "short_word" else char_ref)
            if job.ref.mode == "line":
                job.alt_ref = char_ref
        if not job.ref.text and engine.name in TRANSCRIPT_ENGINES and not job.ref.mode.startswith("bootstrap"):
            job.x_vector_only = True  # a prompt without a transcript can only give the timbre
        job.est_s = estimate_seconds(text, target)
        job.key = _job_key(project, engine, job)
        job.seed = _seed(line_id, job.attempt)
        job.out = project.path(layout.SYNTH_DIR, uid, f"a{job.attempt}.wav")
        jobs.append(job)
    jobs.sort(key=lambda j: j.sort_key)
    if keep_rows and not args.dry_run:
        _ensure_header(project.path(layout.DECISIONS), DECISIONS_SCHEMA)
        append_jsonl(project.path(layout.DECISIONS), keep_rows)
    return jobs, skipped, attention, waiting


def _check_icl(job: Job, char_ref: Ref | None) -> None:
    """A line transcript whose syllable rate over the original speech is implausible probably does
    not match the audio (alt take, ad-lib, wrong link): an ICL prompt with it tends to run away.
    Use the character reference instead, or the line in timbre-only mode."""
    rate = transcript_rate(job.ref)
    job.src_rate = _num(rate, 2)
    lo, hi = ICL_RATE_RANGE
    if lo <= rate <= hi:
        return
    shown = f"{rate:.1f}" if math.isfinite(rate) else "inf"
    reason = f"{job.ref_reason}; " if job.ref_reason else ""
    reason += f"transcript rate {shown} syl/s outside [{lo}, {hi}]"
    if char_ref is not None:
        job.ref = char_ref
        job.ref_reason = f"{reason}: character reference"
    else:
        job.x_vector_only = True
        job.ref_reason = f"{reason}: timbre only"


def _job_key(project: Project, engine: EngineChoice, job: Job) -> str:
    ref = job.ref
    parts: list[Any] = [engine.name, engine.model_name, _primary(project.target_lang), job.text, ref.ref_id,
                        project.rel(ref.wav), ref.wav.stat().st_size, ref.text]
    if job.prompt is not None:
        parts += [job.prompt.ref_id, job.prompt.text]
    return _sha(*parts)


def _engine_params(project: Project, engine: EngineChoice, job: Job) -> dict[str, Any]:
    params: dict[str, Any] = dict(project.setting("tts", "params", {}) or {})
    if engine.name in TOKEN_CAP_ENGINES:
        params.setdefault("max_new_tokens", token_cap(job.est_s, job.target_s))
    if job.x_vector_only:
        params["x_vector_only"] = True
    return params


def effective_strategy(configured: str, engine: str) -> tuple[str, str | None]:
    """ref_strategy for this engine: auto -> bootstrap on voxcpm; bootstrap needs a prompt engine."""
    if configured == "auto" and engine == PREMIUM:
        return "bootstrap", None
    if configured == "bootstrap" and engine not in PROMPT_ENGINES:
        return "auto", f"ref_strategy=bootstrap needs the {PREMIUM} engine: using auto (line/char) with {engine}"
    return configured, None


def _wait_hint(waiting: int) -> str:
    return (f"{waiting} lines wait for their character's target-language prompt (bootstrap): run "
            "`babeldub post`, `babeldub vc`, `babeldub qa`, then `babeldub synth` again")


def cmd_synth(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    catalog = _catalog()
    # GPU-first (contract §1.6): tts.device / --device override, else compute.device (auto -> cuda if present)
    device = args.device or project.setting("tts", "device") or resolve_device(project.setting("compute", "device", "auto"))
    engine_name = args.engine or str(project.setting("tts", "engine", AUTO) or AUTO)
    notes: list[str] = []
    if engine_name == AUTO:
        engine_name, auto_note = auto_engine(project.target_lang, str(device), catalog)
        notes.append(auto_note)
    engine_name, fallback_note = engine_for_device(engine_name, str(device), catalog, project.target_lang)
    if fallback_note:
        notes.append(fallback_note)
    engine = resolve_engine(project, engine_name, catalog, check_env=not args.dry_run)
    if engine.name == "dummy":
        device = "cpu"
    strategy, strategy_note = effective_strategy(str(project.setting("tts", "ref_strategy", "auto") or "auto"), engine.name)
    if strategy_note:
        notes.append(strategy_note)
    best_of = max(1, int(args.best_of or project.setting("tts", "best_of", 1) or 1))
    accent: Accenter | None = None
    if (_primary(project.target_lang) == "ru" and _on(project.setting("tts", "stress", "off"))
            and stress_mode(project, engine, catalog) != "none"):
        accent = Accenter(project, compute=not args.dry_run)
    jobs, skipped, attention, waiting = _plan_synth(project, args, engine, strategy, catalog, accent)
    if accent is not None and accent.note:
        notes.append(accent.note)
    planned = len(jobs)
    if args.limit is not None:
        jobs = jobs[: max(0, args.limit)]
    by_ref = Counter(j.ref_mode for j in jobs)

    if args.dry_run:
        return emit({
            "stage": "synth", "dry_run": True, "engine": engine.name, "model": engine.model_name,
            "model_source": engine.model_source, "env_ready": engine.python.exists(), "planned": planned,
            "would_run": len(jobs), "by_ref": dict(by_ref), "ref_strategy": strategy, "best_of": best_of,
            "by_char": dict(Counter(j.char_id for j in jobs).most_common(12)),
            "skipped": dict(skipped), "attention": clip(attention), "notes": notes,
        })
    if not jobs:
        status = "attention" if attention else "done"
        project.mark_stage("synth", status, done=0, failed=0, remaining=0, skipped=dict(skipped),
                           bootstrap_wait=len(waiting))
        payload_empty: dict[str, Any] = {
            "stage": "synth", "engine": engine.name, "model": engine.model_name, "done": 0, "failed": 0,
            "remaining": 0, "skipped": dict(skipped), "attention": clip(attention),
            "next": _wait_hint(len(waiting)) if waiting else "babeldub post", "notes": notes,
        }
        return emit(payload_empty, EXIT_ATTENTION if attention else EXIT_OK)

    cache_dir = project.path("synth", "refcache")
    for job in jobs:
        if job.ref.mode.startswith("bootstrap"):
            continue  # the long timbre reference is meant to be ~15-20 s
        job.ref, trimmed = _fit_ref(job.ref, cache_dir, ref_max_s(engine.name))
        if trimmed:
            job.ref_trimmed = True
            if engine.name in TRANSCRIPT_ENGINES:
                job.x_vector_only = True  # cut audio no longer matches its transcript

    results_path = project.path(layout.SYNTH_RESULTS)
    _ensure_header(results_path, SYNTH_SCHEMA)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = project.path(layout.LOGS_DIR, f"synth-{stamp}.log")
    device_note: str | None = None
    lock = GpuLock(project.path(layout.GPU_LOCK), owner=f"synth:{engine.name}", wait_s=args.wait_lock)
    run = _SynthRun(project, engine, results_path, cache_dir, best_of)
    batch_size = 1
    project.mark_stage("synth", "running", planned=planned, engine=engine.name)
    started = time.perf_counter()
    use_lock = device != "cpu"
    if use_lock:
        lock.acquire()
    try:
        load_timeout = 3600.0 if engine.model_source == "hf" else 1200.0
        with WorkerClient(engine.python, engine.worker, log_path=log_path, load_timeout=load_timeout) as client:
            try:
                reply = client.load({
                    "op": "load", "model": engine.model_ref, "device": device,
                    "dtype": str(project.setting("tts", "dtype", "bf16")),
                    "options": dict(project.setting("tts", "options", {}) or {}),
                })
            except WorkerError as err:
                project.mark_stage("synth", "failed", error=str(err), log=project.rel(log_path))
                err.extra.setdefault("log", project.rel(log_path))
                raise
            device_note = device_warning(device, reply.get("device"))
            if (reply.get("info") or {}).get("batch"):
                wanted = args.batch or project.setting("tts", "batch_size") or DEFAULT_BATCH
                batch_size = max(1, int(wanted))
            if batch_size > 1:  # similar estimated lengths in one batch: less padding, a lower shared cap
                jobs.sort(key=lambda j: (j.char_id, j.est_s, j.id))
            run.execute(client, jobs, batch_size)
    finally:
        if use_lock:
            lock.release()

    t = run.tally
    remaining = planned - t.processed + t.failed  # failed lines get a new attempt on the next run
    elapsed = time.perf_counter() - started
    status = "attention" if (t.failed or attention) else ("running" if remaining else "done")
    summary: dict[str, Any] = {
        "done": t.done, "failed": t.failed, "remaining": remaining,
        "rtf": _num(sum(t.rtfs) / len(t.rtfs)) if t.rtfs else None,
        "audio_s": round(t.audio_s, 1), "elapsed_s": round(elapsed, 1), "batch": batch_size, "best_of": best_of,
    }
    if t.vram_peak:
        summary["vram_peak_gb"] = round(t.vram_peak, 2)
    project.mark_stage("synth", status, engine=engine.name, model=engine.model_name, bootstrap_wait=len(waiting), **summary)
    payload: dict[str, Any] = {
        "stage": "synth", "engine": engine.name, "model": engine.model_name, "model_source": engine.model_source,
        **summary, "by_ref": dict(by_ref), "skipped": dict(skipped),
        "results": layout.SYNTH_RESULTS, "log": project.rel(log_path),
    }
    if t.errors:
        payload["errors"] = clip(t.errors, 5)
    if attention:
        payload["attention"] = clip(attention)
    if t.runaway:
        payload["runaway"] = clip(t.runaway)
        payload["runaway_recovered"] = t.recovered
    if t.aborted:
        payload["aborted"] = t.aborted
    if device_note:
        notes.append(device_note)
    if notes:
        payload["notes"] = notes
    if remaining:
        payload["next"] = "babeldub synth"
    else:
        payload["next"] = _wait_hint(len(waiting)) if waiting else "babeldub post"
    return emit(payload, EXIT_ATTENTION if (t.failed or attention or t.aborted) else EXIT_OK)


@dataclass
class _Tally:
    done: int = 0
    failed: int = 0
    processed: int = 0
    audio_s: float = 0.0
    vram_peak: float = 0.0
    rtfs: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    runaway: list[str] = field(default_factory=list)  # lines whose first output ran away
    recovered: int = 0  # ... and got a usable take from a retry
    streak: list[str] = field(default_factory=list)
    aborted: str | None = None


class _SynthRun:
    """Sends jobs to the worker (batched when the engine supports it) and appends results.jsonl rows.

    Best of N: every line is generated `best_of` times with different seeds; the take whose speech
    span (energy VAD, -42 dB) is closest in log ratio to the original's speech span is kept.
    Runaway policy: an output that hit the token cap or is far longer than any plausible reading
    is discarded; when every candidate ran away the line is retried alone in timbre-only mode on the
    same reference, then with the character reference in ICL mode; if every try runs away the row
    is an error "runaway".
    """

    def __init__(self, project: Project, engine: EngineChoice, results_path: Path, cache_dir: Path, best_of: int = 1):
        self.project = project
        self.engine = engine
        self.results_path = results_path
        self.cache_dir = cache_dir
        self.best_of = max(1, best_of)
        self.tally = _Tally()

    def request_for(self, job: Job, seed: int | None = None, out: Path | None = None) -> dict[str, Any]:
        job.out.parent.mkdir(parents=True, exist_ok=True)
        req: dict[str, Any] = {
            "op": "synth", "id": job.id, "text": job.text, "lang": self.project.target_lang,
            "ref_wav": str(job.ref.wav), "ref_text": "" if job.x_vector_only else job.ref.text,
            "ref_lang": job.ref.lang, "out": str(out or job.out), "seed": job.seed if seed is None else seed,
            "params": _engine_params(self.project, self.engine, job),
        }
        if job.prompt is not None:  # target-language continuation prompt (VoxCPM2 prompt_wav_path + prompt_text)
            req["prompt_wav"] = str(job.prompt.wav)
            req["prompt_text"] = job.prompt.text
        return req

    def n_for(self, job: Job) -> int:
        """Candidates of one line: tts.best_of, more for a short_word line."""
        return max(self.best_of, job.best_of_min)

    def candidate(self, job: Job, k: int) -> tuple[int, Path]:
        """Seed and output file of candidate k (k = 0 is the line's own seed and file)."""
        if self.n_for(job) == 1:
            return job.seed, job.out
        return job.seed + k, job.out.with_name(f"a{job.attempt}c{k}.wav")

    def record(
        self, job: Job, reply: dict[str, Any], seed: int, batch: int, discarded: list[dict[str, Any]] | None = None,
        best: dict[str, Any] | None = None,
    ) -> None:
        t = self.tally
        t.processed += 1
        ok = bool(reply.get("ok")) and job.out.exists()
        project = self.project
        row: dict[str, Any] = {
            "id": job.id, "attempt": job.attempt, "engine": self.engine.name, "model": self.engine.model_name,
            "wav": project.rel(job.out) if ok else None, "sr": reply.get("sr"), "dur_s": reply.get("dur_s"),
            "seed": seed, "ref_id": job.ref.ref_id, "ref_mode": job.ref_mode,
            "ref_wav": project.rel(job.ref.wav), "ref_trimmed": job.ref_trimmed, "text": job.text,
            "lang": project.target_lang, "char_id": job.char_id, "reason": job.reason, "key": job.key,
            "status": "ok" if ok else "error", "rtf": reply.get("rtf"), "ts": now_iso(),
            "est_s": round(job.est_s, 2),
        }
        cap = _engine_params(project, self.engine, job).get("max_new_tokens")
        if cap is not None:
            row["max_new_tokens"] = cap
        if job.prompt is not None:
            row["prompt_id"] = job.prompt.ref_id
            row["prompt_wav"] = project.rel(job.prompt.wav)
        if job.bucket:
            row["bucket"] = job.bucket
        if job.line_class:
            row["class"] = job.line_class
        if job.ref_reason:
            row["ref_reason"] = job.ref_reason
        if job.src_rate is not None:
            row["src_rate"] = job.src_rate
        if batch > 1:
            row["batch"] = batch
        if best:
            row["best_of"] = best
        if discarded:
            row["retry_reason"] = "runaway"
            row["discarded"] = discarded
        if not ok:
            row["error"] = str(reply.get("error") or "worker reported success but wrote no file")
            if discarded and job.out.exists():
                job.out.unlink()  # the last runaway take is not kept
        if reply.get("hit_token_limit"):
            row["hit_token_limit"] = True
        if reply.get("vram_peak_gb") is not None:
            row["vram_peak_gb"] = reply["vram_peak_gb"]
            t.vram_peak = max(t.vram_peak, float(reply["vram_peak_gb"]))
        append_jsonl(self.results_path, [row])
        if ok:
            t.done += 1
            t.streak = []
            t.audio_s += float(reply.get("dur_s") or 0.0)
            if reply.get("rtf") is not None:
                t.rtfs.append(float(reply["rtf"]))
        else:
            t.failed += 1
            t.errors.append(f"{job.uid}: {row['error'][:160]}")
            t.streak.append(row["error"][:80])
            if len(t.streak) >= 5 and len(set(t.streak[-5:])) == 1:
                t.aborted = f"5 consecutive failures: {t.streak[-1]}"

    def call(self, client: WorkerClient, job: Job, seed: int | None = None, out: Path | None = None) -> dict[str, Any]:
        try:
            return client.request(self.request_for(job, seed, out), timeout=max(240.0, 40.0 * job.target_s))
        except WorkerError as err:
            return {"ok": False, "error": str(err)}

    def finish(self, client: WorkerClient, job: Job, reply: dict[str, Any], seed: int, batch: int) -> None:
        """Record a reply; a runaway is discarded and the line retried alone first."""
        if not (reply.get("ok") and is_runaway(reply, job.est_s, job.target_s)):
            self.record(job, reply, seed, batch)
            return
        self.tally.runaway.append(job.id)
        reply, discarded = self.recover(client, job, reply)
        if reply.get("ok"):
            self.tally.recovered += 1
        self.record(job, reply, job.seed, 1, discarded)

    def target_speech(self, job: Job) -> float:
        """Speech span of the original line (energy VAD), the length best-of aims at."""
        if job.target_speech is None:
            orig = _orig_wav(self.project, job.line)
            span = 0.0
            if orig is not None:
                try:
                    data, sr = read_audio(orig)
                    span = speech_span(data, sr)
                except (RuntimeError, OSError, ValueError):
                    span = 0.0
            job.target_speech = span if span > 0 else job.target_s
        return job.target_speech

    def choose(self, client: WorkerClient, job: Job, cands: list[tuple[int, dict[str, Any], Path]], batch: int) -> None:
        """Keep the best usable candidate as the line's take (job.out); record one results row."""
        if len(cands) == 1:
            seed, reply, out = cands[0]
            if out != job.out and out.exists():
                out.replace(job.out)
            self.finish(client, job, reply, seed, batch)
            return
        usable: list[tuple[float, int, dict[str, Any], Path, float]] = []
        runaways = 0
        for seed, reply, out in cands:
            if not (reply.get("ok") and out.exists()):
                continue
            if is_runaway(reply, job.est_s, job.target_s):
                runaways += 1
                continue
            try:
                data, sr = read_audio(out)
                span = speech_span(data, sr)
            except (RuntimeError, OSError, ValueError):
                continue
            if span > 0:
                usable.append((length_score(span, self.target_speech(job)), seed, reply, out, span))
        if not usable:  # nothing usable: the first candidate goes through the runaway/error path
            seed, reply, out = cands[0]
            for _, _, other in cands[1:]:
                other.unlink(missing_ok=True)
            if out.exists():
                out.replace(job.out)
            self.finish(client, job, {**reply, "wav": str(job.out)} if reply.get("ok") else reply, seed, batch)
            return
        best = min(usable, key=lambda c: (c[0], c[1]))
        best[3].replace(job.out)
        for _, _, other in cands:
            if other != job.out:
                other.unlink(missing_ok=True)
        info = {"n": len(cands), "usable": len(usable), "picked_seed": best[1],
                "speech_s": [round(c[4], 3) for c in usable], "target_speech_s": round(self.target_speech(job), 3)}
        if runaways:
            info["runaways"] = runaways
        self.record(job, {**best[2], "wav": str(job.out)}, best[1], batch, best=info)

    def _discarded(self, job: Job, reply: dict[str, Any]) -> dict[str, Any]:
        return {"ref_mode": job.ref_mode, "ref_id": job.ref.ref_id, "dur_s": reply.get("dur_s"),
                "hit_token_limit": bool(reply.get("hit_token_limit"))}

    def recover(self, client: WorkerClient, job: Job, reply: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Runaway ladder: same reference timbre-only, then the character reference (ICL)."""
        discarded = [self._discarded(job, reply)]
        steps: list[str] = []
        if not job.x_vector_only and self.engine.name in TRANSCRIPT_ENGINES:
            steps.append("xvec")
        if job.alt_ref is not None and job.ref.mode == "line":
            steps.append("char")
        for step in steps:
            if step == "xvec":
                job.x_vector_only = True
            else:
                assert job.alt_ref is not None
                try:
                    job.ref, job.ref_trimmed = _fit_ref(job.alt_ref, self.cache_dir, ref_max_s(self.engine.name))
                    job.key = _job_key(self.project, self.engine, job)
                except (OSError, RuntimeError, ValueError) as exc:  # unreadable reference file
                    return {"ok": False, "error": f"character reference: {type(exc).__name__}: {exc}"}, discarded
                job.x_vector_only = job.ref_trimmed or not job.ref.text
                job.alt_ref = None
            reply = self.call(client, job)
            if not reply.get("ok") or not is_runaway(reply, job.est_s, job.target_s):
                return reply, discarded
            discarded.append(self._discarded(job, reply))
        return {"ok": False, "error": "runaway"}, discarded

    def single(self, client: WorkerClient, job: Job) -> None:
        cands: list[tuple[int, dict[str, Any], Path]] = []
        for k in range(self.n_for(job)):
            seed, out = self.candidate(job, k)
            cands.append((seed, self.call(client, job, seed, out), out))
            if not client.alive and client.restarts >= client.max_restarts:
                break
        self.choose(client, job, cands, 1)
        if not client.alive and client.restarts >= client.max_restarts:
            self.tally.aborted = "worker keeps crashing"

    def batch(self, client: WorkerClient, chunk: list[Job]) -> bool:
        """One generate call per candidate round for several lines. False = a batch failed as a whole
        (the caller falls back to single lines)."""
        cands: dict[str, list[tuple[int, dict[str, Any], Path]]] = {j.id: [] for j in chunk}
        for k in range(self.n_for(chunk[0])):
            seed = chunk[0].seed + k  # a batch shares one seed
            outs = [self.candidate(j, k)[1] for j in chunk]
            msg = {"op": "synth_batch", "seed": seed,
                   "items": [self.request_for(j, seed, out) for j, out in zip(chunk, outs, strict=True)]}
            try:
                reply = client.request(msg, timeout=max(300.0, 60.0 * max(j.target_s for j in chunk)))
            except WorkerError:
                return False
            results = reply.get("results")
            if not reply.get("ok") or not isinstance(results, list) or len(results) != len(chunk):
                return False
            for job, item, out in zip(chunk, results, outs, strict=True):
                item = item if isinstance(item, dict) else {"ok": False, "error": "bad batch item"}
                cands[job.id].append((seed, item, out))
        for job in chunk:
            self.choose(client, job, cands[job.id], len(chunk))
        return True

    def execute(self, client: WorkerClient, jobs: list[Job], batch_size: int) -> None:
        i = 0
        while i < len(jobs) and not self.tally.aborted:
            chunk = jobs[i : i + batch_size]
            i += len(chunk)
            if len(chunk) > 1 and len({self.n_for(j) for j in chunk}) == 1:
                if self.batch(client, chunk):
                    continue
                batch_size = max(1, batch_size // 2)  # e.g. out of memory: smaller batches, these lines one by one
            for job in chunk:
                if self.tally.aborted:
                    break
                self.single(client, job)


# ---- post ---------------------------------------------------------------------------------
def _original_audio(project: Project, line: dict[str, Any], fallback_s: float, fallback_sr: int) -> tuple[np.ndarray, int]:
    """The original line, or silence of its declared length/format when the file is missing."""
    orig_path = _orig_wav(project, line)
    if orig_path is not None:
        return read_audio(orig_path)
    audio = line.get("audio") or {}
    sr = int(audio.get("sr") or fallback_sr)
    dur = float(audio.get("dur_s") or fallback_s)
    return np.zeros((max(1, int(round(dur * sr))), int(audio.get("ch") or 1)), dtype=np.float32), sr


def _vad_segments(project: Project) -> dict[str, list[list[float]]]:
    """Original speech segments per line id from inventory/analysis.jsonl (empty when it does not exist)."""
    segments: dict[str, list[list[float]]] = {}
    for line_id, row in latest_by(read_jsonl(project.path(ANALYSIS))).items():
        try:
            segs = [[float(s), float(e)] for s, e in row.get("vad_segments") or []]
        except (TypeError, ValueError):
            continue
        if segs:
            segments[line_id] = segs
    return segments


def _post_line(
    project: Project, line: dict[str, Any], res: dict[str, Any], settings: dict[str, float], method: str | None,
    segments: list[list[float]] | None = None,
) -> dict[str, Any]:
    raw_path = _abs(project, res.get("wav"))
    if raw_path is None or not raw_path.exists():
        raise BabelDubError(f"synth file missing: {res.get('wav')}")
    raw, sr = read_audio(raw_path)
    orig, o_sr = _original_audio(project, line, raw.shape[0] / sr, sr)

    def stretcher(src: Path, dst: Path, ratio: float) -> str:
        return time_stretch(src, dst, ratio, method=method)

    fixed = bool(line.get("fixed_length"))
    try:
        fit = fit_segments(
            raw, sr, orig, o_sr, tempo_min=settings["tempo_min"], tempo_max=settings["tempo_max"],
            tolerance=settings["tolerance"], ceiling_db=settings["true_peak_db"], stretcher=stretcher,
            fixed_length=fixed, orig_segments=segments, default_level=DEFAULT_LEVEL_LUFS,
        )
    except ValueError as exc:  # silent synthesis
        raise BabelDubError(str(exc)) from exc
    # the same limits as QA's duration check
    over = (fit.ratio > 1.0 + settings["fixed_length_tolerance"] or not fit.fits) if fixed \
        else fit.ratio > settings["max_duration_ratio"]
    out_path = project.path(layout.POST_DIR, f"{_line_uid(line)}.wav")
    write_wav(out_path, fit.audio, fit.sr, subtype="PCM_16")
    frames = fit.audio.shape[0]
    return {
        "id": line["id"], "attempt": res.get("attempt"), "wav": project.rel(out_path),
        # dur_s: whole file; target_s: speech span of the original; ratio: dub speech span / target_s
        "dur_s": round(frames / fit.sr, 4), "orig_dur_s": round(orig.shape[0] / o_sr, 4),
        "target_s": round(fit.target_s, 4), "ratio": round(fit.ratio, 4), "ratio_raw": round(fit.ratio_raw, 4),
        "tempo": round(fit.tempo, 4), "stretch": round(1.0 / fit.tempo, 4), "stretch_method": fit.method,
        "lufs_in": _num(fit.level_in, 2), "lufs_out": _num(fit.level_out, 2), "level_ref": _num(fit.level_ref, 2),
        "measure": fit.measure, "peak_db": _num(fit.peak_db, 2),
        "lead_s": round(fit.lead_s, 4), "trail_s": round(fit.trail_s, 4),
        "pause_cut_s": round(fit.pause_cut_s, 4), "onset_deltas": [round(d, 3) for d in fit.onset_deltas],
        "chunk_tempo": {"n": len(fit.chunk_tempos), "min": round(min(fit.chunk_tempos), 4),
                        "max": round(max(fit.chunk_tempos), 4)},
        "sr": fit.sr, "ch": int(fit.audio.shape[1]), "frames": frames, "exact": fit.exact,
        "fixed_length": fixed, "over_length": bool(over),
        "src_wav": res.get("wav"), "src_key": res.get("key"), "ts": now_iso(),
    }


def post_settings(project: Project) -> dict[str, float]:
    settings = {
        "tempo_min": float(project.setting("post", "tempo_min", 1.0)),
        "tempo_max": float(project.setting("post", "tempo_max", 1.15)),
        "tolerance": float(project.setting("post", "tolerance", 0.03)),
        "fixed_length_tolerance": float(project.setting("post", "fixed_length_tolerance", 0.03)),
        "true_peak_db": float(project.setting("post", "true_peak_db", -1.0)),
        "max_duration_ratio": float(project.setting("qa", "max_duration_ratio", 1.35)),
    }
    if not 0.5 <= settings["tempo_min"] <= 1.0 <= settings["tempo_max"] <= 2.0:
        raise BabelDubError("need 0.5 <= post.tempo_min <= 1 <= post.tempo_max <= 2", code=EXIT_USAGE,
                            hint="`babeldub set post.tempo_min=1.0 post.tempo_max=1.15`")
    return settings


def cmd_post(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    results = read_jsonl(project.path(layout.SYNTH_RESULTS))
    if not results:
        raise BabelDubError("no synthesized lines yet", code=EXIT_USAGE, hint="run `babeldub synth` first")
    lines = latest_by(read_jsonl(project.path(layout.LINES)))
    follows = follower_map(lines.values())
    latest: dict[str, dict[str, Any]] = {}
    for row in results:
        if "id" not in row or not _usable(project, row):
            continue
        prev = latest.get(row["id"])
        if prev is None or int(row.get("attempt") or 0) >= int(prev.get("attempt") or 0):
            latest[row["id"]] = row
    existing = latest_by(read_jsonl(project.path(layout.POST)))
    want = set(_csv(args.ids))
    settings = post_settings(project)
    segments = _vad_segments(project)
    work: list[tuple[dict[str, Any], dict[str, Any]]] = []
    up_to_date = missing_line = followers = 0
    for line_id in sorted(latest):
        res = latest[line_id]
        line = lines.get(line_id)
        if line is None:
            missing_line += 1
            continue
        if want and line_id not in want and _line_uid(line) not in want:
            continue
        if line_id in follows:  # takes synthesized before they became followers are not processed
            followers += 1
            continue
        prev = existing.get(line_id)
        prev_wav = _abs(project, (prev or {}).get("wav"))
        if (not args.force and prev and prev.get("attempt") == res.get("attempt")
                and prev.get("src_key") == res.get("key") and prev_wav is not None and prev_wav.exists()):
            up_to_date += 1
            continue
        work.append((line, res))
    pending = len(work)
    if args.limit is not None:
        work = work[: max(0, args.limit)]
    if args.dry_run:
        return emit({"stage": "post", "dry_run": True, "would_run": len(work), "pending": pending,
                     "up_to_date": up_to_date, "settings": settings})

    post_path = project.path(layout.POST)
    _ensure_header(post_path, POST_SCHEMA)
    done = 0
    over: list[str] = []
    not_exact: list[str] = []  # dubs that run past the original's length even at post.tempo_max
    errors: list[str] = []
    methods: Counter[str] = Counter()
    for line, res in work:
        try:
            row = _post_line(project, line, res, settings, args.stretch_method, segments.get(line["id"]))
        except BabelDubError as err:
            if err.code == EXIT_ENV:
                raise
            errors.append(f"{_line_uid(line)}: {err}")
            continue
        except (RuntimeError, ValueError, OSError) as err:  # soundfile/numpy failures on one file
            errors.append(f"{_line_uid(line)}: {type(err).__name__}: {err}")
            continue
        append_jsonl(post_path, [row])
        done += 1
        methods[row["stretch_method"]] += 1
        if row["over_length"]:
            over.append(row["id"])
        if not row["exact"]:
            not_exact.append(row["id"])
    remaining = pending - len(work)
    status = "attention" if (over or errors) else ("running" if remaining else "done")
    project.mark_stage("post", status, done=done, over_length=len(over), failed=len(errors), remaining=remaining)
    payload: dict[str, Any] = {
        "stage": "post", "done": done, "up_to_date": up_to_date, "failed": len(errors), "remaining": remaining,
        "over_length": clip(over), "stretch": dict(methods), "post": layout.POST,
    }
    if missing_line:
        payload["orphans"] = missing_line
    if followers:
        payload["follows"] = followers
    if errors:
        payload["errors"] = clip(errors, 5)
    payload["exact"] = done - len(not_exact)
    if not_exact:
        payload["not_exact"] = clip(not_exact)
    payload["next"] = "babeldub post" if remaining else "babeldub vc"
    return emit(payload, EXIT_ATTENTION if (over or errors) else EXIT_OK)


# ---- registration -------------------------------------------------------------------------
def register(sub: Any, common: Callable[[argparse.ArgumentParser], None]) -> None:
    p = sub.add_parser(
        "synth",
        help="dub translated lines in the original character's voice (TTS voice cloning)",
        description="Synthesize dub_text of selected lines; the voice prompt is the original line "
        "(ref_strategy=line) or the character's best reference (char).",
    )
    common(p)
    p.add_argument("--chars", help="only these characters (char_id or name, comma-separated)")
    p.add_argument("--ids", help="only these line ids or uids (comma-separated)")
    p.add_argument("--limit", type=int, help="synthesize at most N lines in this run")
    p.add_argument("--attempt-next", action="store_true", help="new attempt for lines with a QA regen/reref decision")
    p.add_argument("--engine", choices=(AUTO, *ENGINES), help="override tts.engine for this run")
    p.add_argument("--device", choices=("cuda", "cpu"), help="override the device (default: tts.device, else compute.device: GPU when present)")
    p.add_argument("--wait-lock", type=float, default=0.0, metavar="SECONDS", help="wait for a busy GPU lock")
    p.add_argument("--batch", type=int, metavar="N", help="lines per generate call (default tts.batch_size or 4)")
    p.add_argument("--best-of", type=int, metavar="N", help="candidates per line, the one closest to the original's "
                   "speech length wins (default tts.best_of)")
    p.add_argument("--force", action="store_true", help="new attempt even if an identical one exists")
    p.add_argument("--dry-run", action="store_true", help="show the plan without loading a model")
    p.set_defaults(func=cmd_synth)

    p = sub.add_parser(
        "post",
        help="fit duration (never slower, pauses then tempo), match loudness and format of synthesized lines to the originals",
    )
    common(p)
    p.add_argument("--ids", help="only these line ids or uids (comma-separated)")
    p.add_argument("--limit", type=int, help="process at most N lines")
    p.add_argument("--force", action="store_true", help="reprocess lines that are up to date")
    p.add_argument("--dry-run", action="store_true", help="count work without writing files")
    p.add_argument("--stretch-method", choices=STRETCH_METHODS, help="force a time-stretch tool")
    p.set_defaults(func=cmd_post)
