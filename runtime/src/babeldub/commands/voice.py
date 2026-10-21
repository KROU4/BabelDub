"""Voice stages: `synth` (dub in the ORIGINAL character's voice) and `post` (fit, level, format).

synth  For every selected line the voice prompt is, by default, the original line itself with its
       source transcript (ref_strategy=line): the TTS engine clones timbre AND the emotion/delivery
       of that exact phrase and speaks the translation with it. Short, noisy or untranscribed lines
       use the character's best clean reference (char). One worker process per run (the model loads
       once), lines grouped by character, results appended to synth/results.jsonl as they finish.
       ICL needs the exact transcript of the prompt: a line transcript with an implausible syllable
       rate goes to the char reference or timbre-only mode. Generation is capped per line from the
       text estimate and the original; a runaway take is discarded and retried (see _SynthRun).
       Alt takes/duplicates (core/takes.py followers) are skipped: the build reuses their leader.
post   Trims silence, fits the duration to the original's speech (pitch-preserving stretch within
       post.max_stretch), restores the original's edge silences, matches loudness (BS.1770 LUFS,
       RMS for clips < 0.4 s) under a true-peak ceiling and converts to the original sr/channels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from babeldub.audio.dsp import (
    fade,
    level_db,
    match_loudness,
    pad_silence,
    resample,
    speech_seconds,
    to_channels,
    to_mono,
    trim_silence,
    true_peak_db,
    voiced_bounds,
)
from babeldub.audio.io import probe, read_audio, write_wav
from babeldub.audio.stretch import METHODS as STRETCH_METHODS
from babeldub.audio.stretch import time_stretch
from babeldub.core import layout
from babeldub.core.compute import device_warning, resolve_device
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import append_jsonl, latest_by, now_iso, read_jsonl, write_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_ENV, EXIT_OK, EXIT_USAGE, BabelDubError, clip, emit
from babeldub.core.paths import env_python, models_dir
from babeldub.core.project import Project
from babeldub.core.takes import follower_map
from babeldub.text.syllables import SYLLABLE_RATE, count_syllables
from babeldub.workers.client import GpuLock, WorkerClient, WorkerError

VOICED_KINDS = ("dialogue", "cutscene", "bark")
ENGINES = ("qwen3tts-fast", "qwen3tts", "chatterbox", "dummy")
# same models and the same output for one seed: a take of one is "done" for the other
ENGINE_FAMILY = {"qwen3tts-fast": "qwen3tts"}
REF_MAX_S = 15.0  # longer references are cut at a pause (models are trained on 3-15 s prompts)
EDGE_PAD_S = 0.03  # silence kept around speech when trimming synthesized audio
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

# Fallback when data/models.yaml or babeldub.envsetup is unavailable.
BUILTIN_ENGINES: dict[str, dict[str, Any]] = {
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
    "dummy": {"env": None, "worker": "tts_dummy.py", "languages": ["*"], "model": "dummy", "repo": "dummy"},
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
    text = _FALLBACK_MARKUP.sub(" ", text.replace("\\n", " ").replace("\n", " "))
    text = re.sub(r"\s+([,.!?;:…])", r"\1", re.sub(r"\s+", " ", text))
    return re.sub(r"^[\s:;,]+", "", text).strip()


def _text_preparer() -> Callable[[str, str, str], str]:
    """babeldub.text.normalize.prepare_for_tts when available, else a markup-only cleaner."""
    try:
        from babeldub.text.normalize import prepare_for_tts
    except ImportError:
        return lambda text, lang, yo: _fallback_clean(text)

    def prepare(text: str, lang: str, yo: str) -> str:
        try:
            return prepare_for_tts(text, lang, yo=yo)
        except (TypeError, ValueError):
            return _fallback_clean(text)

    return prepare


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
                    cuda_only=cat.cuda_only, fallback=cat.fallback)
    return spec


def engine_for_device(engine: str, device: str, catalog: Any | None) -> tuple[str, str | None]:
    """The engine itself, or its fallback (with a note) when it is CUDA only and cannot run here."""
    if engine not in BUILTIN_ENGINES:
        return engine, None
    spec = _engine_spec(engine, catalog)
    fallback = spec.get("fallback")
    if not spec.get("cuda_only") or not fallback:
        return engine, None
    if not str(device).startswith("cuda"):
        return fallback, f"{engine} runs on CUDA only: using the official {fallback} engine on {device}"
    if spec.get("env") and not env_python(spec["env"]).exists():
        fallback_env = _engine_spec(fallback, catalog).get("env")
        if fallback_env and not env_python(fallback_env).exists():
            return engine, None  # neither is installed: the env error should point to the fast engine
        return fallback, (f"{engine} env is not installed (`babeldub setup --engines {engine}`): "
                          f"using the official {fallback} engine")
    return engine, None


def resolve_engine(project: Project, engine: str, catalog: Any | None = None, check_env: bool = True) -> EngineChoice:
    """Engine venv, worker script and model for this project; raises EXIT_ENV if the venv is missing."""
    if engine not in ENGINES:
        raise BabelDubError(f"unknown TTS engine '{engine}'", code=EXIT_USAGE, known=list(ENGINES))
    spec = _engine_spec(engine, catalog)
    lang = _primary(project.target_lang)
    languages = [str(x) for x in spec["languages"]]
    if "*" not in languages and lang not in languages:
        others = [e for e in ("qwen3tts", "chatterbox") if e != engine and lang in BUILTIN_ENGINES[e]["languages"]]
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
    mode: str  # line | char
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

    @property
    def ref_mode(self) -> str:
        """line | line-xvec | char | char-xvec (-xvec: timbre only, the transcript is not used)."""
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


def _fit_ref(ref: Ref, cache_dir: Path) -> tuple[Ref, bool]:
    """A reference longer than REF_MAX_S is cut (True: the cut audio no longer matches its text)."""
    if ref.dur_s > REF_MAX_S or (ref.dur_s == 0 and probe(ref.wav)["dur_s"] > REF_MAX_S):
        return Ref(ref.mode, ref.ref_id, _cut_reference(ref.wav, cache_dir), ref.text, ref.lang, REF_MAX_S), True
    return ref, False


# ---- synth --------------------------------------------------------------------------------
def _plan_synth(project: Project, args: argparse.Namespace, engine: EngineChoice) -> tuple[list[Job], Counter[str], list[str]]:
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
    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(project.path(layout.SYNTH_RESULTS)):
        if "id" in row:
            history[row["id"]].append(row)

    target = project.target_lang
    tts = project.data.get("tts", {})
    strategy = str(tts.get("ref_strategy") or "auto")
    min_s = float(tts.get("line_ref_min_s", 1.6))
    max_attempts = int(tts.get("max_attempts", 3))
    yo = str(tts.get("yo") or "keep")
    prepare = _text_preparer()
    want_chars = {c.lower() for c in _csv(args.chars)}
    want_ids = set(_csv(args.ids))
    follows = follower_map(lines_all.values())

    skipped: Counter[str] = Counter()
    attention: list[str] = []
    jobs: list[Job] = []
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
        char_id = str((assign.get(line_id) or {}).get("char_id") or "") if cast_done else hint
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
        tr = translations.get(line_id)
        if not tr or not str(tr.get("dub_text") or "").strip() or (tr.get("lang") and _primary(tr["lang"]) != _primary(target)):
            skipped["no_translation"] += 1
            continue
        text = prepare(str(tr["dub_text"]), target, yo)
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
        ref = _choose_ref(project, line, refs_by_char.get(char_id, []), strategy, min_s, prefer, avoid, prepare)
        if ref is None:
            skipped["no_ref"] += 1
            attention.append(line_id)
            continue
        audio = line.get("audio") or {}
        target_s = float(audio.get("dur_s") or ref.dur_s or 3.0)
        job = Job(line, line_id, uid, char_id, text, last_attempt + 1, ref, reason, target_s,
                  (char_id, str(line.get("scene") or ""), int(line.get("order") or 0), line_id))
        if not args.force and not (pending and args.attempt_next) and latest_ok is not None:
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
                job.reason = "retranslated"
        elif args.force and rows:
            job.reason = "force"
        tries_for_text = sum(1 for r in rows if r.get("text") == text)
        if not args.force and tries_for_text >= max_attempts:
            skipped["max_attempts"] += 1
            attention.append(line_id)
            continue
        if ref.mode == "line":
            char_ref = _char_ref(project, refs_by_char.get(char_id, []), ref.lang, avoid, prepare)
            _check_icl(job, None if prefer == "line" else char_ref)
            if job.ref.mode == "line":
                job.alt_ref = char_ref
        if not job.ref.text and engine.name in TRANSCRIPT_ENGINES:
            job.x_vector_only = True  # a prompt without a transcript can only give the timbre
        job.est_s = estimate_seconds(text, target)
        job.key = _job_key(project, engine, job)
        job.seed = _seed(line_id, job.attempt)
        job.out = project.path(layout.SYNTH_DIR, uid, f"a{job.attempt}.wav")
        jobs.append(job)
    jobs.sort(key=lambda j: j.sort_key)
    return jobs, skipped, attention


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
    reason = f"transcript rate {shown} syl/s outside [{lo}, {hi}]"
    if char_ref is not None:
        job.ref = char_ref
        job.ref_reason = f"{reason}: character reference"
    else:
        job.x_vector_only = True
        job.ref_reason = f"{reason}: timbre only"


def _job_key(project: Project, engine: EngineChoice, job: Job) -> str:
    ref = job.ref
    return _sha(engine.name, engine.model_name, _primary(project.target_lang), job.text, ref.ref_id,
                project.rel(ref.wav), ref.wav.stat().st_size, ref.text)


def _engine_params(project: Project, engine: EngineChoice, job: Job) -> dict[str, Any]:
    params: dict[str, Any] = dict(project.setting("tts", "params", {}) or {})
    if engine.name in TOKEN_CAP_ENGINES:
        params.setdefault("max_new_tokens", token_cap(job.est_s, job.target_s))
    if job.x_vector_only:
        params["x_vector_only"] = True
    return params


def cmd_synth(args: argparse.Namespace) -> int:
    project = Project.load(args.project)
    catalog = _catalog()
    # GPU-first (contract §1.6): tts.device / --device override, else compute.device (auto -> cuda if present)
    device = args.device or project.setting("tts", "device") or resolve_device(project.setting("compute", "device", "auto"))
    engine_name = args.engine or str(project.setting("tts", "engine", "qwen3tts-fast"))
    notes: list[str] = []
    engine_name, fallback_note = engine_for_device(engine_name, str(device), catalog)
    if fallback_note:
        notes.append(fallback_note)
    engine = resolve_engine(project, engine_name, catalog, check_env=not args.dry_run)
    if engine.name == "dummy":
        device = "cpu"
    jobs, skipped, attention = _plan_synth(project, args, engine)
    planned = len(jobs)
    if args.limit is not None:
        jobs = jobs[: max(0, args.limit)]
    by_ref = Counter(j.ref_mode for j in jobs)
    if int(project.setting("tts", "best_of", 1) or 1) > 1:
        notes.append("tts.best_of>1 is not used by synth v0.1: QA regenerates weak lines via decisions + --attempt-next")

    if args.dry_run:
        return emit({
            "stage": "synth", "dry_run": True, "engine": engine.name, "model": engine.model_name,
            "model_source": engine.model_source, "env_ready": engine.python.exists(), "planned": planned, "would_run": len(jobs), "by_ref": dict(by_ref),
            "by_char": dict(Counter(j.char_id for j in jobs).most_common(12)),
            "skipped": dict(skipped), "attention": clip(attention), "notes": notes,
        })
    if not jobs:
        status = "attention" if attention else "done"
        project.mark_stage("synth", status, done=0, failed=0, remaining=0, skipped=dict(skipped))
        return emit({
            "stage": "synth", "engine": engine.name, "model": engine.model_name, "done": 0, "failed": 0,
            "remaining": 0, "skipped": dict(skipped), "attention": clip(attention),
            "next": "babeldub post", "notes": notes,
        }, EXIT_ATTENTION if attention else EXIT_OK)

    cache_dir = project.path("synth", "refcache")
    for job in jobs:
        job.ref, trimmed = _fit_ref(job.ref, cache_dir)
        if trimmed:
            job.ref_trimmed = True
            job.x_vector_only = True  # cut audio no longer matches its transcript

    results_path = project.path(layout.SYNTH_RESULTS)
    _ensure_header(results_path, SYNTH_SCHEMA)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = project.path(layout.LOGS_DIR, f"synth-{stamp}.log")
    device_note: str | None = None
    lock = GpuLock(project.path(layout.GPU_LOCK), owner=f"synth:{engine.name}", wait_s=args.wait_lock)
    run = _SynthRun(project, engine, results_path, cache_dir)
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
        "audio_s": round(t.audio_s, 1), "elapsed_s": round(elapsed, 1), "batch": batch_size,
    }
    if t.vram_peak:
        summary["vram_peak_gb"] = round(t.vram_peak, 2)
    project.mark_stage("synth", status, engine=engine.name, model=engine.model_name, **summary)
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
    payload["next"] = "babeldub synth" if remaining else "babeldub post"
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

    Runaway policy: an output that hit the token cap or is far longer than any plausible reading
    is discarded; the line is retried alone in timbre-only mode on the same reference, then with
    the character reference in ICL mode; if every try runs away the row is an error "runaway".
    """

    def __init__(self, project: Project, engine: EngineChoice, results_path: Path, cache_dir: Path):
        self.project = project
        self.engine = engine
        self.results_path = results_path
        self.cache_dir = cache_dir
        self.tally = _Tally()

    def request_for(self, job: Job) -> dict[str, Any]:
        job.out.parent.mkdir(parents=True, exist_ok=True)
        return {
            "op": "synth", "id": job.id, "text": job.text, "lang": self.project.target_lang,
            "ref_wav": str(job.ref.wav), "ref_text": "" if job.x_vector_only else job.ref.text,
            "ref_lang": job.ref.lang, "out": str(job.out), "seed": job.seed,
            "params": _engine_params(self.project, self.engine, job),
        }

    def record(
        self, job: Job, reply: dict[str, Any], seed: int, batch: int, discarded: list[dict[str, Any]] | None = None
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
        if job.ref_reason:
            row["ref_reason"] = job.ref_reason
        if job.src_rate is not None:
            row["src_rate"] = job.src_rate
        if batch > 1:
            row["batch"] = batch
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

    def call(self, client: WorkerClient, job: Job) -> dict[str, Any]:
        try:
            return client.request(self.request_for(job), timeout=max(240.0, 40.0 * job.target_s))
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
                    job.ref, job.ref_trimmed = _fit_ref(job.alt_ref, self.cache_dir)
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
        self.finish(client, job, self.call(client, job), job.seed, 1)
        if not client.alive and client.restarts >= client.max_restarts:
            self.tally.aborted = "worker keeps crashing"

    def batch(self, client: WorkerClient, chunk: list[Job]) -> bool:
        """One generate call for several lines. False = the batch failed as a whole (caller falls back)."""
        msg = {"op": "synth_batch", "seed": chunk[0].seed, "items": [self.request_for(j) for j in chunk]}
        try:
            reply = client.request(msg, timeout=max(300.0, 60.0 * max(j.target_s for j in chunk)))
        except WorkerError:
            return False
        results = reply.get("results")
        if not reply.get("ok") or not isinstance(results, list) or len(results) != len(chunk):
            return False
        for job, item in zip(chunk, results, strict=True):
            item = item if isinstance(item, dict) else {"ok": False, "error": "bad batch item"}
            self.finish(client, job, item, chunk[0].seed, len(chunk))
        return True

    def execute(self, client: WorkerClient, jobs: list[Job], batch_size: int) -> None:
        i = 0
        while i < len(jobs) and not self.tally.aborted:
            chunk = jobs[i : i + batch_size]
            i += len(chunk)
            if len(chunk) > 1 and self.batch(client, chunk):
                continue
            if len(chunk) > 1:  # e.g. out of memory: smaller batches from now on, these lines one by one
                batch_size = max(1, batch_size // 2)
            for job in chunk:
                if self.tally.aborted:
                    break
                self.single(client, job)


# ---- post ---------------------------------------------------------------------------------
def _edges(mono: np.ndarray, sr: int) -> tuple[np.ndarray, float, float, float]:
    """Trim to speech ± EDGE_PAD_S. Returns (trimmed, kept_lead_s, kept_trail_s, speech_s)."""
    trimmed, lead, trail = trim_silence(mono, sr, pad_s=EDGE_PAD_S)
    kept_lead = min(EDGE_PAD_S, lead)
    kept_trail = min(EDGE_PAD_S, trail)
    speech = len(trimmed) / sr - kept_lead - kept_trail
    return trimmed, kept_lead, kept_trail, max(0.0, speech)


def _post_one(
    project: Project, line: dict[str, Any], res: dict[str, Any], settings: dict[str, float], method: str | None
) -> dict[str, Any]:
    raw_path = _abs(project, res.get("wav"))
    if raw_path is None or not raw_path.exists():
        raise BabelDubError(f"synth file missing: {res.get('wav')}")
    raw, sr = read_audio(raw_path)
    speech, kept_lead, kept_trail, core_s = _edges(to_mono(raw), sr)
    if len(speech) == 0 or core_s <= 0:
        raise BabelDubError("synthesized audio is silent")

    audio = line.get("audio") or {}
    orig_path = _orig_wav(project, line)
    if orig_path is not None:
        orig, o_sr = read_audio(orig_path)
        o_dur = orig.shape[0] / o_sr
        bounds = voiced_bounds(orig, o_sr)
        if bounds is not None:
            o_lead, o_trail = bounds[0] / o_sr, o_dur - bounds[1] / o_sr
            target_s = (bounds[1] - bounds[0]) / o_sr
            orig_core = orig[bounds[0] : bounds[1]]
        else:
            o_lead = o_trail = 0.0
            target_s, orig_core = o_dur, orig
        out_sr, out_ch = o_sr, orig.shape[1]
        measure = "lufs" if target_s >= 0.4 else "rms"
        # compare like with like: the dub is mono and duplicated to the original's channels later,
        # so the reference level is that of the original's mono downmix (BS.1770 sums channels)
        target_level = level_db(to_mono(orig_core), o_sr, measure)
    else:
        o_dur = float(audio.get("dur_s") or core_s)
        o_lead = o_trail = EDGE_PAD_S
        target_s = max(0.05, o_dur - 2 * EDGE_PAD_S)
        out_sr, out_ch = int(audio.get("sr") or sr), int(audio.get("ch") or 1)
        measure = "lufs" if target_s >= 0.4 else "rms"
        target_level = float("nan")
    if not math.isfinite(target_level):
        target_level = DEFAULT_LEVEL_LUFS
    target_s = max(target_s, 0.05)
    fixed = bool(line.get("fixed_length"))
    ratio_raw = core_s / target_s
    tolerance = settings["fixed_length_tolerance"] if fixed else settings["tolerance"]

    stretch, used = 1.0, "none"
    if ratio_raw > 1.0 + tolerance:
        stretch = max(target_s / core_s, 1.0 - settings["max_stretch"])
        with tempfile.TemporaryDirectory(prefix="babeldub-post-") as tmp:
            src, dst = Path(tmp, "in.wav"), Path(tmp, "out.wav")
            write_wav(src, speech, sr, subtype="FLOAT")
            used = time_stretch(src, dst, stretch, method=method)
            stretched, s_sr = read_audio(dst)
        speech, kept_lead, kept_trail, core_s = _edges(to_mono(resample(stretched, s_sr, sr)), sr)
    ratio = core_s / target_s
    over = ratio > 1.0 + settings["fixed_length_tolerance"] if fixed else ratio > settings["max_duration_ratio"]

    speech = fade(speech, sr, 5.0)
    level_in = level_db(speech, sr, measure)
    speech = match_loudness(speech, sr, target_level, ceiling_db=settings["true_peak_db"], measure=measure)
    speech = resample(speech, sr, out_sr)
    lead_add = max(0.0, o_lead - kept_lead)
    trail_add = max(0.0, o_trail - kept_trail)
    if fixed:  # stay inside the original length when the speech fits
        overflow = lead_add + len(speech) / out_sr + trail_add - o_dur
        if overflow > 0:
            cut = min(trail_add, overflow)
            trail_add -= cut
            lead_add = max(0.0, lead_add - (overflow - cut))
    final = to_channels(pad_silence(speech, out_sr, lead_add, trail_add), out_ch)
    peak = true_peak_db(final, out_sr)
    if math.isfinite(peak) and peak > settings["true_peak_db"]:  # resampling can move inter-sample peaks
        final = final * np.float32(10.0 ** ((settings["true_peak_db"] - peak) / 20.0))
        peak = true_peak_db(final, out_sr)
    head = int(round(lead_add * out_sr))
    level_out = level_db(to_mono(final[head : head + len(speech)]), out_sr, measure)
    out_path = project.path(layout.POST_DIR, f"{_line_uid(line)}.wav")
    write_wav(out_path, final, out_sr, subtype="PCM_16")
    return {
        "id": line["id"], "attempt": res.get("attempt"), "wav": project.rel(out_path),
        # dur_s: whole file; target_s: speech span of the original; ratio: dub speech span / target_s
        "dur_s": round(final.shape[0] / out_sr, 4), "orig_dur_s": round(o_dur, 4),
        "target_s": round(target_s, 4), "ratio": round(ratio, 4),
        "ratio_raw": round(ratio_raw, 4), "stretch": round(stretch, 4), "stretch_method": used,
        "lufs_in": _num(level_in, 2), "lufs_out": _num(level_out, 2), "level_ref": _num(target_level, 2),
        "measure": measure, "peak_db": _num(peak, 2),
        "lead_s": round(lead_add + kept_lead, 4), "trail_s": round(trail_add + kept_trail, 4),
        "sr": out_sr, "ch": out_ch, "fixed_length": fixed, "over_length": bool(over),
        "src_wav": res.get("wav"), "src_key": res.get("key"), "ts": now_iso(),
    }


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
    settings = {
        "max_stretch": float(project.setting("post", "max_stretch", 0.12)),
        "fixed_length_tolerance": float(project.setting("post", "fixed_length_tolerance", 0.03)),
        "true_peak_db": float(project.setting("post", "true_peak_db", -1.0)),
        "tolerance": float(project.setting("post", "tolerance", 0.05)),
        "max_duration_ratio": float(project.setting("qa", "max_duration_ratio", 1.35)),
    }
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
    errors: list[str] = []
    methods: Counter[str] = Counter()
    for line, res in work:
        try:
            row = _post_one(project, line, res, settings, args.stretch_method)
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
    payload["next"] = "babeldub post" if remaining else "babeldub qa"
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
    p.add_argument("--engine", choices=ENGINES, help="override tts.engine for this run")
    p.add_argument("--device", choices=("cuda", "cpu"), help="override the device (default: tts.device, else compute.device: GPU when present)")
    p.add_argument("--wait-lock", type=float, default=0.0, metavar="SECONDS", help="wait for a busy GPU lock")
    p.add_argument("--batch", type=int, metavar="N", help="lines per generate call (default tts.batch_size or 4)")
    p.add_argument("--force", action="store_true", help="new attempt even if an identical one exists")
    p.add_argument("--dry-run", action="store_true", help="show the plan without loading a model")
    p.set_defaults(func=cmd_synth)

    p = sub.add_parser(
        "post",
        help="fit duration, match loudness and format of synthesized lines to the originals",
    )
    common(p)
    p.add_argument("--ids", help="only these line ids or uids (comma-separated)")
    p.add_argument("--limit", type=int, help="process at most N lines")
    p.add_argument("--force", action="store_true", help="reprocess lines that are up to date")
    p.add_argument("--dry-run", action="store_true", help="count work without writing files")
    p.add_argument("--stretch-method", choices=STRETCH_METHODS, help="force a time-stretch tool")
    p.set_defaults(func=cmd_post)
