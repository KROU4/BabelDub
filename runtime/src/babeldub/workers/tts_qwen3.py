"""BabelDub TTS worker: Qwen3-TTS Base (voice cloning), official `qwen-tts` package.

Runs inside BABELDUB_HOME/envs/qwen3tts (torch + qwen-tts); never imports babeldub.
Protocol (contract §6), one JSON per line on stdin -> one JSON per line on stdout:
  load  {"model": dir|repo, "device": "cuda", "dtype": "bf16", "options": {"attn_implementation": "sdpa"}}
  synth {"id", "text", "lang": "ru", "ref_wav", "ref_text", "out", "seed", "params": {...}}
  synth_batch {"seed", "items": [synth, ...]} -> {"results": [synth answer, ...]} (one generate call)
  stats {} -> VRAM counters;  ping {};  quit {}
Dubbing with the original actor's voice: the reference is the original line (or the best clip
of the character) together with its source transcript, used in ICL mode so the model carries
over timbre, emotion and delivery. `params.x_vector_only` switches to speaker-embedding-only
cloning (used when the reference had to be cut and no longer matches its transcript).
Voice-clone prompts are cached per (ref_wav, ref_text), so a character reference is encoded once.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from collections import OrderedDict
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

LANG_NAMES = {
    "zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean", "de": "German",
    "fr": "French", "ru": "Russian", "pt": "Portuguese", "es": "Spanish", "it": "Italian",
    "auto": "Auto",
}
GEN_KEYS = (
    "do_sample", "top_k", "top_p", "temperature", "repetition_penalty", "subtalker_dosample",
    "subtalker_top_k", "subtalker_top_p", "subtalker_temperature", "max_new_tokens",
)
CODEC_HZ = 12.5  # Qwen3-TTS 12Hz tokenizer frame rate
MAX_PROMPTS = 64

STATE: dict[str, Any] = {"model": None, "sr": 24000, "cuda": False, "prompts": OrderedDict()}


def log(msg: str) -> None:
    sys.stderr.write(f"[tts_qwen3] {msg}\n")
    sys.stderr.flush()


def language_name(code: str | None) -> str:
    if not code:
        return "Auto"
    key = code.strip().lower().replace("_", "-").split("-")[0]
    return LANG_NAMES.get(key, code)


def op_load(req: dict[str, Any]) -> dict[str, Any]:
    import torch
    from qwen_tts import Qwen3TTSModel

    model_path = req.get("model") or "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    device = str(req.get("device") or "cuda")
    options = req.get("options") or {}
    if device.startswith("cuda") and not torch.cuda.is_available():
        return {"ok": False, "op": "load", "error": "CUDA is not available in the qwen3tts env",
                "hint": "check the NVIDIA driver and that torch is a CUDA build (babeldub doctor)"}
    dtype_name = str(req.get("dtype") or "bf16")
    if device == "cpu" and dtype_name != "fp32":
        dtype_name = "fp32"
    dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[dtype_name]
    device_map = "cuda:0" if device == "cuda" else device
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        model_path,
        device_map=device_map,
        dtype=dtype,
        attn_implementation=options.get("attn_implementation", "sdpa"),
    )
    STATE["model"] = model
    STATE["cuda"] = device.startswith("cuda")
    STATE["prompts"].clear()
    try:
        STATE["sr"] = int(model.model.speech_tokenizer.get_output_sample_rate())
    except Exception:  # older package layout: fall back to the documented 24 kHz
        STATE["sr"] = 24000
    info: dict[str, Any] = {
        "engine": "qwen3tts",
        "model": model_path,
        "dtype": dtype_name,
        "device": device_map,
        "torch": torch.__version__,
        "languages": sorted(str(x) for x in (model.get_supported_languages() or [])),
        "load_s": round(time.perf_counter() - started, 2),
        "batch": True,
    }
    if STATE["cuda"]:
        info["gpu"] = torch.cuda.get_device_name(0)
        info["vram_alloc_gb"] = round(torch.cuda.memory_allocated() / 2**30, 3)
        torch.cuda.reset_peak_memory_stats()
    log(f"loaded {model_path} in {info['load_s']}s")
    return {"ok": True, "op": "load", "sr": STATE["sr"], "info": info}


def _prompt(ref_wav: str, ref_text: str | None, x_vector_only: bool) -> tuple[Any, bool]:
    model = STATE["model"]
    path = os.path.abspath(ref_wav)
    key = (path, os.path.getmtime(path), None if x_vector_only else ref_text, x_vector_only)
    cache: OrderedDict[Any, Any] = STATE["prompts"]
    if key in cache:
        cache.move_to_end(key)
        return cache[key], True
    items = model.create_voice_clone_prompt(
        ref_audio=path, ref_text=None if x_vector_only else ref_text, x_vector_only_mode=x_vector_only
    )
    cache[key] = items
    while len(cache) > MAX_PROMPTS:
        cache.popitem(last=False)
    return items, False


def _parse(req: dict[str, Any], op: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Validated synth item, or (None, error reply)."""
    rid = req.get("id")
    text = str(req.get("text") or "").strip()
    ref_wav = req.get("ref_wav")
    if not text:
        return None, {"ok": False, "op": op, "id": rid, "error": "empty text"}
    if not ref_wav or not os.path.exists(ref_wav):
        return None, {"ok": False, "op": op, "id": rid, "error": f"reference not found: {ref_wav}"}
    if not req.get("out"):
        return None, {"ok": False, "op": op, "id": rid, "error": "missing 'out' path"}
    params = dict(req.get("params") or {})
    ref_text = (req.get("ref_text") or "").strip() or None
    x_vector_only = bool(params.pop("x_vector_only", False)) or ref_text is None
    return {
        "id": rid, "text": text, "lang": language_name(req.get("lang")), "ref_wav": ref_wav, "ref_text": ref_text,
        "xvec": x_vector_only, "gen": {k: params[k] for k in GEN_KEYS if k in params},
        "out": req["out"], "seed": int(req.get("seed") or 0),
    }, {}


def _seed_all(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch.manual_seed(seed)
    if STATE["cuda"]:
        torch.cuda.manual_seed_all(seed)


def _save(item: dict[str, Any], wav: Any, sr: int, rtf: float, gen_s: float, op: str) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    audio = np.asarray(wav, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return {"ok": False, "op": op, "id": item["id"], "error": "model returned empty audio"}
    peak = float(np.max(np.abs(audio)))
    if peak > 0.999:
        audio *= 0.999 / peak
    out = item["out"]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sf.write(out, audio, int(sr), subtype="PCM_16")
    dur = audio.size / int(sr)
    reply: dict[str, Any] = {
        "ok": True, "op": op, "id": item["id"], "wav": out, "sr": int(sr), "dur_s": round(dur, 4),
        "rtf": round(rtf, 4), "gen_s": round(gen_s, 3), "seed": item["seed"],
        "mode": "xvec" if item["xvec"] else "icl",
    }
    limit = item["gen"].get("max_new_tokens")
    if limit and dur >= 0.97 * int(limit) / CODEC_HZ:
        reply["hit_token_limit"] = True  # generation ran to the cap: likely a runaway/loop
    if STATE["cuda"]:
        import torch

        reply["vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
    return reply


def op_synth(req: dict[str, Any]) -> dict[str, Any]:
    import torch

    model = STATE["model"]
    if model is None:
        return {"ok": False, "op": "synth", "id": req.get("id"), "error": "model is not loaded (send op=load first)"}
    item, error = _parse(req, "synth")
    if item is None:
        return error
    try:
        prompt, cached = _prompt(item["ref_wav"], item["ref_text"], item["xvec"])
        _seed_all(item["seed"])
        started = time.perf_counter()
        wavs, sr = model.generate_voice_clone(
            text=item["text"], language=item["lang"], voice_clone_prompt=prompt, **item["gen"]
        )
        if STATE["cuda"]:
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
    except torch.cuda.OutOfMemoryError:
        STATE["prompts"].clear()
        torch.cuda.empty_cache()
        return {"ok": False, "op": "synth", "id": item["id"], "error": "CUDA out of memory"}
    dur = max(len(wavs[0]) / int(sr), 1e-6)
    reply = _save(item, wavs[0], int(sr), elapsed / dur, elapsed, "synth")
    reply["prompt_cached"] = cached
    return reply


def op_synth_batch(req: dict[str, Any]) -> dict[str, Any]:
    """Several lines in one generate call (each with its own voice prompt); much higher throughput
    than one by one because generation is dominated by per-step overhead, not by batch width."""
    import torch

    model = STATE["model"]
    if model is None:
        return {"ok": False, "op": "synth_batch", "error": "model is not loaded (send op=load first)"}
    raw_items = req.get("items") or []
    replies: list[dict[str, Any] | None] = [None] * len(raw_items)
    valid: list[tuple[int, dict[str, Any]]] = []
    for i, raw in enumerate(raw_items):
        item, error = _parse(raw, "synth")
        if item is None:
            replies[i] = error
        else:
            valid.append((i, item))
    if valid:
        gen = dict(valid[0][1]["gen"])
        caps = [int(it["gen"]["max_new_tokens"]) for _, it in valid if it["gen"].get("max_new_tokens")]
        if caps:
            gen["max_new_tokens"] = max(caps)
        try:
            prompts = [_prompt(it["ref_wav"], it["ref_text"], it["xvec"])[0][0] for _, it in valid]
            _seed_all(int(req.get("seed") or valid[0][1]["seed"]))
            started = time.perf_counter()
            wavs, sr = model.generate_voice_clone(
                text=[it["text"] for _, it in valid], language=[it["lang"] for _, it in valid],
                voice_clone_prompt=prompts, **gen,
            )
            if STATE["cuda"]:
                torch.cuda.synchronize()
            elapsed = time.perf_counter() - started
        except torch.cuda.OutOfMemoryError:
            STATE["prompts"].clear()
            torch.cuda.empty_cache()
            return {"ok": False, "op": "synth_batch", "error": "CUDA out of memory"}
        total = max(sum(len(w) for w in wavs) / int(sr), 1e-6)
        for (i, item), wav in zip(valid, wavs, strict=True):
            replies[i] = _save(item, wav, int(sr), elapsed / total, elapsed, "synth")
            replies[i]["batch"] = len(valid)  # type: ignore[index]
    return {"ok": True, "op": "synth_batch", "results": replies}


def op_stats(req: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "op": "stats", "prompts_cached": len(STATE["prompts"])}
    if STATE["cuda"]:
        import torch

        out["vram_alloc_gb"] = round(torch.cuda.memory_allocated() / 2**30, 3)
        out["vram_peak_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
        if req.get("reset_peak"):
            torch.cuda.reset_peak_memory_stats()
    return out


HANDLERS = {"load": op_load, "synth": op_synth, "synth_batch": op_synth_batch, "stats": op_stats, "ping": lambda req: {"ok": True, "op": "ping"}}


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
