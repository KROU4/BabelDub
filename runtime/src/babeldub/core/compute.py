"""GPU-first compute policy.

If an NVIDIA GPU is present, every neural workload (TTS, ASR, speaker embeddings,
source separation) runs on it: it is one to two orders of magnitude faster than CPU.
CPU is used only when there is no usable GPU or the user explicitly asks for it
(project setting compute.device = "cpu"). Falling back to CPU while a GPU exists is
never silent: workers report the device they actually use and commands warn.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from typing import Any

from babeldub.core.output import EXIT_USAGE, BabelDubError

DEVICES = ("auto", "cuda", "cpu")


@lru_cache(maxsize=1)
def detect_gpu() -> dict[str, Any]:
    """NVIDIA GPU info via nvidia-smi (no torch needed in the CLI process)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"available": False, "reason": "nvidia-smi not found"}
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": f"nvidia-smi failed: {exc}"}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"available": False, "reason": (proc.stderr or "no GPU reported").strip()[:200]}
    gpus = []
    for line in proc.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            gpus.append(
                {
                    "name": parts[0],
                    "vram_gb": round(float(parts[1]) / 1024, 1),
                    "used_gb": round(float(parts[2]) / 1024, 1),
                    "driver": parts[3],
                }
            )
    if not gpus:
        return {"available": False, "reason": "could not parse nvidia-smi output"}
    return {"available": True, "gpus": gpus, "name": gpus[0]["name"], "vram_gb": gpus[0]["vram_gb"]}


def resolve_device(setting: str | None = "auto") -> str:
    """Map the project setting to the device a worker must use: 'cuda' or 'cpu'."""
    setting = (setting or "auto").lower()
    if setting not in DEVICES:
        raise BabelDubError(f"unknown compute.device '{setting}'", code=EXIT_USAGE, allowed=list(DEVICES))
    if setting == "cpu":
        return "cpu"
    if detect_gpu()["available"]:
        return "cuda"
    if setting == "cuda":
        raise BabelDubError(
            "compute.device=cuda but no NVIDIA GPU is visible",
            code=EXIT_USAGE,
            hint="check the driver with `nvidia-smi` or set `babeldub set compute.device=auto`",
        )
    return "cpu"


def device_warning(requested: str, actual: str | None) -> str | None:
    """Text for the summary when a worker ended up on CPU although a GPU should be used."""
    if requested == "cuda" and actual and not actual.startswith("cuda"):
        return (
            f"GPU is available but the worker runs on {actual}: the engine venv probably has a CPU-only "
            "torch build; run `babeldub setup --engines <engine> --force` to reinstall CUDA wheels"
        )
    return None
