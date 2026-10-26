"""BabelDub TEST engine `dummy`: no neural network, no GPU, not a voice.

It exists so the synth -> post -> qa -> build chain can run in CI and in end-to-end tests
without an engine venv. It renders a harmonic buzz plus noise whose length follows the
syllable count of the text and whose pitch follows the reference recording, then writes it
as a WAV, speaking the same JSONL protocol as the real workers (contract §6).

Standalone script: runs under any Python with numpy + soundfile; never imports babeldub.
Test hooks in `params`: dummy_fail = "error" (answer ok=false) | "exit" (crash the process) |
"batch" (fail a whole synth_batch); dummy_sleep = seconds to stall before answering.
Like VoxCPM2 it accepts a continuation prompt (`prompt_wav` + `prompt_text`, mode "ref+prompt"), so the
bootstrap reference strategy can be tested end to end, and a delivery `control` text (ref_strategy=tags):
echoed in the reply, never voiced or counted in the length; with a prompt as well the prompt is dropped
(`prompt_dropped`), as VoxCPM2's worker does.

Runaway simulation (a TTS model stuck in a loop until its token cap), for the CLI's runaway guard:
  * the word RUNAWAY in `text` -> every request for that line runs away;
  * RUNAWAY in `ref_text` -> only ICL requests run away (timbre-only requests send no transcript),
    like a voice prompt whose transcript does not match its audio;
  * params.dummy_runaway_xvec = true -> timbre-only (x_vector_only) requests run away too.
A runaway answer is audio exactly as long as the cap (params.max_new_tokens at 12.5 tokens/s,
60 s without a cap) with "hit_token_limit": true. Like a real engine, normal output is cut at the cap.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, TextIO

import numpy as np
import soundfile as sf


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

SR = 24000
CODEC_HZ = 12.5  # tokens per second of audio, as in Qwen3-TTS 12Hz
RUNAWAY_MARKER = "RUNAWAY"
NO_CAP_RUNAWAY_S = 60.0
VOWELS = set("aeiouyáéíóúàèìòùâêîôûäëïöüåæøąęůаеёиоуыэюяіїєαεηιουωάέήίόύώ")


def count_syllables(text: str) -> int:
    return sum(1 for ch in text.lower() if ch in VOWELS)


def estimate_f0(path: str | None) -> float:
    """Rough pitch of the reference (autocorrelation over the loudest 50 ms frames)."""
    if not path or not os.path.exists(path):
        return 140.0
    try:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
    except (RuntimeError, OSError):
        return 140.0
    mono = data.mean(axis=1)[: int(sr * 6)]
    hop = int(sr * 0.05)
    if hop <= 0 or len(mono) < hop * 2:
        return 140.0
    frames = mono[: len(mono) // hop * hop].reshape(-1, hop)
    loud = frames[np.argsort((frames**2).mean(axis=1))[-8:]]
    lo, hi = int(sr / 400), int(sr / 70)
    estimates = []
    for frame in loud:
        frame = frame - frame.mean()
        ac = np.correlate(frame, frame, mode="full")[len(frame) - 1 :]
        if ac[0] <= 0 or hi >= len(ac):
            continue
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] > 0.3 * ac[0]:
            estimates.append(sr / lag)
    return float(np.median(estimates)) if estimates else 140.0


def render(text: str, f0: float, seed: int, rate: float, speech_s: float | None = None) -> np.ndarray:
    """Buzz of the text's syllables at `rate`, or of exactly `speech_s` seconds (a loop of syllables)."""
    rng = np.random.default_rng(seed)
    syllables = max(1, count_syllables(text))
    if speech_s is None:
        speech_s = syllables / rate * float(rng.uniform(0.92, 1.08))
    else:
        syllables = max(1, int(speech_s * rate))
    n = int(speech_s * SR)
    t = np.arange(n) / SR
    contour = f0 * (1.0 + 0.04 * np.sin(2 * np.pi * 0.8 * t)) * (1.0 - 0.08 * t / max(speech_s, 1e-3))
    phase = 2 * np.pi * np.cumsum(contour) / SR
    voice = sum(np.sin(k * phase) / k for k in range(1, 9))
    noise = rng.normal(0.0, 1.0, n)
    envelope = np.clip(np.sin(np.pi * t * syllables / speech_s), 0.0, None) ** 0.6
    signal = envelope * (0.7 * voice + 0.15 * noise)
    signal /= max(1e-6, float(np.max(np.abs(signal))))
    pad = np.zeros(int(0.12 * SR))
    return np.concatenate([pad, 0.5 * signal, pad]).astype(np.float32)


def op_load(req: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "op": "load",
        "sr": SR,
        "info": {"engine": "dummy", "test_only": True, "model": req.get("model") or "dummy",
                 "note": "test tone generator, not speech", "batch": True},
    }


def op_synth_batch(req: dict[str, Any]) -> dict[str, Any]:
    """Same answers as op=synth for each item (the real engines run one batched generate call)."""
    items = req.get("items") or []
    if any((it.get("params") or {}).get("dummy_fail") == "batch" for it in items):
        return {"ok": False, "op": "synth_batch", "error": "dummy batch failure requested"}
    return {"ok": True, "op": "synth_batch", "results": [op_synth(it) for it in items]}


def op_synth(req: dict[str, Any]) -> dict[str, Any]:
    params = req.get("params") or {}
    if params.get("dummy_sleep"):
        time.sleep(float(params["dummy_sleep"]))
    fail = params.get("dummy_fail")
    if fail == "exit":
        os._exit(3)
    if fail == "error":
        return {"ok": False, "op": "synth", "id": req.get("id"), "error": "dummy failure requested"}
    text = str(req.get("text") or "").strip()
    if not text:
        return {"ok": False, "op": "synth", "id": req.get("id"), "error": "empty text"}
    control = str(req.get("control") or "").strip() or None
    prompt_wav = req.get("prompt_wav")
    dropped = bool(control and (prompt_wav or req.get("prompt_text")))
    if dropped:
        prompt_wav = None
    if prompt_wav and (not os.path.exists(prompt_wav) or not str(req.get("prompt_text") or "").strip()):
        return {"ok": False, "op": "synth", "id": req.get("id"), "error": "prompt_wav needs an existing file and prompt_text"}
    ref_text = str(req.get("ref_text") or "").strip()
    xvec = bool(params.get("x_vector_only")) or not ref_text
    if xvec:
        runaway = RUNAWAY_MARKER in text or bool(params.get("dummy_runaway_xvec"))
    else:
        runaway = RUNAWAY_MARKER in text or RUNAWAY_MARKER in ref_text
    cap = int(params["max_new_tokens"]) if params.get("max_new_tokens") else None
    cap_s = cap / CODEC_HZ if cap else None
    started = time.perf_counter()
    f0, seed, rate = estimate_f0(req.get("ref_wav")), int(req.get("seed") or 0), float(params.get("syllables_per_s", 5.0))
    if runaway:
        total_s = cap_s or NO_CAP_RUNAWAY_S
        audio = render(text, f0, seed, rate, speech_s=max(0.1, total_s - 0.24))[: int(total_s * SR)]
    else:
        audio = render(text, f0, seed, rate)
    hit = cap_s is not None and len(audio) >= 0.97 * cap_s * SR
    if cap_s is not None:
        audio = audio[: int(cap_s * SR)]
    out = req["out"]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sf.write(out, audio, SR, subtype="PCM_16")
    dur = len(audio) / SR
    reply: dict[str, Any] = {
        "ok": True, "op": "synth", "id": req.get("id"), "wav": out, "sr": SR, "dur_s": round(dur, 4),
        "rtf": round((time.perf_counter() - started) / dur, 4),
        "mode": "ref+prompt" if prompt_wav else ("xvec" if xvec else "icl"),
    }
    if hit:
        reply["hit_token_limit"] = True
    if control:
        reply["control"] = control
    if dropped:
        reply["prompt_dropped"] = True
    return reply


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
            try:
                if op == "load":
                    reply = op_load(req)
                elif op == "synth":
                    reply = op_synth(req)
                elif op == "synth_batch":
                    reply = op_synth_batch(req)
                elif op == "ping":
                    reply = {"ok": True, "op": "ping"}
                else:
                    reply = {"ok": False, "op": op, "error": f"unknown op '{op}'"}
            except Exception as exc:  # keep the worker alive; report the failure
                reply = {"ok": False, "op": op, "id": req.get("id"), "error": f"{type(exc).__name__}: {exc}"}
        PROTO.write(json.dumps(reply, ensure_ascii=False) + "\n")
        PROTO.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
