"""BabelDub TTS worker: VoxCPM2 (openbmb/VoxCPM2, Apache-2.0, 2B, 48 kHz, 30 languages).

Runs inside BABELDUB_HOME/envs/voxcpm (torch + `voxcpm` from GitHub); never imports babeldub.
JSONL protocol (contract §6):
  load  {"model": dir|repo, "device": "cuda", "options": {"optimize": false, "load_denoiser": false}}
  synth {"id", "text", "ref_wav", "prompt_wav"?, "prompt_text"?, "out", "seed", "params": {...}}
        ref_wav     -> reference_wav_path: the timbre (the character's long original reference)
        prompt_wav  -> prompt_wav_path + prompt_text: a continuation prompt in the target language
                       (the character's accepted dubs and their exact text); both or neither
        params      cfg_value (2.0), inference_timesteps (10), min_len, max_len, normalize,
                    retry_badcase, retry_badcase_max_times, retry_badcase_ratio_threshold
        ref_text and x_vector_only are ignored: a VoxCPM2 reference needs no transcript.
  synth_batch {"seed", "items": [synth, ...]} -> {"results": [...]}: a sequential loop
  stats {} -> VRAM counters;  ping {};  quit {}
The text must be plain: U+0301 stress marks make the model add sounds (the CLI strips them).
Measured on an RTX 3060 Ti 8 GB: peak VRAM 6.5 GB.
"""

from __future__ import annotations

import inspect
import json
import os
import random
import sys
import time
from typing import Any, TextIO

GEN_KEYS = (
    "cfg_value", "inference_timesteps", "min_len", "max_len", "normalize", "retry_badcase",
    "retry_badcase_max_times", "retry_badcase_ratio_threshold",
)
DEFAULTS = {"cfg_value": 2.0, "inference_timesteps": 10}

STATE: dict[str, Any] = {"model": None, "sr": 48000, "device": None, "seed_kw": False}


def log(msg: str) -> None:
    sys.stderr.write(f"[tts_voxcpm] {msg}\n")
    sys.stderr.flush()


def _protocol_stream() -> TextIO:
    """Keep the real stdout for protocol lines only; fd 1 (C code, CUDA) goes to stderr."""
    try:
        sys.stdout.flush()
        fd = os.dup(1)
        os.dup2(2, 1)
        stream: TextIO = os.fdopen(fd, "w", encoding="utf-8", newline="\n", buffering=1)
    except (OSError, ValueError):
        stream = sys.stdout
    sys.stdout = sys.stderr
    return stream


def _cuda(torch: Any) -> bool:
    return bool(torch.cuda.is_available())


def op_load(req: dict[str, Any]) -> dict[str, Any]:
    import torch
    from voxcpm import VoxCPM

    device = str(req.get("device") or "cuda")
    if device.startswith("cuda") and not _cuda(torch):
        return {"ok": False, "op": "load", "error": "CUDA is not available in the voxcpm env",
                "hint": "check the NVIDIA driver and that torch is a CUDA build (babeldub doctor)"}
    options = req.get("options") or {}
    model_path = req.get("model") or "openbmb/VoxCPM2"
    started = time.perf_counter()
    model = VoxCPM.from_pretrained(
        model_path,
        load_denoiser=bool(options.get("load_denoiser", False)),
        optimize=bool(options.get("optimize", False)),  # torch.compile needs triton; off by default
        device=device,
    )
    tts = model.tts_model
    actual = str(getattr(tts, "device", device))
    if actual == "cuda" and _cuda(torch):
        actual = f"cuda:{torch.cuda.current_device()}"
    try:
        STATE["seed_kw"] = "seed" in inspect.signature(model._generate).parameters
    except (AttributeError, TypeError, ValueError):
        STATE["seed_kw"] = False
    STATE.update(model=model, sr=int(getattr(tts, "sample_rate", 48000) or 48000), device=actual)
    info: dict[str, Any] = {
        "engine": "voxcpm", "model": model_path, "device": actual, "torch": torch.__version__,
        "load_s": round(time.perf_counter() - started, 2), "batch": False, "sr": STATE["sr"],
    }
    if actual.startswith("cuda"):
        info["gpu"] = torch.cuda.get_device_name(0)
        info["vram_alloc_gb"] = round(torch.cuda.memory_allocated() / 2**30, 3)
        torch.cuda.reset_peak_memory_stats()
    log(f"loaded {model_path} on {actual} in {info['load_s']}s (sr {STATE['sr']})")
    return {"ok": True, "op": "load", "sr": STATE["sr"], "device": actual, "info": info}


def _parse(req: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Validated synth item, or (None, error reply)."""
    rid = req.get("id")
    text = str(req.get("text") or "").strip()
    ref_wav = req.get("ref_wav")
    prompt_wav = req.get("prompt_wav") or None
    prompt_text = str(req.get("prompt_text") or "").strip() or None
    if not text:
        return None, {"ok": False, "op": "synth", "id": rid, "error": "empty text"}
    if not ref_wav or not os.path.exists(ref_wav):
        return None, {"ok": False, "op": "synth", "id": rid, "error": f"reference not found: {ref_wav}"}
    if (prompt_wav is None) != (prompt_text is None):
        return None, {"ok": False, "op": "synth", "id": rid, "error": "prompt_wav and prompt_text go together"}
    if prompt_wav and not os.path.exists(prompt_wav):
        return None, {"ok": False, "op": "synth", "id": rid, "error": f"prompt not found: {prompt_wav}"}
    if not req.get("out"):
        return None, {"ok": False, "op": "synth", "id": rid, "error": "missing 'out' path"}
    params = dict(req.get("params") or {})
    gen = {**DEFAULTS, **{k: params[k] for k in GEN_KEYS if k in params}}
    return {
        "id": rid, "text": text, "ref_wav": os.path.abspath(ref_wav),
        "prompt_wav": os.path.abspath(prompt_wav) if prompt_wav else None, "prompt_text": prompt_text,
        "gen": gen, "out": req["out"], "seed": int(req.get("seed") or 0),
    }, {}


def _seed_all(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch.manual_seed(seed)
    if _cuda(torch):
        torch.cuda.manual_seed_all(seed)


def op_synth(req: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf
    import torch

    model = STATE["model"]
    if model is None:
        return {"ok": False, "op": "synth", "id": req.get("id"), "error": "model is not loaded (send op=load first)"}
    item, error = _parse(req)
    if item is None:
        return error
    kwargs: dict[str, Any] = dict(item["gen"])
    kwargs["reference_wav_path"] = item["ref_wav"]
    if item["prompt_wav"]:
        kwargs["prompt_wav_path"] = item["prompt_wav"]
        kwargs["prompt_text"] = item["prompt_text"]
    if STATE["seed_kw"]:
        kwargs["seed"] = item["seed"]
    on_cuda = str(STATE["device"]).startswith("cuda")
    try:
        _seed_all(item["seed"])
        started = time.perf_counter()
        wav = model.generate(text=item["text"], **kwargs)
        if on_cuda:
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        return {"ok": False, "op": "synth", "id": item["id"], "error": "CUDA out of memory"}
    audio = np.asarray(wav, dtype=np.float32).reshape(-1)
    if audio.size <= 1:
        return {"ok": False, "op": "synth", "id": item["id"], "error": "model returned empty audio"}
    peak = float(np.max(np.abs(audio)))
    if peak > 0.999:
        audio *= 0.999 / peak
    out = item["out"]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sr = int(STATE["sr"])
    sf.write(out, audio, sr, subtype="PCM_16")
    dur = audio.size / sr
    reply: dict[str, Any] = {
        "ok": True, "op": "synth", "id": item["id"], "wav": out, "sr": sr, "dur_s": round(dur, 4),
        "rtf": round(elapsed / max(dur, 1e-6), 4), "gen_s": round(elapsed, 3), "seed": item["seed"],
        "mode": "ref+prompt" if item["prompt_wav"] else "ref",
    }
    if on_cuda:
        reply["vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
    return reply


def op_synth_batch(req: dict[str, Any]) -> dict[str, Any]:
    """One line after another; each item answers like op=synth."""
    if STATE["model"] is None:
        return {"ok": False, "op": "synth_batch", "error": "model is not loaded (send op=load first)"}
    results: list[dict[str, Any]] = []
    for raw in req.get("items") or []:
        try:
            reply = op_synth(raw)
        except Exception as exc:  # one bad item must not fail the others
            log(f"synth {raw.get('id')} failed: {type(exc).__name__}: {exc}")
            reply = {"ok": False, "op": "synth", "id": raw.get("id"), "error": f"{type(exc).__name__}: {exc}"}
        results.append(reply)
    return {"ok": True, "op": "synth_batch", "results": results}


def op_stats(req: dict[str, Any]) -> dict[str, Any]:
    import torch

    out: dict[str, Any] = {"ok": True, "op": "stats", "device": STATE["device"]}
    if STATE["model"] is not None and str(STATE["device"]).startswith("cuda"):
        out["vram_alloc_gb"] = round(torch.cuda.memory_allocated() / 2**30, 3)
        out["vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
        if req.get("reset_peak"):
            torch.cuda.reset_peak_memory_stats()
    return out


HANDLERS = {"load": op_load, "synth": op_synth, "synth_batch": op_synth_batch, "stats": op_stats,
            "ping": lambda req: {"ok": True, "op": "ping"}}


def main() -> int:
    proto = _protocol_stream()
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
        proto.write(json.dumps(reply, ensure_ascii=False) + "\n")
        proto.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
