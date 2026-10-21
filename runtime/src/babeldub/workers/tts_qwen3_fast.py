"""BabelDub TTS worker: Qwen3-TTS Base through `faster-qwen3-tts` (CUDA graphs). CUDA only.

Runs inside BABELDUB_HOME/envs/qwen3tts-fast (torch + faster-qwen3-tts); never imports babeldub.
Same models, voice prompt and JSONL protocol as tts_qwen3.py (contract §6):
  load  {"model": dir|repo, "device": "cuda", "dtype": "bf16", "options": {"attn_implementation": "sdpa",
         "max_seq_len": 2048, "warmup_prefill": 100}}
  synth {"id", "text", "lang": "ru", "ref_wav", "ref_text", "out", "seed", "params": {...}}
  synth_batch {"seed", "items": [synth, ...]} -> {"results": [...]}: a sequential loop (the library
         generates one text per call; `load` reports info.batch=false, so `synth` sends single lines)
  stats {} -> VRAM counters;  ping {};  quit {}
The voice prompt is ref_audio + ref_text in ICL mode, `params.x_vector_only` (or an empty ref_text)
for timbre only; the library caches it per (ref_audio, ref_text, xvec_only). For the same seed the
output matches the official package. Measured on an RTX 3060 Ti 8 GB with 1.7B-Base: RTF ~0.52 per
line (official package ~3.7 single, ~1.2-1.4 in batches of 4), peak VRAM ~4.5 GB, ~15 s load+warmup.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from typing import Any, TextIO

LANG_NAMES = {
    "zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean", "de": "German",
    "fr": "French", "ru": "Russian", "pt": "Portuguese", "es": "Spanish", "it": "Italian",
    "auto": "Auto",
}
GEN_KEYS = (
    "do_sample", "top_k", "top_p", "temperature", "repetition_penalty", "max_new_tokens", "min_new_tokens",
)
CODEC_HZ = 12.5  # Qwen3-TTS 12Hz tokenizer: 12.5 generated tokens per second of audio

STATE: dict[str, Any] = {"model": None, "sr": 24000, "device": None}


def log(msg: str) -> None:
    sys.stderr.write(f"[tts_qwen3_fast] {msg}\n")
    sys.stderr.flush()


def _protocol_stream() -> TextIO:
    """Keep the real stdout for protocol lines only; fd 1 (C code, CUDA graphs) goes to stderr."""
    try:
        sys.stdout.flush()
        fd = os.dup(1)
        os.dup2(2, 1)
        stream: TextIO = os.fdopen(fd, "w", encoding="utf-8", newline="\n", buffering=1)
    except (OSError, ValueError):
        stream = sys.stdout
    sys.stdout = sys.stderr
    return stream


def language_name(code: str | None) -> str:
    if not code:
        return "Auto"
    key = code.strip().lower().replace("_", "-").split("-")[0]
    return LANG_NAMES.get(key, code)


def op_load(req: dict[str, Any]) -> dict[str, Any]:
    import torch

    device = str(req.get("device") or "cuda")
    if not device.startswith("cuda"):
        return {"ok": False, "op": "load", "error": "qwen3tts-fast runs on CUDA only",
                "hint": "use the official engine on the CPU: --engine qwen3tts"}
    if not torch.cuda.is_available():
        return {"ok": False, "op": "load", "error": "CUDA is not available in the qwen3tts-fast env",
                "hint": "check the NVIDIA driver and that torch is a CUDA build (babeldub doctor)"}
    from faster_qwen3_tts import FasterQwen3TTS

    model_path = req.get("model") or "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    options = req.get("options") or {}
    dtype_name = str(req.get("dtype") or "bf16")
    dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}.get(dtype_name, torch.bfloat16)
    started = time.perf_counter()
    model = FasterQwen3TTS.from_pretrained(
        model_path,
        device="cuda",
        dtype=dtype,
        attn_implementation=options.get("attn_implementation", "sdpa"),
        max_seq_len=int(options.get("max_seq_len", 2048)),
    )
    load_s = time.perf_counter() - started
    model.warmup(prefill_len=int(options.get("warmup_prefill", 100)))  # captures the CUDA graphs once
    STATE.update(model=model, sr=int(getattr(model, "sample_rate", 24000) or 24000),
                 device=f"cuda:{torch.cuda.current_device()}")
    info: dict[str, Any] = {
        "engine": "qwen3tts-fast",
        "model": model_path,
        "dtype": dtype_name,
        "device": STATE["device"],
        "torch": torch.__version__,
        "load_s": round(load_s, 2),
        "warmup_s": round(time.perf_counter() - started - load_s, 2),
        "batch": False,
        "gpu": torch.cuda.get_device_name(0),
        "vram_alloc_gb": round(torch.cuda.memory_allocated() / 2**30, 3),
    }
    torch.cuda.reset_peak_memory_stats()
    log(f"loaded {model_path} in {info['load_s']}s, warmup {info['warmup_s']}s")
    return {"ok": True, "op": "load", "sr": STATE["sr"], "device": STATE["device"], "info": info}


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
    ref_text = (req.get("ref_text") or "").strip()
    xvec = bool(params.pop("x_vector_only", False)) or not ref_text
    return {
        "id": rid, "text": text, "lang": language_name(req.get("lang")), "ref_wav": os.path.abspath(ref_wav),
        "ref_text": "" if xvec else ref_text, "xvec": xvec, "gen": {k: params[k] for k in GEN_KEYS if k in params},
        "out": req["out"], "seed": int(req.get("seed") or 0),
    }, {}


def _seed_all(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def _save(item: dict[str, Any], wav: Any, sr: int, gen_s: float) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf
    import torch

    audio = np.asarray(wav, dtype=np.float32).reshape(-1)
    if audio.size <= 1:  # the library returns one zero sample when no token was generated
        return {"ok": False, "op": "synth", "id": item["id"], "error": "model returned empty audio"}
    peak = float(np.max(np.abs(audio)))
    if peak > 0.999:
        audio *= 0.999 / peak
    out = item["out"]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sf.write(out, audio, int(sr), subtype="PCM_16")
    dur = audio.size / int(sr)
    reply: dict[str, Any] = {
        "ok": True, "op": "synth", "id": item["id"], "wav": out, "sr": int(sr), "dur_s": round(dur, 4),
        "rtf": round(gen_s / max(dur, 1e-6), 4), "gen_s": round(gen_s, 3), "seed": item["seed"],
        "mode": "xvec" if item["xvec"] else "icl",
        "vram_peak_gb": round(torch.cuda.max_memory_allocated() / 2**30, 3),
    }
    limit = item["gen"].get("max_new_tokens")
    if limit and dur >= 0.97 * int(limit) / CODEC_HZ:
        reply["hit_token_limit"] = True  # generation ran to the cap: likely a runaway/loop
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
        _seed_all(item["seed"])
        started = time.perf_counter()
        wavs, sr = model.generate_voice_clone(
            text=item["text"], language=item["lang"], ref_audio=item["ref_wav"], ref_text=item["ref_text"],
            xvec_only=item["xvec"], **item["gen"],
        )
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        return {"ok": False, "op": "synth", "id": item["id"], "error": "CUDA out of memory"}
    return _save(item, wavs[0], int(sr), elapsed)


def op_synth_batch(req: dict[str, Any]) -> dict[str, Any]:
    """One line after another (no batched generation in this library); each item answers like op=synth."""
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
    if STATE["model"] is not None:
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
