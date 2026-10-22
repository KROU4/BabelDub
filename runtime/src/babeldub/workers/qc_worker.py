"""BabelDub QC worker: ASR (faster-whisper), VAD (Silero), prosody (librosa pyin), speaker
embeddings (SpeechBrain ECAPA), naturalness MOS (UTMOS22 via SpeechMOS) and, opt-in, emotion
(emotion2vec+ via funasr).

Runs inside the `qc` engine venv as `<env python> -u qc_worker.py`; it never imports babeldub.
Protocol (docs/dev/CONTRACT.md section 6): one JSON command per stdin line, exactly one JSON
answer per command on stdout; all logs go to stderr.

  {"op": "load", "asr_model": dir, "sv_model": dir, "mos_model": dir, "emotion_model": dir,
   "device": "auto|cuda|cpu", "compute_type": null, "sv_savedir": dir}
      asr/sv load at once; mos/emotion/vad load on their first request (a vad+asr run never
      loads the others)
  {"op": "asr", "id": ..., "wav": ..., "lang": "ru"}       -> {"text", "lang", "lang_prob", "dur_s"}
  {"op": "asr_words", "id", "wav", "lang"?}               -> {"text", "lang", "words": [{w, start, end, prob}],
                                                              "avg_logprob", "no_speech_prob", "dur_s"}
  {"op": "vad", "id", "wav", "options"?}                  -> {"segments": [[start_s, end_s]], "speech_s", "dur_s"}
  {"op": "prosody", "id", "wav"}                          -> {"f0_median_hz", "f0_range_st", "rms_db",
                                                              "voiced_ratio", "dur_s"}
  {"op": "emotion", "id", "wav"}                          -> {"label", "score", "emb": [floats]}
  {"op": "mos", "id", "wav"}                              -> {"mos"}
  {"op": "embed", "id": ..., "wav": ...}                   -> {"emb": [192 floats], "dur_s"}
  {"op": "sim", "id": ..., "wav": ..., "refs": [...]}      -> {"sim": mean cosine, "sims": [...]}
  {"op": "score", "id", "wav", "text", "lang", "orig_wav", "refs", "want": [cer, sim, emo, mos]}
      -> {"cer", "asr_text", "sim", "emo_cos", "mos", "unavailable": [...]}
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
# analysis VAD: a 250 ms pause already splits segments (the translator sees such pauses)
SEGMENT_VAD = {"threshold": 0.5, "min_silence_duration_ms": 250, "speech_pad_ms": 30, "min_speech_duration_ms": 60}
F0_MIN_HZ, F0_MAX_HZ = 60.0, 700.0
SCORE_WANTS = ("cer", "sim", "emo", "mos")

_STATE: dict[str, Any] = {
    "device": None,
    "asr": None,
    "asr_dir": None,
    "compute_type": None,
    "sv": None,
    "sv_dir": None,
    "emb_cache": {},
    "mos": None,
    "mos_dir": None,
    "emotion": None,
    "emotion_dir": None,
    "audio_cache": None,  # (key, 16 kHz mono numpy array): the ops of one line decode it once
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


def _audio_16k(path: str) -> Any:
    """16 kHz mono float32 numpy array; the last file is cached, so vad/asr/prosody decode it once."""
    try:
        st = os.stat(path)
        key: Any = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None  # let the decoder report the missing file
    cached = _STATE["audio_cache"]
    if key is not None and cached and cached[0] == key:
        return cached[1]
    audio = _read_16k(path)[0].numpy()
    if audio.size == 0:
        raise ValueError(f"empty audio: {path}")
    _STATE["audio_cache"] = (key, audio)
    return audio


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
        _STATE.update(asr=None, asr_dir=None, sv=None, sv_dir=None, emb_cache={}, mos=None, emotion=None)
    _STATE["device"] = device
    info: dict[str, Any] = {}
    sv_dir = req.get("sv_model")
    if sv_dir and (_STATE["sv"] is None or _STATE["sv_dir"] != sv_dir):
        info.update(_load_sv(sv_dir, device, req.get("sv_savedir")))
    asr_dir = req.get("asr_model")
    if asr_dir and (_STATE["asr"] is None or _STATE["asr_dir"] != asr_dir):
        info.update(_load_asr(asr_dir, device, req.get("compute_type")))
    # heavier optional models load on their first request
    for key in ("mos", "emotion"):
        folder = req.get(f"{key}_model")
        if folder and folder != _STATE[f"{key}_dir"]:
            _STATE.update({key: None, f"{key}_dir": folder})
    info["load_s"] = round(time.time() - t0, 2)
    return {"device": device, "asr": _STATE["asr"] is not None, "sv": _STATE["sv"] is not None,
            "mos": _STATE["mos_dir"] is not None, "emotion": _STATE["emotion_dir"] is not None, "info": info}


def _torch_device() -> str:
    device = _STATE["device"] or "cpu"
    return "cuda:0" if device == "cuda" else device


def _get_mos() -> Any:
    if _STATE["mos"] is None:
        folder = _STATE["mos_dir"]
        if not folder:
            raise RuntimeError("MOS model not configured: send {'op': 'load', 'mos_model': dir} first")
        import torch
        from speechmos.utmos22.strong.model import UTMOS22Strong

        ckpts = sorted(Path(folder).glob("*.pt")) + sorted(Path(folder).glob("*.pth"))
        if not ckpts:
            raise FileNotFoundError(f"no UTMOS22 checkpoint (*.pt) in {folder}")
        model = UTMOS22Strong()
        model.load_state_dict(torch.load(str(ckpts[0]), map_location="cpu"))
        model.eval().to(_torch_device())
        _STATE["mos"] = model
        log(f"MOS model loaded from {ckpts[0]}")
    return _STATE["mos"]


def _get_emotion() -> Any:
    if _STATE["emotion"] is None:
        folder = _STATE["emotion_dir"]
        if not folder:
            raise RuntimeError("emotion model not configured: send {'op': 'load', 'emotion_model': dir} first")
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError("the emotion op needs funasr in the qc env: uv pip install funasr") from exc
        _STATE["emotion"] = AutoModel(model=folder, device=_torch_device(), disable_update=True,
                                      disable_pbar=True, log_level="ERROR")
        log(f"emotion model loaded from {folder}")
    return _STATE["emotion"]


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
    audio = _audio_16k(req["wav"])  # decoded here: faster-whisper's PyAV path is version-fragile
    # VAD drops silent or noisy tails, on which Whisper hallucinates text ("to be continued...");
    # a 0.5 s pause already ends a speech chunk (the library default of 2 s keeps short tails)
    vad = bool(opts.get("vad_filter", True))

    def transcribe(use_vad: bool) -> tuple[str, Any]:
        segments, info = model.transcribe(
            audio,
            language=lang,
            beam_size=int(opts.get("beam_size", 5)),
            vad_filter=use_vad,
            vad_parameters=dict(opts.get("vad_parameters") or VAD_PARAMETERS) if use_vad else None,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return " ".join(seg.text.strip() for seg in segments).strip(), info

    text, info = transcribe(vad)
    if vad and not text:  # VAD heard no speech (a short or quiet bark): try once on the whole clip
        text, info = transcribe(False)
    return {
        "id": req.get("id"),
        "text": text,
        "lang": info.language,
        "lang_prob": round(float(info.language_probability or 0.0), 4),
        "dur_s": round(float(info.duration or 0.0), 3),
    }


def _lang(req: dict[str, Any]) -> str | None:
    lang = req.get("lang") or None
    return lang.split("-")[0].split("_")[0].lower() if lang else None


def op_asr_words(req: dict[str, Any]) -> dict[str, Any]:
    """Transcript with word times and confidences (the original lines: analyze)."""
    model = _STATE["asr"]
    if model is None:
        raise RuntimeError("ASR model not loaded: send {'op': 'load', 'asr_model': ...} first")
    opts = req.get("options") or {}
    audio = _audio_16k(req["wav"])

    def transcribe(use_vad: bool) -> tuple[list[Any], Any]:
        segments, info = model.transcribe(
            audio,
            language=_lang(req),
            beam_size=int(opts.get("beam_size", 5)),
            vad_filter=use_vad,
            vad_parameters=dict(opts.get("vad_parameters") or VAD_PARAMETERS) if use_vad else None,
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        return list(segments), info

    segments, info = transcribe(True)
    if not any(seg.text.strip() for seg in segments):  # a short quiet bark: once more without VAD
        segments, info = transcribe(False)
    words = [
        {"w": w.word.strip(), "start": round(float(w.start), 3), "end": round(float(w.end), 3),
         "prob": round(float(w.probability), 4)}
        for seg in segments for w in (seg.words or []) if w.word.strip()
    ]
    spans = [(max(float(seg.end) - float(seg.start), 0.01), seg) for seg in segments]
    total = sum(s for s, _ in spans)
    avg_logprob = sum(s * float(seg.avg_logprob) for s, seg in spans) / total if spans else None
    no_speech = min((float(seg.no_speech_prob) for seg in segments), default=1.0)
    return {
        "id": req.get("id"),
        "text": " ".join(seg.text.strip() for seg in segments).strip(),
        "lang": info.language,
        "lang_prob": round(float(info.language_probability or 0.0), 4),
        "words": words,
        "avg_logprob": None if avg_logprob is None else round(avg_logprob, 4),
        "no_speech_prob": round(no_speech, 4),
        "dur_s": round(audio.size / SV_SR, 3),
    }


def op_vad(req: dict[str, Any]) -> dict[str, Any]:
    """Silero VAD (bundled with faster-whisper): speech segments in seconds."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = _audio_16k(req["wav"])
    params = {**SEGMENT_VAD, **(req.get("options") or {})}
    stamps = get_speech_timestamps(audio, vad_options=VadOptions(**params), sampling_rate=SV_SR)
    segments = [[round(s["start"] / SV_SR, 3), round(s["end"] / SV_SR, 3)] for s in stamps]
    return {"id": req.get("id"), "segments": segments, "speech_s": round(sum(e - s for s, e in segments), 3),
            "dur_s": round(audio.size / SV_SR, 3)}


def op_prosody(req: dict[str, Any]) -> dict[str, Any]:
    """Arousal features: F0 median and 10-90 % range in semitones (pyin), active RMS, voiced ratio."""
    import librosa
    import numpy as np

    audio = _audio_16k(req["wav"]).astype(np.float32)
    dur = audio.size / SV_SR
    frame, hop = 1024, 160
    if audio.size < frame:
        audio = np.pad(audio, (0, frame - audio.size))
    f0, voiced, _prob = librosa.pyin(audio, fmin=F0_MIN_HZ, fmax=F0_MAX_HZ, sr=SV_SR, frame_length=frame,
                                     hop_length=hop)
    f0v = f0[voiced & np.isfinite(f0)] if f0 is not None else np.zeros(0)
    rms = librosa.feature.rms(y=audio, frame_length=frame // 2, hop_length=hop)[0]
    rms_db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    active = rms_db[rms_db > rms_db.max() - 30.0] if rms_db.size else rms_db  # speech frames, not silence
    out: dict[str, Any] = {
        "id": req.get("id"),
        "f0_median_hz": None,
        "f0_range_st": None,
        "rms_db": round(float(np.mean(active)), 2) if active.size else None,
        "voiced_ratio": round(float(np.mean(voiced)), 3) if voiced is not None and voiced.size else 0.0,
        "dur_s": round(dur, 3),
    }
    if f0v.size >= 3:
        lo, hi = np.percentile(f0v, [10, 90])
        out["f0_median_hz"] = round(float(np.median(f0v)), 1)
        out["f0_range_st"] = round(float(12.0 * np.log2(hi / lo)), 2)
    return out


def _emotion_of(path: str) -> dict[str, Any]:
    import numpy as np

    model = _get_emotion()
    res = model.generate(_audio_16k(path), fs=SV_SR, granularity="utterance", extract_embedding=True)[0]
    labels = [str(x).split("/")[-1] for x in res.get("labels") or []]
    scores = [float(x) for x in res.get("scores") or []]
    best = int(np.argmax(scores)) if scores else -1
    emb = np.asarray(res.get("feats"), dtype=np.float32).reshape(-1)
    return {"label": labels[best] if best >= 0 else None, "score": round(scores[best], 4) if best >= 0 else None,
            "emb": emb}


def op_emotion(req: dict[str, Any]) -> dict[str, Any]:
    res = _emotion_of(req["wav"])
    return {"id": req.get("id"), "label": res["label"], "score": res["score"],
            "emb": [round(float(x), 5) for x in res["emb"]]}


def _mos_of(path: str) -> float:
    import torch

    model = _get_mos()
    wave = torch.from_numpy(_audio_16k(path)).unsqueeze(0).to(_torch_device())
    with torch.inference_mode():
        return float(model(wave, SV_SR).reshape(-1)[0])


def op_mos(req: dict[str, Any]) -> dict[str, Any]:
    return {"id": req.get("id"), "mos": round(_mos_of(req["wav"]), 3)}


def _norm_text(text: str | None) -> str:
    """Same normalisation as babeldub.qa.metrics.normalize_text (lower, no markup/punctuation, ё -> е)."""
    import re
    import unicodedata

    if not text:
        return ""
    text = re.sub(r"\{[^{}]*\}|<[^<>]*>|\[[^\[\]]*\]", " ", text)
    text = unicodedata.normalize("NFC", text).lower().replace("ё", "е")
    for ch in "'’ʼ`´":
        text = text.replace(ch, "")
    text = "".join(" " if unicodedata.category(ch)[0] in ("P", "S", "C") else ch for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def _cer(reference: str | None, hypothesis: str | None) -> float:
    ref, hyp = _norm_text(reference), _norm_text(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, a in enumerate(ref, 1):
        row = [i]
        for j, b in enumerate(hyp, 1):
            row.append(min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (a != b)))
        prev = row
    return prev[-1] / len(ref)


def op_score(req: dict[str, Any]) -> dict[str, Any]:
    """Several metrics of one take in one call: CER, voice similarity, emotion cosine, MOS."""
    import numpy as np

    want = [w for w in (req.get("want") or SCORE_WANTS)]
    bad = [w for w in want if w not in SCORE_WANTS]
    if bad:
        raise ValueError(f"unknown score metrics {bad}; known: {list(SCORE_WANTS)}")
    out: dict[str, Any] = {"id": req.get("id"), "cer": None, "asr_text": None, "sim": None, "emo_cos": None,
                           "mos": None}
    unavailable: list[str] = []
    if "cer" in want:
        if _STATE["asr"] is None:
            unavailable.append("cer")
        else:
            asr = op_asr({"wav": req["wav"], "lang": req.get("lang")})
            out["asr_text"] = asr["text"]
            out["cer"] = round(_cer(req.get("text"), asr["text"]), 4)
    if "sim" in want:
        refs = req.get("refs") or []
        if _STATE["sv"] is None or not refs:
            unavailable.append("sim")
        else:
            out["sim"] = op_sim({"wav": req["wav"], "refs": refs})["sim"]
    if "emo" in want:
        if not _STATE["emotion_dir"] or not req.get("orig_wav"):
            unavailable.append("emo")
        else:
            a = _emotion_of(req["wav"])["emb"]
            b = _emotion_of(req["orig_wav"])["emb"]
            out["emo_cos"] = round(float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1.0)), 4)
    if "mos" in want:
        if not _STATE["mos_dir"]:
            unavailable.append("mos")
        else:
            out["mos"] = round(_mos_of(req["wav"]), 3)
    if unavailable:
        out["unavailable"] = unavailable
    return out


OPS = {
    "load": op_load,
    "asr": op_asr,
    "asr_words": op_asr_words,
    "vad": op_vad,
    "prosody": op_prosody,
    "emotion": op_emotion,
    "mos": op_mos,
    "embed": op_embed,
    "sim": op_sim,
    "score": op_score,
}


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
