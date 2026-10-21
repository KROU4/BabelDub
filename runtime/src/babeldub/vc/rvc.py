"""RVC v2 through Applio's command line (`core.py`), run as subprocesses in the `applio` env.

Training (`babeldub vc train`): preprocess -> extract (rmvpe f0) -> train (pretrained HiFi-GAN 40 kHz)
-> index, in BABELDUB_HOME/engines/applio/logs/<name>/; the final <name>_<E>e_<steps>s.pth and the
.index are copied to the project's vc/<char_id>/. Conversion (`babeldub vc`): `core.py batch-infer`
on a folder; outputs are <stem>_output.wav at 40 kHz. The CLI never imports torch.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from babeldub.core.jsonl import now_iso
from babeldub.core.output import EXIT_ENV, BabelDubError
from babeldub.core.paths import engines_dir, env_python

ENGINE = "applio"
SAMPLE_RATE = 40000
TRAIN_STEP_TIMEOUT = 4 * 3600.0
_SAFE = re.compile(r"[^A-Za-z0-9]+")
_PTH = re.compile(r"_(\d+)e_(\d+)s\.pth$")


def applio_dir() -> Path:
    return engines_dir() / ENGINE


def require_applio() -> tuple[Path, Path]:
    """(env python, checkout) of the Applio engine, or EXIT_ENV with the setup command."""
    python, folder = env_python(ENGINE), applio_dir()
    if not python.exists() or not (folder / "core.py").exists():
        raise BabelDubError(
            "the RVC engine (Applio) is not installed",
            code=EXIT_ENV,
            hint="babeldub setup --engines applio (an NVIDIA GPU with 8 GB+ is recommended)",
            python=str(python), checkout=str(folder),
        )
    return python, folder


def model_name(slug: str, char_id: str) -> str:
    """Applio model folder name: ASCII only, unique per project and character."""
    tag = hashlib.sha1(f"{slug}/{char_id}".encode()).hexdigest()[:6]
    char = _SAFE.sub("", char_id)[:16] or "char"
    return f"bd_{_SAFE.sub('', slug)[:16]}_{char}_{tag}"


def cpu_cores() -> int:
    return max(1, min(6, (os.cpu_count() or 2) - 1))


def save_every(epochs: int) -> int:
    """Applio accepts --save-every-epoch in 1..100; use the largest divisor of `epochs` in that range
    so the final epoch is always saved."""
    return max(d for d in range(1, min(epochs, 100) + 1) if epochs % d == 0)


def auto_epochs(minutes: float, base: int = 20) -> int:
    """RVC needs a similar number of optimizer steps regardless of dataset size: small datasets
    (minor characters, 1-5 min of speech) get more epochs. 30 min -> base, 1.5 min -> ~130, cap 300."""
    if minutes <= 0:
        return base
    return int(min(300, max(base, round(base * 10.0 / minutes))))


def train_commands(python: Path, name: str, dataset: Path, epochs: int, gpu: str) -> list[list[str]]:
    py, cores = str(python), str(cpu_cores())
    sr = str(SAMPLE_RATE)
    return [
        [py, "core.py", "preprocess", "--model-name", name, "--dataset-path", str(dataset), "--sample-rate", sr,
         "--cpu-cores", cores],
        [py, "core.py", "extract", "--model-name", name, "--f0-method", "rmvpe", "--sample-rate", sr,
         "--gpu", gpu, "--cpu-cores", cores],
        [py, "core.py", "train", "--model-name", name, "--sample-rate", sr, "--total-epoch", str(epochs),
         "--save-every-epoch", str(save_every(epochs)), "--batch-size", "8", "--gpu", gpu, "--pretrained",
         "--index-algorithm", "Auto"],
        [py, "core.py", "index", "--model-name", name, "--index-algorithm", "Auto"],
    ]


def convert_command(python: Path, in_dir: Path, out_dir: Path, pth: Path, index: Path, *,
                    index_rate: float, protect: float, f0_method: str) -> list[str]:
    return [
        str(python), "core.py", "batch-infer", "--input-folder", str(in_dir), "--output-folder", str(out_dir),
        "--pth-path", str(pth), "--index-path", str(index), "--index-rate", str(index_rate),
        "--protect", str(protect), "--f0-method", f0_method, "--proposed-pitch",
    ]


def trained_files(folder: Path, name: str) -> tuple[Path | None, Path | None]:
    """The highest-epoch <name>_<E>e_<steps>s.pth and the .index in Applio's logs/<name>/."""
    pths = []
    for path in folder.glob(f"{name}_*e_*s.pth"):
        match = _PTH.search(path.name)
        if match:
            pths.append((int(match.group(1)), int(match.group(2)), path))
    pth = max(pths)[2] if pths else None
    indexes = sorted(folder.glob("*.index"), key=lambda p: (not p.name.startswith(name), p.name))
    return pth, indexes[0] if indexes else None


def run(cmd: list[str], cwd: Path, log: Path, timeout: float, cpu: bool = False) -> int:
    """Run an Applio command with output appended to `log`; -1 could not start, -2 timeout."""
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
    if cpu:
        env["CUDA_VISIBLE_DEVICES"] = ""
    with log.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"\n[{now_iso()}] $ {subprocess.list2cmdline(cmd)}\n")
        fh.flush()
        try:
            proc = subprocess.run(cmd, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  env=env, timeout=timeout, creationflags=0x08000000 if os.name == "nt" else 0)
        except subprocess.TimeoutExpired:
            fh.write(f"\n[babeldub] timeout after {timeout:.0f}s\n")
            return -2
        except OSError as exc:
            fh.write(f"\n[babeldub] cannot start: {exc}\n")
            return -1
    return proc.returncode


def log_tail(log: Path, n: int = 6) -> list[str]:
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln[:200] for ln in lines if ln.strip()][-n:]
