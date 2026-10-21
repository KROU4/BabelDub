"""BabelDub QC worker: ASR (faster-whisper) and speaker embeddings (SpeechBrain ECAPA).

Runs inside the `qc` engine venv as `<env python> -u qc_worker.py`; it never imports babeldub.
Protocol (docs/dev/CONTRACT.md section 6): one JSON command per stdin line, exactly one JSON
answer per command on stdout; all logs go to stderr.

  {"op": "load", "asr_model": dir, "sv_model": dir, "device": "auto|cuda|cpu",
   "compute_type": null, "sv_savedir": dir}
  {"op": "asr", "id": ..., "wav": ..., "lang": "ru"}       -> {"text", "lang", "lang_prob", "dur_s"}
  {"op": "embed", "id": ..., "wav": ...}                   -> {"emb": [192 floats], "dur_s"}
  {"op": "sim", "id": ..., "wav": ..., "refs": [...]}      -> {"sim": mean cosine, "sims": [...]}
  {"op": "quit"}
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import warnings
from pathlib import Path
from typing import Any

SV_SR = 16000
MIN_SV_SAMPLES = SV_SR // 2  # pad very short clips to 0.5 s for a stable embedding
EMB_CACHE_MAX = 4096
VAD_PARAMETERS = {"min_silence_duration_ms": 500, "speech_pad_ms": 200}

_STATE: dict[str, Any] = {
    "device": None,
    "asr": None,
    "asr_dir": None,
    "compute_type": None,
    "sv": None,
    "sv_dir": None,
    "emb_cache": {},
}


def log(msg: str) -> None:
    sys.stderr.write(f"[qc_worker] {msg}\n")
    sys.stderr.flush()


def _protocol_stdout() -> Any:
    """Keep a private handle to the real stdout; route fd 1 (native prints too) to stderr."""
    out_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(out_fd, "w", encoding="utf-8", buffering=1, newline="\n")


# ---- CUDA libraries (Windows) -------------------------------------------------------------
def _nvidia_bin_dirs() -> list[Path]:
    dirs: list[Path] = []
    for entry in sys.path:
        base = Path(entry) / "nvidia"
        for name in ("cudnn", "cublas", "cuda_nvrtc"):
            cand = base / name / "bin"
            if cand.is_dir() and cand not in dirs:
                dirs.append(cand)
    return dirs


_DLL_HANDLES: list[Any] = []


def _add_nvidia_dll_dirs() -> list[str]:
    """Make cuDNN/cuBLAS wheels (nvidia-*-cu12) visible to CTranslate2."""
    added: list[str] = []
    if sys.platform != "win32":
        return added
    for d in _nvidia_bin_dirs():
        try:
            _DLL_HANDLES.append(os.add_dll_directory(str(d)))
        except OSError:
            continue
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
        added.append(str(d))
    return added


def _torch_ships_cuda_libs() -> bool:
    import torch

    lib = Path(torch.__file__).parent / "lib"
    return (lib / "cudnn64_9.dll").exists() and (lib / "cublas64_12.dll").exists()


def _is_cuda_lib_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(k in text for k in ("cudnn", "cublas", ".dll", "library", "libcu"))


def _resolve_device(requested: str | None) -> str:
    import torch  # also loads the cuDNN/cuBLAS DLLs bundled with torch before CTranslate2 needs them

    if requested in (None, "", "auto"):
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested.startswith("cuda") and not torch.cuda.is_available():
        log("CUDA requested but torch.cuda.is_available() is False (CPU-only torch?): using cpu")
        return "cpu"  # reported back in the load reply; the CLI warns about it
    return requested


# ---- audio ---------------------------------------------------------------------------------
def _read_16k(path: str) -> Any:
    """Mono float32 tensor [1, T] at 16 kHz (soundfile + torchaudio; no PyAV/ffmpeg involved)."""
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio.functional as AF

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    wav = torch.from_numpy(np.ascontiguousarray(mono)).unsqueeze(0)
    if int(sr) != SV_SR and wav.shape[1] > 0:
        wav = AF.resample(wav, int(sr), SV_SR)
    return wav


# ---- models --------------------------------------------------------------------------------
def _load_asr(model_dir: str, device: str, compute_type: str | None) -> dict[str, Any]:
    import numpy as np
    from faster_whisper import WhisperModel

    ctype = compute_type or ("float16" if device.startswith("cuda") else "int8")
    if device.startswith("cuda") and sys.platform == "win32" and not _torch_ships_cuda_libs():
        _add_nvidia_dll_dirs()

    def build() -> Any:
        model = WhisperModel(model_dir, device="cuda" if device.startswith("cuda") else device, compute_type=ctype)
        # warm-up: the encoder pulls in cuDNN/cuBLAS now instead of on the first real request
        segments, _info = model.transcribe(np.zeros(SV_SR, dtype=np.float32), language="en", beam_size=1)
        list(segments)
        return model

    try:
        model = build()
    except Exception as exc:
        if not (device.startswith("cuda") and _is_cuda_lib_error(exc)):
            raise
        added = _add_nvidia_dll_dirs()
        log(f"CUDA library error ({exc}); retrying with DLL dirs {added}")
        model = build()
    _STATE.update(asr=model, asr_dir=model_dir, compute_type=ctype)
    return {"asr": model_dir, "compute_type": ctype}


def _load_sv(model_dir: str, device: str, savedir: str | None) -> dict[str, Any]:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    warnings.filterwarnings("ignore", message=".*list_audio_backends has been deprecated.*")
    from speechbrain.inference.speaker import EncoderClassifier
    from speechbrain.utils.fetching import LocalStrategy

    src = Path(model_dir)
    save = Path(savedir) if savedir else src.parent / ".speechbrain" / src.name
    save.mkdir(parents=True, exist_ok=True)
    overrides: dict[str, Any] = {}
    hparams = src / "hyperparams.yaml"
    if hparams.exists() and "pretrained_path:" in hparams.read_text(encoding="utf-8", errors="replace"):
        overrides["pretrained_path"] = src.as_posix()  # use local weights, never the hub id in the yaml
    model = EncoderClassifier.from_hparams(
        source=src.as_posix(),
        savedir=save.as_posix(),
        run_opts={"device": "cuda:0" if device == "cuda" else device},  # speechbrain wants an index
        overrides=overrides,
        local_strategy=LocalStrategy.COPY,
    )
    model.eval()
    _STATE.update(sv=model, sv_dir=model_dir, emb_cache={})
    return {"sv": model_dir}


def op_load(req: dict[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    device = _resolve_device(req.get("device"))
    if _STATE["device"] and _STATE["device"] != device:
        _STATE.update(asr=None, asr_dir=None, sv=None, sv_dir=None, emb_cache={})
    _STATE["device"] = device
    info: dict[str, Any] = {}
    sv_dir = req.get("sv_model")
    if sv_dir and (_STATE["sv"] is None or _STATE["sv_dir"] != sv_dir):
        info.update(_load_sv(sv_dir, device, req.get("sv_savedir")))
    asr_dir = req.get("asr_model")
    if asr_dir and (_STATE["asr"] is None or _STATE["asr_dir"] != asr_dir):
        info.update(_load_asr(asr_dir, device, req.get("compute_type")))
    if not sv_dir and not asr_dir:
        raise ValueError("load needs asr_model and/or sv_model")
    info["load_s"] = round(time.time() - t0, 2)
    return {"device": device, "asr": _STATE["asr"] is not None, "sv": _STATE["sv"] is not None, "info": info}


def _embed_path(path: str) -> Any:
    import numpy as np
    import torch

    model = _STATE["sv"]
    if model is None:
        raise RuntimeError("speaker model not loaded: send {'op': 'load', 'sv_model': ...} first")
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    cache: dict[Any, Any] = _STATE["emb_cache"]
    if key in cache:
        return cache[key]
    wav = _read_16k(path)
    if wav.shape[1] == 0:
        raise ValueError(f"empty audio: {path}")
    if wav.shape[1] < MIN_SV_SAMPLES:
        wav = torch.nn.functional.pad(wav, (0, MIN_SV_SAMPLES - wav.shape[1]))
    with torch.inference_mode():
        emb = model.encode_batch(wav.to(_STATE["device"]))
    vec = emb.squeeze().detach().float().cpu().numpy().astype(np.float32)
    if len(cache) >= EMB_CACHE_MAX:
        cache.pop(next(iter(cache)))
    cache[key] = vec
    return vec


def op_embed(req: dict[str, Any]) -> dict[str, Any]:
    vec = _embed_path(req["wav"])
    return {"id": req.get("id"), "emb": [round(float(x), 6) for x in vec]}


def op_sim(req: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    refs = req.get("refs") or []
    if not refs:
        raise ValueError("sim needs a non-empty 'refs' list")
    a = _embed_path(req["wav"])
    a = a / (np.linalg.norm(a) or 1.0)
    sims: list[float] = []
    for ref in refs:
        b = _embed_path(ref)
        b = b / (np.linalg.norm(b) or 1.0)
        sims.append(round(float(np.dot(a, b)), 4))
    return {"id": req.get("id"), "sim": round(float(np.mean(sims)), 4), "sims": sims}


def op_asr(req: dict[str, Any]) -> dict[str, Any]:
    model = _STATE["asr"]
    if model is None:
        raise RuntimeError("ASR model not loaded: send {'op': 'load', 'asr_model': ...} first")
    lang = req.get("lang") or None
    if lang:
        lang = lang.split("-")[0].split("_")[0].lower()
    opts = req.get("options") or {}
    audio = _read_16k(req["wav"])[0].numpy()  # decoded here: faster-whisper's PyAV path is version-fragile
    if audio.size == 0:
        raise ValueError(f"empty audio: {req['wav']}")
    # VAD drops silent or noisy tails, on which Whisper hallucinates text ("to be continued...");
    # a 0.5 s pause already ends a speech chunk (the library default of 2 s keeps short tails)
    vad = bool(opts.get("vad_filter", True))
    segments, info = model.transcribe(
        audio,
        language=lang,
        beam_size=int(opts.get("beam_size", 5)),
        vad_filter=vad,
        vad_parameters=dict(opts.get("vad_parameters") or VAD_PARAMETERS) if vad else None,
        condition_on_previous_text=False,
        without_timestamps=True,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return {
        "id": req.get("id"),
        "text": text,
        "lang": info.language,
        "lang_prob": round(float(info.language_probability or 0.0), 4),
        "dur_s": round(float(info.duration or 0.0), 3),
    }


OPS = {"load": op_load, "asr": op_asr, "embed": op_embed, "sim": op_sim}


def main() -> int:
    out = _protocol_stdout()
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")

    def reply(payload: dict[str, Any]) -> None:
        out.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        out.flush()

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            reply({"ok": False, "op": None, "error": f"invalid JSON: {exc.msg}"})
            continue
        op = req.get("op")
        if op == "quit":
            reply({"ok": True, "op": "quit"})
            return 0
        handler = OPS.get(op)
        if handler is None:
            reply({"ok": False, "op": op, "id": req.get("id"), "error": f"unknown op '{op}'"})
            continue
        try:
            reply({"ok": True, "op": op, **handler(req)})
        except Exception as exc:
            log(traceback.format_exc())
            reply({"ok": False, "op": op, "id": req.get("id"), "error": f"{type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
