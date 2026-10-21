"""CLI-side access to the `qc` worker (ASR + speaker embeddings).

The worker is workers/qc_worker.py running in BABELDUB_HOME/envs/qc. QcSession starts it via
babeldub.workers.client.WorkerClient, holds the project GPU lock while it runs on CUDA
(GPU-first policy, see core/compute.py) and wraps the `asr` / `embed` ops.
"""

from __future__ import annotations

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

    @classmethod
    def open(
        cls,
        project_root: Path,
        settings: dict[str, Any],
        *,
        need_asr: bool,
        need_sv: bool,
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

    def close(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            client.close()
        if self.lock is not None:
            self.lock.release()
            self.lock = None
