"""BabelDub QC worker: ASR (faster-whisper), VAD (Silero), prosody (librosa pyin), speaker
embeddings (SpeechBrain ECAPA), naturalness MOS (UTMOS22 via SpeechMOS) and, opt-in, emotion
(emotion2vec+ via funasr) and Russian stress marks for homographs (RUAccent, opt-in via tts.stress).

Runs inside the `qc` engine venv as `<env python> -u qc_worker.py`; it never imports babeldub.
Protocol (docs/dev/CONTRACT.md section 6): one JSON command per stdin line, exactly one JSON
answer per command on stdout; all logs go to stderr.

  {"op": "load", "asr_model": dir, "sv_model": dir, "mos_model": dir, "emotion_model": dir,
   "device": "auto|cuda|cpu", "compute_type": null, "sv_savedir": dir}
      asr/sv load at once; mos/emotion/vad load on their first request (a vad+asr run never
      loads the others)
  {"op": "asr", "id": ..., "wav": ..., "lang": "ru", "text"?: reference}
      -> {"text", "text_raw", "lang", "lang_prob", "dur_s"}: known hallucination tails ("продолжение следует...")
         that the reference does not contain are stripped from `text`; `text_raw` is Whisper's own output
  {"op": "asr_words", "id", "wav", "lang"?}               -> {"text", "lang", "words": [{w, start, end, prob}],
                                                              "avg_logprob", "no_speech_prob", "dur_s"}
  {"op": "vad", "id", "wav", "options"?}                  -> {"segments": [[start_s, end_s]], "speech_s", "dur_s"}
  {"op": "prosody", "id", "wav", "f0_hint_hz"?}           -> {"f0_median_hz", "f0_range_st", "rms_db", "rms_std_db",
                                                              "voiced_ratio", "voiced_s", "f0_bounds_hz", "dur_s",
                                                              "contour"}: two-pass pyin
  {"op": "emotion", "id", "wav"}                          -> {"label", "score", "emb": [floats]}
  {"op": "mos", "id", "wav"}                              -> {"mos"}
  {"op": "embed", "id": ..., "wav": ...}                   -> {"emb": [192 floats], "dur_s"}
  {"op": "sim", "id": ..., "wav": ..., "refs": [...]}      -> {"sim": mean cosine, "sims": [...]}
  {"op": "score", "id", "wav", "text", "lang", "orig_wav", "refs", "want": [cer, sim, emo, mos, prosody],
   "f0_hint_hz"?}
      -> {"cer", "cer_folded" (ru), "asr_text", "asr_text_raw", "sim", "emo_cos", "mos", "prosody",
          "orig_prosody", "unavailable": [...]}
  {"op": "spell", "texts": [...], "lang": "ru"}           -> {"texts": [...]}: digits spelled out (num2words)
  {"op": "accent", "texts": [...], "homographs_only": true}
      -> {"texts": [...]}: "+" before the stressed vowel (RUAccent; loads on the first request)
  {"op": "quit"}
"""

from __future__ import annotations

import json
import os
import re
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
F0_MIN_HZ, F0_MAX_HZ = 60.0, 700.0  # coarse pass: wide search range
F0_CLIP_HZ = (50.0, 1000.0)  # fine pass: median / 2 .. median x 2, clipped to this range
COARSE_HOP = 640  # 40 ms frames are enough for the median of the coarse pass
SCORE_WANTS = ("cer", "sim", "emo", "mos", "prosody")
CONTOUR_HZ = 20  # F0 contour points per second returned by prosody (for the contour correlation)

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
    "accent": None,
    "accent_dir": None,
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
    for key in ("mos", "emotion", "accent"):
        folder = req.get(f"{key}_model")
        if folder and folder != _STATE[f"{key}_dir"]:
            _STATE.update({key: None, f"{key}_dir": folder})
    info["load_s"] = round(time.time() - t0, 2)
    return {"device": device, "asr": _STATE["asr"] is not None, "sv": _STATE["sv"] is not None,
            "mos": _STATE["mos_dir"] is not None, "emotion": _STATE["emotion_dir"] is not None,
            "accent": _STATE["accent_dir"] is not None, "info": info}


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
        model.load_state_dict(torch.load(str(ckpts[0]), map_location="cpu", weights_only=True))
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


# Credits and outros Whisper invents on silent or noisy tails (learnt from subtitled video). Each entry is
# (phrase regex, credit tail): a credit tail lets up to 4 more words (a name) follow the phrase.
HALLUCINATION_TAILS: tuple[tuple[str, bool], ...] = (
    ("продолжение следует", False),
    ("субтитры (?:сделал|сделала|создавал|создавала|подготовил|подготовила|делал|делала|by)", True),
    ("редактор субтитров", True),
    ("корректор субтитров", True),
    ("спасибо за просмотр", False),
    ("подписывайтесь на (?:наш )?канал", False),
    ("дякую за перегляд", False),
    ("субтитри (?:зробив|створив|by)", True),
    ("thanks for watching", False),
    ("thank you for watching", False),
    ("subtitles by", True),
    ("to be continued", False),
    ("please subscribe", False),
    ("ciąg dalszy nastąpi", False),
    ("dzięki za oglądanie", False),
    ("napisy (?:wykonane przez|stworzone przez|by)", True),
    ("untertitel (?:von|im auftrag des|der amara org community)", True),
    ("vielen dank fürs zuschauen", False),
    ("fortsetzung folgt", False),
    ("sous titres réalisés par", True),
    ("merci d avoir regardé", False),
    ("subtítulos (?:realizados )?por", True),
    ("gracias por ver", False),
    ("continuará", False),
)
_GAP = r"[\s\W_]+"  # what may stand between the words of a phrase in raw ASR text


def _tail_regex(phrase: str, credit: bool) -> re.Pattern[str]:
    body = phrase.replace(" ", _GAP)
    names = r"(?:[\s\W_]+[\w.]+){0,4}" if credit else ""
    return re.compile(rf"(?<!\w)(?:{body}){names}[\s\W_]*$", re.IGNORECASE)


# (the phrase in the normalised reference, where apostrophes are gone: "d'avoir" -> "davoir"; the tail regex)
_TAIL_RES = [(re.compile(phrase.replace(" ", r"\s*"), re.IGNORECASE), _tail_regex(phrase, credit))
             for phrase, credit in HALLUCINATION_TAILS]


def strip_hallucinations(text: str, reference: str | None) -> str:
    """Drop known Whisper hallucination phrases at the end of `text` unless the reference (the dub text)
    contains them; repeated until no such tail is left."""
    ref = _norm_text(reference)
    changed = True
    while changed and text:
        changed = False
        for in_ref, tail in _TAIL_RES:
            match = tail.search(text)
            if match and not in_ref.search(ref):
                text = text[: match.start()].rstrip(" \t,;:-—–")
                changed = True
    return text


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
        "text": strip_hallucinations(text, req.get("text")),
        "text_raw": text,
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


def _voiced_f0(f0: Any, voiced: Any) -> Any:
    import numpy as np

    return f0[voiced & np.isfinite(f0)] if f0 is not None and voiced is not None else np.zeros(0)


def f0_bounds(audio: Any, hint_hz: float | None, frame: int) -> tuple[float, float, str]:
    """(fmin, fmax, source) of the fine pyin pass: one octave either side of the speaker's median F0,
    clipped to F0_CLIP_HZ. The median is `hint_hz` (e.g. the character's) or a coarse pyin pass over the
    wide F0_MIN_HZ..F0_MAX_HZ range; the wide range itself when the coarse pass hears no voice."""
    import librosa
    import numpy as np

    median: float | None = float(hint_hz) if hint_hz and float(hint_hz) > 0 else None
    source = "hint" if median else "coarse"
    if median is None:
        f0, voiced, _prob = librosa.pyin(audio, fmin=F0_MIN_HZ, fmax=F0_MAX_HZ, sr=SV_SR, frame_length=frame,
                                         hop_length=COARSE_HOP)
        f0v = _voiced_f0(f0, voiced)
        if f0v.size < 3:
            return F0_MIN_HZ, F0_MAX_HZ, "wide"
        median = float(np.median(f0v))
    lo = min(max(median / 2.0, F0_CLIP_HZ[0]), F0_CLIP_HZ[1] / 2.0)
    hi = max(min(median * 2.0, F0_CLIP_HZ[1]), lo * 2.0)
    return lo, hi, source


def op_prosody(req: dict[str, Any]) -> dict[str, Any]:
    """Arousal features: F0 median and 10-90 % range in semitones (two-pass pyin: a coarse median, then
    pyin within one octave of it, so octave jumps do not inflate the range), active RMS and its spread,
    voiced ratio and voiced seconds. `f0_hint_hz` (the character's median F0) sets the bounds directly."""
    import librosa
    import numpy as np

    audio = _audio_16k(req["wav"]).astype(np.float32)
    dur = audio.size / SV_SR
    frame, hop = 1024, 160
    if audio.size < frame:
        audio = np.pad(audio, (0, frame - audio.size))
    fmin, fmax, source = f0_bounds(audio, req.get("f0_hint_hz"), frame)
    f0, voiced, _prob = librosa.pyin(audio, fmin=fmin, fmax=fmax, sr=SV_SR, frame_length=frame, hop_length=hop)
    f0v = _voiced_f0(f0, voiced)
    rms = librosa.feature.rms(y=audio, frame_length=frame // 2, hop_length=hop)[0]
    rms_db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    active = rms_db[rms_db > rms_db.max() - 30.0] if rms_db.size else rms_db  # speech frames, not silence
    out: dict[str, Any] = {
        "id": req.get("id"),
        "f0_median_hz": None,
        "f0_range_st": None,
        "rms_db": round(float(np.mean(active)), 2) if active.size else None,
        "rms_std_db": round(float(np.std(active)), 2) if active.size >= 2 else None,
        "voiced_ratio": round(float(np.mean(voiced)), 3) if voiced is not None and voiced.size else 0.0,
        "voiced_s": round(f0v.size * hop / SV_SR, 3),
        "f0_bounds_hz": [round(fmin, 1), round(fmax, 1)],
        "f0_bounds_source": source,
        "dur_s": round(dur, 3),
        "contour": [],
    }
    if f0 is not None and f0v.size >= 3:
        # voiced F0 in semitones relative to the median, CONTOUR_HZ points per second (null = unvoiced)
        step = max(1, int(round(SV_SR / hop / CONTOUR_HZ)))
        ref = float(np.median(f0v))
        out["contour"] = [
            round(float(12.0 * np.log2(v / ref)), 2) if ok and np.isfinite(v) else None
            for v, ok in zip(f0[::step], voiced[::step], strict=True)
        ]
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


# same as babeldub.qa.metrics.RU_VOWEL_FOLD: Russian vowel reduction (о -> а, е/э/я -> и)
RU_VOWEL_FOLD = str.maketrans({"о": "а", "е": "и", "э": "и", "я": "и"})


def _cer(reference: str | None, hypothesis: str | None, fold: bool = False) -> float:
    ref, hyp = _norm_text(reference), _norm_text(hypothesis)
    if fold:
        ref, hyp = ref.translate(RU_VOWEL_FOLD), hyp.translate(RU_VOWEL_FOLD)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, a in enumerate(ref, 1):
        row = [i]
        for j, b in enumerate(hyp, 1):
            row.append(min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (a != b)))
        prev = row
    return prev[-1] / len(ref)


_NUM_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![\w])")
_EN_THOUSANDS_RE = re.compile(r"(?<![\w.,])\d{1,3}(?:,\d{3})+(?![\w,])")


def _spell(text: str | None, lang: str | None) -> str:
    """Digits spelled out with num2words (same rules as babeldub.text.normalize.numbers_to_words)."""
    if not text or not lang:
        return text or ""
    try:
        from num2words import num2words
    except ImportError:
        return text
    code = lang.replace("_", "-").split("-")[0].lower()
    if code == "en":
        text = _EN_THOUSANDS_RE.sub(lambda m: m.group(0).replace(",", ""), text)

    def repl(match: Any) -> str:
        raw = match.group(0)
        value: Any = float(raw.replace(",", ".")) if ("." in raw or "," in raw) else int(raw)
        try:
            return str(num2words(value, lang=code))
        except Exception:
            return raw

    return _NUM_RE.sub(repl, text)


def op_spell(req: dict[str, Any]) -> dict[str, Any]:
    return {"id": req.get("id"), "texts": [_spell(str(t or ""), req.get("lang")) for t in req.get("texts") or []]}


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
            asr = op_asr({"wav": req["wav"], "lang": req.get("lang"), "text": req.get("text")})
            out["asr_text"], out["asr_text_raw"] = asr["text"], asr["text_raw"]
            ref, hyp = _spell(req.get("text"), req.get("lang")), _spell(asr["text"], req.get("lang"))
            out["cer"] = round(_cer(ref, hyp), 4)
            if _lang(req) == "ru":
                out["cer_folded"] = round(_cer(ref, hyp, fold=True), 4)
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
    if "prosody" in want:
        hint = req.get("f0_hint_hz")
        out["prosody"] = op_prosody({"wav": req["wav"], "f0_hint_hz": hint})
        if req.get("orig_wav"):
            out["orig_prosody"] = op_prosody({"wav": req["orig_wav"], "f0_hint_hz": hint})
    if unavailable:
        out["unavailable"] = unavailable
    return out


def _get_accent() -> Any:
    if _STATE["accent"] is None:
        folder = _STATE["accent_dir"]
        if not folder:
            raise RuntimeError("accent model not configured: send {'op': 'load', 'accent_model': dir} first")
        try:
            from ruaccent import RUAccent
        except ImportError as exc:
            raise RuntimeError("the accent op needs ruaccent in the qc env: uv pip install ruaccent") from exc
        Path(folder).mkdir(parents=True, exist_ok=True)
        model = RUAccent()
        # the speaker model forces HF offline mode; RUAccent fetches its dictionaries and model on first use
        import huggingface_hub.constants as hf_constants

        offline_env, offline = os.environ.pop("HF_HUB_OFFLINE", None), hf_constants.HF_HUB_OFFLINE
        hf_constants.HF_HUB_OFFLINE = False
        try:
            model.load(omograph_model_size="turbo3.1", use_dictionary=True, workdir=folder,
                       device="CUDA" if (_STATE["device"] or "").startswith("cuda") else "CPU")
        finally:
            hf_constants.HF_HUB_OFFLINE = offline
            if offline_env is not None:
                os.environ["HF_HUB_OFFLINE"] = offline_env
        _STATE["accent"] = model
        log(f"RUAccent loaded from {folder}")
    return _STATE["accent"]


def _keep_homograph_marks(text: str, homographs: Any, given: str = "") -> str:
    """Drop "+" from every word that is not a stress homograph (its plain form has several stressings);
    words already marked in the input (`given`, e.g. a glossary pronunciation) keep their marks."""
    import re

    kept = set(re.findall(r"[\w+]*\+[\w+]*", given))

    def word(match: Any) -> str:
        token = match.group(0)
        plain = token.replace("+", "")
        return token if token in kept or plain.lower() in homographs else plain

    return re.sub(r"[\w+]+", word, text)


def op_accent(req: dict[str, Any]) -> dict[str, Any]:
    """RUAccent stress marks ("+" before the stressed vowel), by default on homographs only."""
    model = _get_accent()
    texts = [str(t or "") for t in req.get("texts") or []]
    marked = [model.process_all(t) if t.strip() else t for t in texts]
    if req.get("homographs_only", True):
        marked = [_keep_homograph_marks(m, model.omographs, t) for m, t in zip(marked, texts, strict=True)]
    return {"id": req.get("id"), "texts": marked}


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
    "accent": op_accent,
    "spell": op_spell,
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
