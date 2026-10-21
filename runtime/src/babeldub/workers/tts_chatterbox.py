"""BabelDub TTS worker: Chatterbox Multilingual (Resemble AI, MIT) for languages Qwen3-TTS lacks (pl, tr, ...).

Runs inside BABELDUB_HOME/envs/chatterbox (torch 2.6 + `chatterbox-tts` from GitHub for the
V3 checkpoint); never imports babeldub. Same JSONL protocol as tts_qwen3.py (contract §6).
  load  {"model": dir|"ResembleAI/chatterbox", "device": "cuda", "options": {"t3_model": "v3"}}
  synth {"id", "text", "lang": "pl", "ref_wav", "ref_lang": "en", "out", "seed", "params": {...}}
Chatterbox clones from audio only (no transcript). `exaggeration` controls emotional intensity;
when the reference is in another language than the target, cfg_weight defaults to 0 (upstream
advice against accent transfer). Every output carries Resemble's inaudible PerTh watermark.
Speaker conditionals are cached per (ref_wav, exaggeration).
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, TextIO


def _protocol_stream() -> TextIO:
    """Keep the real stdout for protocol lines only; fd 1 (C code, child processes) goes to stderr."""
    try:
        sys.stdout.flush()
        fd = os.dup(1)
        os.dup2(2, 1)
        stream: TextIO = os.fdopen(fd, "w", encoding="utf-8", newline="\n", buffering=1)
    except (OSError, ValueError):
        stream = sys.stdout
    sys.stdout = sys.stderr
    return stream


PROTO = _protocol_stream()

GEN_KEYS = ("exaggeration", "cfg_weight", "temperature", "repetition_penalty", "min_p", "top_p")
MAX_CONDS = 32

STATE: dict[str, Any] = {"model": None, "sr": 24000, "cuda": False, "conds": OrderedDict(), "languages": []}


def log(msg: str) -> None:
    sys.stderr.write(f"[tts_chatterbox] {msg}\n")
    sys.stderr.flush()


def _base_lang(code: str | None) -> str:
    return (code or "").strip().lower().replace("_", "-").split("-")[0]


def op_load(req: dict[str, Any]) -> dict[str, Any]:
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    device = str(req.get("device") or "cuda")
    if device.startswith("cuda") and not torch.cuda.is_available():
        return {"ok": False, "op": "load", "error": "CUDA is not available in the chatterbox env",
                "hint": "check the NVIDIA driver and that torch is a CUDA build (babeldub doctor)"}
    options = req.get("options") or {}
    t3_model = options.get("t3_model", "v3")
    model_ref = str(req.get("model") or "ResembleAI/chatterbox")
    started = time.perf_counter()
    loaded_t3 = t3_model
    local = Path(model_ref)
    try:
        if local.is_dir():
            model = ChatterboxMultilingualTTS.from_local(local, device, t3_model=t3_model)
        else:
            model = ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model=t3_model)
    except TypeError:  # chatterbox-tts <= 0.1.7 (PyPI) has no t3_model: only the V2 checkpoint
        loaded_t3 = "v2 (installed chatterbox-tts has no t3_model; install it from GitHub for V3)"
        if local.is_dir():
            model = ChatterboxMultilingualTTS.from_local(local, device)
        else:
            model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    STATE.update(model=model, sr=int(model.sr), cuda=device.startswith("cuda"))
    STATE["conds"].clear()
    STATE["languages"] = sorted(ChatterboxMultilingualTTS.get_supported_languages().keys())
    info: dict[str, Any] = {
        "engine": "chatterbox",
        "model": model_ref,
        "t3_model": loaded_t3,
        "device": device,
        "torch": torch.__version__,
        "languages": STATE["languages"],
        "watermark": "PerTh (inaudible, added by the library to every file)",
        "load_s": round(time.perf_counter() - started, 2),
    }
    if STATE["cuda"]:
        info["gpu"] = torch.cuda.get_device_name(0)
        info["vram_alloc_gb"] = round(torch.cuda.memory_allocated() / 2**30, 3)
        torch.cuda.reset_peak_memory_stats()
    return {"ok": True, "op": "load", "sr": STATE["sr"], "info": info}


def _conditionals(ref_wav: str, exaggeration: float) -> bool:
    """Make model.conds match the reference; returns True when served from the cache."""
    model = STATE["model"]
    path = os.path.abspath(ref_wav)
    key = (path, os.path.getmtime(path), float(exaggeration))
    cache: OrderedDict[Any, Any] = STATE["conds"]
    if key in cache:
        cache.move_to_end(key)
        model.conds = cache[key]
        return True
    model.prepare_conditionals(path, exaggeration=exaggeration)
    cache[key] = model.conds
    while len(cache) > MAX_CONDS:
        cache.popitem(last=False)
    return False


def op_synth(req: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf
    import torch

    rid = req.get("id")
    model = STATE["model"]
    if model is None:
        return {"ok": False, "op": "synth", "id": rid, "error": "model is not loaded (send op=load first)"}
    text = str(req.get("text") or "").strip()
    ref_wav = req.get("ref_wav")
    lang = _base_lang(req.get("lang"))
    if not text:
        return {"ok": False, "op": "synth", "id": rid, "error": "empty text"}
    if not ref_wav or not os.path.exists(ref_wav):
        return {"ok": False, "op": "synth", "id": rid, "error": f"reference not found: {ref_wav}"}
    if STATE["languages"] and lang not in STATE["languages"]:
        return {"ok": False, "op": "synth", "id": rid, "error": f"language '{lang}' is not supported by Chatterbox"}
    params = dict(req.get("params") or {})
    ref_lang = _base_lang(req.get("ref_lang"))
    gen: dict[str, Any] = {
        "exaggeration": 0.5,
        "cfg_weight": 0.0 if ref_lang and ref_lang != lang else 0.5,
        "temperature": 0.8,
    }
    gen.update({k: params[k] for k in GEN_KEYS if k in params})
    seed = int(req.get("seed") or 0)
    try:
        cached = _conditionals(ref_wav, float(gen["exaggeration"]))
        random.seed(seed)
        np.random.seed(seed % 2**32)
        torch.manual_seed(seed)
        if STATE["cuda"]:
            torch.cuda.manual_seed_all(seed)
        started = time.perf_counter()
        wav = model.generate(text, language_id=lang, audio_prompt_path=None, **gen)
        if STATE["cuda"]:
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
    except torch.cuda.OutOfMemoryError:
        STATE["conds"].clear()
        torch.cuda.empty_cache()
        return {"ok": False, "op": "synth", "id": rid, "error": "CUDA out of memory"}
    audio = wav.detach().cpu().numpy().astype(np.float32).reshape(-1) if hasattr(wav, "detach") else np.asarray(wav, np.float32).reshape(-1)
    if audio.size == 0:
        return {"ok": False, "op": "synth", "id": rid, "error": "model returned empty audio"}
    peak = float(np.max(np.abs(audio)))
    if peak > 0.999:
        audio *= 0.999 / peak
    out = req["out"]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sr = int(STATE["sr"])
    sf.write(out, audio, sr, subtype="PCM_16")
    dur = audio.size / sr
    reply: dict[str, Any] = {
        "ok": True, "op": "synth", "id": rid, "wav": out, "sr": sr, "dur_s": round(dur, 4),
        "rtf": round(elapsed / max(dur, 1e-6), 4), "gen_s": round(elapsed, 3), "seed": seed,
        "cfg_weight": gen["cfg_weight"], "exaggeration": gen["exaggeration"], "conds_cached": cached,
    }
    if STATE["cuda"]:
        reply["vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
    return reply


def op_stats(req: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "op": "stats", "conds_cached": len(STATE["conds"])}
    if STATE["cuda"]:
        import torch

        out["vram_alloc_gb"] = round(torch.cuda.memory_allocated() / 2**30, 3)
        out["vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
        if req.get("reset_peak"):
            torch.cuda.reset_peak_memory_stats()
    return out


HANDLERS = {"load": op_load, "synth": op_synth, "stats": op_stats, "ping": lambda req: {"ok": True, "op": "ping"}}


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            reply: dict[str, Any] = {"ok": False, "op": None, "error": f"bad JSON: {exc.msg}"}
        else:
            op = req.get("op")
            if op == "quit":
                return 0
            handler = HANDLERS.get(op)
            if handler is None:
                reply = {"ok": False, "op": op, "error": f"unknown op '{op}'"}
            else:
                try:
                    reply = handler(req)
                except Exception as exc:  # report and keep serving
                    log(f"{op} failed: {type(exc).__name__}: {exc}")
                    reply = {"ok": False, "op": op, "id": req.get("id"), "error": f"{type(exc).__name__}: {exc}"}
        PROTO.write(json.dumps(reply, ensure_ascii=False) + "\n")
        PROTO.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
