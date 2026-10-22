"""CLI-side access to the `qc` worker (ASR, VAD, prosody, speaker embeddings, MOS, emotion).

The worker is workers/qc_worker.py running in BABELDUB_HOME/envs/qc. QcSession starts it via
babeldub.workers.client.WorkerClient, holds the project GPU lock while it runs on CUDA
(GPU-first policy, see core/compute.py) and wraps the worker ops. MOS (UTMOS22) and emotion
(emotion2vec+, opt-in) are optional: `mos_model_dir`/`emotion_model_dir` return None when the
weights are not downloaded, and the worker loads them only on their first request.
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
DEFAULT_SV_MODEL = "spkrec-ecapa-voxceleb"
DEFAULT_MOS_MODEL = "utmos22-strong"
DEFAULT_EMOTION_MODEL = "emotion2vec_plus_base"
DEFAULT_ACCENT_DIR = "ruaccent"  # RUAccent workdir: it downloads its dictionaries there on first use
QC_LOG = "logs/qc_worker.log"


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


def mos_model_dir(settings: dict[str, Any]) -> Path | None:
    """UTMOS22 checkpoint folder (`qa.mos_model`), None when it is not downloaded."""
    return _optional_dir(settings, "mos_model", DEFAULT_MOS_MODEL)


def emotion_model_dir(settings: dict[str, Any]) -> Path | None:
    """emotion2vec+ folder (`qa.emotion_model`, opt-in license), None when it is not downloaded."""
    return _optional_dir(settings, "emotion_model", DEFAULT_EMOTION_MODEL)


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
        mos_dir = mos_model_dir(settings) if need_mos else None
        emo_dir = emotion_model_dir(settings) if need_emotion else None
        if mos_dir is not None:
            req["mos_model"] = str(mos_dir)
        if emo_dir is not None:
            req["emotion_model"] = str(emo_dir)
        if need_accent:
            req["accent_model"] = str(accent_model_dir(settings))
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
        session.has_mos = mos_dir is not None
        session.has_emotion = emo_dir is not None
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

    def asr(self, key: str, wav: Path, lang: str | None) -> dict[str, Any]:
        reply = self._call({"op": "asr", "id": key, "wav": str(wav), "lang": lang})
        return {"text": reply.get("text") or "", "lang": reply.get("lang"), "lang_prob": reply.get("lang_prob")}

    def asr_words(self, key: str, wav: Path, lang: str | None) -> dict[str, Any]:
        """Transcript with word times: {text, lang, lang_prob, words[{w, start, end, prob}], avg_logprob, no_speech_prob}."""
        return self._call({"op": "asr_words", "id": key, "wav": str(wav), "lang": lang})

    def vad(self, key: str, wav: Path) -> dict[str, Any]:
        """Silero speech segments: {segments [[start_s, end_s]], speech_s, dur_s}."""
        return self._call({"op": "vad", "id": key, "wav": str(wav)})

    def prosody(self, key: str, wav: Path) -> dict[str, Any]:
        """{f0_median_hz, f0_range_st, rms_db, voiced_ratio, dur_s}."""
        return self._call({"op": "prosody", "id": key, "wav": str(wav)})

    def emotion(self, key: str, wav: Path) -> dict[str, Any]:
        """{label, score, emb (np.float32)}; only when the session was opened with an emotion model."""
        reply = self._call({"op": "emotion", "id": key, "wav": str(wav)})
        return {"label": reply.get("label"), "score": reply.get("score"),
                "emb": np.asarray(reply.get("emb") or [], dtype=np.float32)}

    def mos(self, key: str, wav: Path) -> float:
        return float(self._call({"op": "mos", "id": key, "wav": str(wav)})["mos"])

    def score(self, key: str, wav: Path, *, text: str | None = None, lang: str | None = None,
              orig_wav: Path | None = None, refs: list[Path] | None = None,
              want: tuple[str, ...] = ("cer", "sim", "emo", "mos")) -> dict[str, Any]:
        """{cer, asr_text, sim, emo_cos, mos, prosody, orig_prosody, unavailable?}: metrics that cannot be
        computed are null; prosody (with its F0 contour) only when `want` has "prosody"."""
        req: dict[str, Any] = {"op": "score", "id": key, "wav": str(wav), "text": text, "lang": lang,
                               "refs": [str(r) for r in refs or []], "want": list(want)}
        if orig_wav is not None:
            req["orig_wav"] = str(orig_wav)
        reply = self._call(req)
        return {k: reply.get(k) for k in ("cer", "asr_text", "sim", "emo_cos", "mos", "prosody", "orig_prosody",
                                          "unavailable")}

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


def number_cer(session: QcSession | None, reference: str | None, hypothesis: str | None, lang: str | None) -> float:
    """CER with digits spelled out on both sides: by the CLI's num2words when installed, else by the
    qc worker's (one `spell` request, only when a side has digits)."""
    from babeldub.qa.metrics import cer
    from babeldub.text.normalize import has_num2words

    ref, hyp = reference or "", hypothesis or ""
    if not lang or has_num2words() or session is None or not (_DIGIT.search(ref) or _DIGIT.search(hyp)):
        return cer(ref, hyp, lang)
    try:
        ref, hyp = session.spell([ref, hyp], lang)
    except QcItemError:
        pass
    return cer(ref, hyp)
