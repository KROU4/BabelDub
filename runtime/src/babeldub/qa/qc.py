"""CLI-side access to the `qc` worker (ASR, VAD, prosody, speaker embeddings, MOS, emotion).

The worker is workers/qc_worker.py running in BABELDUB_HOME/envs/qc. QcSession starts it via
babeldub.workers.client.WorkerClient, holds the project GPU lock while it runs on CUDA
(GPU-first policy, see core/compute.py) and wraps the worker ops. MOS (Distill-MOS or UTMOS22),
emotion (emotion2vec+, opt-in) and the second Russian ASR (GigaAM-v3) are optional: `mos_setup`,
`emotion_model_dir` and `asr2_model_dir` return None when the weights are not downloaded, and the
worker loads them only on their first request.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from babeldub.core import layout
from babeldub.core.compute import device_warning, resolve_device
from babeldub.core.output import EXIT_ENV, BabelDubError
from babeldub.core.paths import env_python, hf_home, models_dir

QC_ENV = "qc"
QC_SCRIPT = "qc_worker.py"
DEFAULT_ASR_MODEL = "faster-whisper-large-v3-turbo"
DEFAULT_ASR2_MODEL = "GigaAM-v3-ctc"  # second ASR for Russian targets
DEFAULT_SV_MODEL = "spkrec-ecapa-voxceleb"
DEFAULT_MOS_MODEL = "utmos22-strong"  # UTMOS22 folder; also what an older project without qa.mos_model uses
DISTILLMOS = "distillmos"  # qa.mos_model value and catalog folder of Distill-MOS
MOS_MODELS = (DISTILLMOS, "utmos22")  # the values `set qa.mos_model` accepts
DEFAULT_EMOTION_MODEL = "emotion2vec_plus_base"
DEFAULT_ACCENT_DIR = "ruaccent"  # RUAccent workdir: it downloads its dictionaries there on first use
QC_LOG = "logs/qc_worker.log"
HINT_F0_UP = 4.0  # hinted pyin bounds (dub and original) reach this multiple of the F0 hint, as in the worker's `score`


class QcItemError(Exception):
    """One request failed (bad file, decode error); the stage records it and continues."""


def _model_dir(settings: dict[str, Any], key: str, default: str) -> Path:
    name = (settings.get("qa") or {}).get(key) or default
    p = Path(name)
    return p if p.is_absolute() else models_dir() / name


def asr_model_dir(settings: dict[str, Any]) -> Path:
    return _model_dir(settings, "asr_model", DEFAULT_ASR_MODEL)


def sv_model_dir(settings: dict[str, Any]) -> Path:
    return _model_dir(settings, "sv_model", DEFAULT_SV_MODEL)


def _optional_dir(settings: dict[str, Any], key: str, default: str) -> Path | None:
    folder = _model_dir(settings, key, default)
    return folder if folder.is_dir() and any(folder.iterdir()) else None


def mos_setup(settings: dict[str, Any]) -> tuple[str, Path, Path | None] | None:
    """(backend, checkpoint folder, UTMOS22 fallback folder) of `qa.mos_model`, None without any MOS weights.

    `distillmos` uses the catalog folder `distillmos` (falling back to UTMOS22 when it is not downloaded, or in
    the worker when the distillmos package is missing); `utmos22` the folder `utmos22-strong`. Any other value
    is an older project's UTMOS22 folder name or path (the setting used to name the folder)."""
    value = str((settings.get("qa") or {}).get("mos_model") or DEFAULT_MOS_MODEL)
    utmos_name = DEFAULT_MOS_MODEL if value in MOS_MODELS else value
    utmos_dir = _optional_dir({"qa": {"mos_model": utmos_name}}, "mos_model", DEFAULT_MOS_MODEL)
    if value == DISTILLMOS:
        distill_dir = _optional_dir({}, "distillmos", DISTILLMOS)
        if distill_dir is not None:
            return DISTILLMOS, distill_dir, utmos_dir
    return ("utmos22", utmos_dir, None) if utmos_dir is not None else None


def default_mos_model() -> str:
    """`qa.mos_model` of a new project: distillmos when the qc env has the distillmos package (or is not
    installed yet: `setup` installs the current catalog spec, which has it), else utmos22."""
    from babeldub.envsetup.catalog import env_marker, load_catalog

    engine = load_catalog().engines.get(QC_ENV)
    if engine is None:
        return "utmos22"
    if not env_python(QC_ENV).exists():
        packages: list[Any] = list(engine.packages)
    else:
        packages = list(((env_marker(engine) or {}).get("spec") or {}).get("packages") or [])
    return DISTILLMOS if any(str(p).startswith("distillmos") for p in packages) else "utmos22"


def emotion_model_dir(settings: dict[str, Any]) -> Path | None:
    """emotion2vec+ folder (`qa.emotion_model`, opt-in license), None when it is not downloaded."""
    return _optional_dir(settings, "emotion_model", DEFAULT_EMOTION_MODEL)


def asr2_model_dir(settings: dict[str, Any]) -> Path | None:
    """GigaAM-v3 CTC folder (`qa.asr2_model`) for a Russian target, None for other languages or when it is
    not downloaded (QA and best-of then use Whisper alone)."""
    from babeldub.qa.metrics import folds_vowels

    if not folds_vowels(settings.get("target_lang")):
        return None
    return _optional_dir(settings, "asr2_model", DEFAULT_ASR2_MODEL)


def accent_model_dir(settings: dict[str, Any]) -> Path:
    """RUAccent workdir (`qa.accent_model`); used only with tts.stress=on."""
    return _model_dir(settings, "accent_model", DEFAULT_ACCENT_DIR)


def qc_missing(settings: dict[str, Any], need_asr: bool, need_sv: bool) -> list[str]:
    """What is missing for the qc worker: env and/or model folders (empty list = ready)."""
    missing: list[str] = []
    if not env_python(QC_ENV).exists():
        missing.append(f"env:{QC_ENV}")
    if need_asr and not asr_model_dir(settings).is_dir():
        missing.append(f"model:{asr_model_dir(settings).name}")
    if need_sv and not sv_model_dir(settings).is_dir():
        missing.append(f"model:{sv_model_dir(settings).name}")
    return missing


def _make_client(log_path: Path) -> Any:
    from babeldub.workers.client import WorkerClient

    return WorkerClient(QC_ENV, QC_SCRIPT, timeout=300.0, load_timeout=900.0, log_path=log_path)


def _make_lock(path: Path, owner: str, wait_s: float) -> Any:
    from babeldub.workers.client import GpuLock

    return GpuLock(path, owner=owner, wait_s=wait_s)


class QcSession:
    """Loaded qc worker. Use as a context manager: stops the worker and releases the GPU lock."""

    def __init__(self, client: Any, device: str, lock: Any = None, info: dict[str, Any] | None = None):
        self.client = client
        self.device = device
        self.lock = lock
        self.info = info or {}
        self.warnings: list[str] = []
        self.has_mos = False
        self.has_emotion = False
        self.has_asr2 = False
        self.mos_model: str | None = None  # distillmos | utmos22: requested, then the model that answered

    @classmethod
    def open(
        cls,
        project_root: Path,
        settings: dict[str, Any],
        *,
        need_asr: bool,
        need_sv: bool,
        need_mos: bool = False,
        need_emotion: bool = False,
        need_accent: bool = False,
        need_asr2: bool = False,
        device: str | None = None,
        wait_lock: float = 0.0,
        owner: str = "qa",
    ) -> QcSession:
        missing = qc_missing(settings, need_asr, need_sv)
        if missing:
            raise BabelDubError(
                "qc environment is not ready",
                code=EXIT_ENV,
                missing=missing,
                hint="run `babeldub doctor`, then `babeldub setup --engines qc`",
            )
        wanted = resolve_device(device or (settings.get("compute") or {}).get("device") or "auto")
        lock = None
        if wanted == "cuda":
            lock = _make_lock(project_root / layout.GPU_LOCK, owner, wait_lock)
            lock.acquire()
        client = _make_client(project_root / QC_LOG)
        req: dict[str, Any] = {"op": "load", "device": wanted}
        if need_asr:
            req["asr_model"] = str(asr_model_dir(settings))
        if need_sv:
            req["sv_model"] = str(sv_model_dir(settings))
            req["sv_savedir"] = str(hf_home() / "speechbrain" / sv_model_dir(settings).name)
        mos = mos_setup(settings) if need_mos else None
        emo_dir = emotion_model_dir(settings) if need_emotion else None
        if mos is not None:
            req.update(mos_model=str(mos[1]), mos_backend=mos[0])
            if mos[2] is not None:
                req["mos_fallback"] = str(mos[2])
        if emo_dir is not None:
            req["emotion_model"] = str(emo_dir)
        if need_accent:
            req["accent_model"] = str(accent_model_dir(settings))
        asr2_dir = asr2_model_dir(settings) if need_asr2 else None
        if asr2_dir is not None:
            req["asr2_model"] = str(asr2_dir)
        try:
            reply = client.load(req)
        except BabelDubError as err:
            client.close()
            if lock is not None:
                lock.release()
            raise BabelDubError(
                f"qc worker could not load its models: {err}",
                code=EXIT_ENV,
                hint="see logs/qc_worker.log; `babeldub doctor`, then `babeldub setup --engines qc`",
                stderr=err.extra.get("stderr"),
            ) from err
        actual = str(reply.get("device") or wanted)
        session = cls(client, actual, lock, reply.get("info") or {})
        session.has_mos = mos is not None
        session.mos_model = mos[0] if mos is not None else None
        session.has_emotion = emo_dir is not None
        session.has_asr2 = asr2_dir is not None
        warning = device_warning(wanted, actual)
        if warning:
            session.warnings.append(warning)
        if lock is not None and not actual.startswith("cuda"):
            lock.release()
            session.lock = None
        return session

    def __enter__(self) -> QcSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _call(self, req: dict[str, Any]) -> dict[str, Any]:
        from babeldub.workers.client import WorkerCrashed, WorkerTimeout

        try:
            reply = self.client.request(req)
        except (WorkerCrashed, WorkerTimeout) as exc:
            if self.client.restarts >= self.client.max_restarts:
                raise
            raise QcItemError(str(exc)) from exc
        if not reply.get("ok"):
            raise QcItemError(str(reply.get("error") or "unknown worker error"))
        return reply

    def embed(self, key: str, wav: Path) -> np.ndarray:
        reply = self._call({"op": "embed", "id": key, "wav": str(wav)})
        return np.asarray(reply["emb"], dtype=np.float32)

    def asr(self, key: str, wav: Path, lang: str | None, text: str | None = None) -> dict[str, Any]:
        """{text, text_raw, lang, lang_prob}: with the reference `text`, known Whisper hallucination tails it
        does not contain are stripped from `text` (`text_raw` keeps them)."""
        req: dict[str, Any] = {"op": "asr", "id": key, "wav": str(wav), "lang": lang}
        if text:
            req["text"] = text
        reply = self._call(req)
        out = reply.get("text") or ""
        return {"text": out, "text_raw": reply.get("text_raw", out) or "", "lang": reply.get("lang"),
                "lang_prob": reply.get("lang_prob")}

    def asr2(self, key: str, wav: Path) -> str | None:
        """The second ASR's transcript (GigaAM-v3 CTC), None when the session has none. The first failure
        (e.g. its packages are missing from an older qc env) turns it off for the session with a warning."""
        if not self.has_asr2:
            return None
        try:
            return str(self._call({"op": "asr2", "id": key, "wav": str(wav)}).get("text") or "")
        except QcItemError as exc:
            self.has_asr2 = False
            self.warnings.append(f"second ASR (GigaAM) unavailable: {exc}")
            return None

    def asr_words(self, key: str, wav: Path, lang: str | None) -> dict[str, Any]:
        """Transcript with word times: {text, lang, lang_prob, words[{w, start, end, prob}], avg_logprob, no_speech_prob}."""
        return self._call({"op": "asr_words", "id": key, "wav": str(wav), "lang": lang})

    def vad(self, key: str, wav: Path) -> dict[str, Any]:
        """Silero speech segments: {segments [[start_s, end_s]], speech_s, dur_s}."""
        return self._call({"op": "vad", "id": key, "wav": str(wav)})

    def prosody(self, key: str, wav: Path, f0_hint: float | None = None) -> dict[str, Any]:
        """{f0_median_hz, f0_range_st, rms_db, rms_std_db, voiced_ratio, voiced_s, f0_bounds_hz, dur_s, contour};
        `f0_hint` (the character's median F0) bounds pyin from half of it up to HINT_F0_UP x it, the same for a dub
        and its original, so a take or an original an octave above the character reads as such (the register
        check)."""
        req: dict[str, Any] = {"op": "prosody", "id": key, "wav": str(wav)}
        if f0_hint:
            req["f0_hint_hz"] = round(float(f0_hint), 1)
            req["f0_up"] = HINT_F0_UP
        return self._call(req)

    def emotion(self, key: str, wav: Path) -> dict[str, Any]:
        """{label, score, emb (np.float32)}; only when the session was opened with an emotion model."""
        reply = self._call({"op": "emotion", "id": key, "wav": str(wav)})
        return {"label": reply.get("label"), "score": reply.get("score"),
                "emb": np.asarray(reply.get("emb") or [], dtype=np.float32)}

    def mos(self, key: str, wav: Path) -> float:
        """MOS of a file; `mos_model` then names the model that answered (Distill-MOS may fall back to UTMOS22)."""
        reply = self._call({"op": "mos", "id": key, "wav": str(wav)})
        self.mos_model = reply.get("mos_model") or self.mos_model
        return float(reply["mos"])

    def score(self, key: str, wav: Path, *, text: str | None = None, lang: str | None = None,
              orig_wav: Path | None = None, refs: list[Path] | None = None,
              want: tuple[str, ...] = ("cer", "sim", "emo", "mos"), f0_hint: float | None = None) -> dict[str, Any]:
        """{cer, asr_text, sim, emo_cos, mos, prosody, orig_prosody, unavailable?, asr2_error?}: metrics that
        cannot be computed are null; prosody (with its F0 contour) only when `want` has "prosody". A second-ASR
        failure is reported once in `warnings`, as by `asr2`."""
        req: dict[str, Any] = {"op": "score", "id": key, "wav": str(wav), "text": text, "lang": lang,
                               "refs": [str(r) for r in refs or []], "want": list(want)}
        if orig_wav is not None:
            req["orig_wav"] = str(orig_wav)
        if f0_hint:
            req["f0_hint_hz"] = round(float(f0_hint), 1)
        reply = self._call(req)
        self.mos_model = reply.get("mos_model") or self.mos_model
        if reply.get("asr2_error") and self.has_asr2:
            self.has_asr2 = False
            self.warnings.append(f"second ASR (GigaAM) unavailable: {reply['asr2_error']}")
        return {k: reply.get(k) for k in ("cer", "cer_folded", "asr_text", "asr_text_raw", "asr2_text", "cer2",
                                          "sim", "emo_cos", "mos", "mos_model", "prosody", "orig_prosody",
                                          "unavailable", "asr2_error")}

    def spell(self, texts: list[str], lang: str | None) -> list[str]:
        """Digits spelled out by the worker's num2words (the CLI may not have it)."""
        reply = self._call({"op": "spell", "texts": list(texts), "lang": lang})
        out = [str(t) for t in reply.get("texts") or []]
        return out if len(out) == len(texts) else list(texts)

    def accent(self, texts: list[str], homographs_only: bool = True) -> list[str]:
        """RUAccent marks ("+" before the stressed vowel) for each text, homographs only by default."""
        reply = self._call({"op": "accent", "texts": list(texts), "homographs_only": homographs_only})
        out = [str(t) for t in reply.get("texts") or []]
        if len(out) != len(texts):
            raise QcItemError(f"accent returned {len(out)} texts for {len(texts)}")
        return out

    def close(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            client.close()
        if self.lock is not None:
            self.lock.release()
            self.lock = None


_DIGIT = re.compile(r"\d")


def number_cer(session: QcSession | None, reference: str | None, hypothesis: str | None, lang: str | None,
               fold: bool = False) -> float:
    """CER with digits spelled out on both sides: by the CLI's num2words when installed, else by the
    qc worker's (one `spell` request, only when a side has digits). `fold`: vowel-folded (Russian)."""
    from babeldub.qa.metrics import cer
    from babeldub.text.normalize import has_num2words

    ref, hyp = reference or "", hypothesis or ""
    if not lang or has_num2words() or session is None or not (_DIGIT.search(ref) or _DIGIT.search(hyp)):
        return cer(ref, hyp, lang, fold=fold)
    try:
        ref, hyp = session.spell([ref, hyp], lang)
    except QcItemError:
        pass
    return cer(ref, hyp, fold=fold)


ASR_DISAGREE = 0.15  # qa.max_asr_disagreement: the two ASRs' CERs differ more -> asr_disagree


def cer_report(session: QcSession | None, reference: str | None, asr_text: str | None, lang: str | None, *,
               asr2_text: str | None = None, disagree: float = ASR_DISAGREE) -> dict[str, Any]:
    """Every CER of one take and the one the gates use.

    cer_raw     Whisper against the reference (digits spelled out)
    cer_folded  the same with Russian vowels folded (ru only, else None)
    cer_asr2    the second ASR (GigaAM-v3 CTC, ru), the lower of its plain and folded CER (None without it)
    cer         the gate value: the lowest of the above, so a correct reduced vowel or one ASR's slip
                does not fail a take
    asr_disagree  both ASRs heard the take and their best CERs differ by more than `disagree`
    """
    from babeldub.qa.metrics import folds_vowels

    fold = folds_vowels(lang)
    out: dict[str, Any] = {"cer": None, "cer_raw": None, "cer_folded": None, "cer_asr2": None,
                           "asr_disagree": None}
    best1: float | None = None
    if asr_text is not None:
        out["cer_raw"] = best1 = number_cer(session, reference, asr_text, lang)
        if fold:
            out["cer_folded"] = number_cer(session, reference, asr_text, lang, fold=True)
            best1 = min(best1, out["cer_folded"])
    if asr2_text is not None:
        plain = number_cer(session, reference, asr2_text, lang)
        out["cer_asr2"] = min(plain, number_cer(session, reference, asr2_text, lang, fold=True)) if fold else plain
        if best1 is not None:
            out["asr_disagree"] = abs(best1 - out["cer_asr2"]) > disagree
    known = [v for v in (best1, out["cer_asr2"]) if v is not None]
    out["cer"] = min(known) if known else None
    return out
