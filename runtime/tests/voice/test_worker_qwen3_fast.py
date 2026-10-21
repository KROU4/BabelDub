"""tts_qwen3_fast.py worker with fake `torch` and `faster_qwen3_tts` modules (no GPU, no weights)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest
import soundfile as sf

from babeldub.workers.client import WorkerClient

FAKE_TORCH = '''
__version__ = "2.8.0+fake"
bfloat16, float16, float32 = "bf16", "fp16", "fp32"
SEEDS = []


def manual_seed(seed):
    SEEDS.append(seed)


class _Cuda:
    class OutOfMemoryError(RuntimeError):
        pass

    available = True

    def is_available(self):
        return self.available

    def current_device(self):
        return 0

    def get_device_name(self, index=0):
        return "Fake GPU"

    def memory_allocated(self):
        return 2**30

    def max_memory_allocated(self):
        return 3 * 2**30

    def reset_peak_memory_stats(self):
        pass

    def manual_seed_all(self, seed):
        pass

    def synchronize(self):
        pass

    def empty_cache(self):
        pass


cuda = _Cuda()
'''

FAKE_FASTER_QWEN = '''
import numpy as np

CALLS = []


class FasterQwen3TTS:
    sample_rate = 24000

    def __init__(self, path, kwargs):
        self.path, self.kwargs, self.warm = path, kwargs, None

    @classmethod
    def from_pretrained(cls, model_name, **kwargs):
        return cls(model_name, kwargs)

    def warmup(self, prefill_len=100):
        self.warm = prefill_len

    def generate_voice_clone(self, text, language, ref_audio=None, ref_text="", xvec_only=False,
                             max_new_tokens=2048, **kwargs):
        CALLS.append(dict(text=text, language=language, ref_audio=ref_audio, ref_text=ref_text,
                          xvec_only=xvec_only, max_new_tokens=max_new_tokens, **kwargs))
        if "OOM" in text:
            import torch
            raise torch.cuda.OutOfMemoryError("fake")
        seconds = max_new_tokens / 12.5 if "LOOP" in text else 1.5  # LOOP: runs to the token cap
        t = np.arange(int(seconds * self.sample_rate)) / self.sample_rate
        return [(0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)], self.sample_rate
'''


def _module(name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(name)
    exec(compile(source, f"<fake {name}>", "exec"), module.__dict__)
    return module


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, types.ModuleType, types.ModuleType]:
    from babeldub.workers import tts_qwen3_fast as w

    torch = _module("torch", FAKE_TORCH)
    fq = _module("faster_qwen3_tts", FAKE_FASTER_QWEN)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "faster_qwen3_tts", fq)
    monkeypatch.setattr(w, "STATE", {"model": None, "sr": 24000, "device": None})
    return w, torch, fq


def ref(tmp_path: Path) -> str:
    path = tmp_path / "ref.wav"
    sf.write(str(path), [0.0] * 2400, 24000)
    return str(path)


def req(tmp_path: Path, name: str, text: str = "Привет.", **params: Any) -> dict[str, Any]:
    return {"op": "synth", "id": name, "text": text, "lang": "ru-RU", "ref_wav": ref(tmp_path),
            "ref_text": "Hello there.", "out": str(tmp_path / f"{name}.wav"), "seed": 1234,
            "params": {"max_new_tokens": 100, **params}}


def test_load_needs_cuda_and_warms_up(worker: Any, tmp_path: Path) -> None:
    w, torch, _fq = worker
    cpu = w.op_load({"model": "m", "device": "cpu"})
    assert cpu["ok"] is False and "CUDA only" in cpu["error"]
    torch.cuda.available = False
    assert "not available" in w.op_load({"model": "m", "device": "cuda"})["error"]
    torch.cuda.available = True
    out = w.op_load({"model": str(tmp_path), "device": "cuda", "dtype": "bf16", "options": {"max_seq_len": 1024}})
    assert out["ok"] and out["device"] == "cuda:0" and out["sr"] == 24000
    assert out["info"]["batch"] is False and out["info"]["engine"] == "qwen3tts-fast"
    model = w.STATE["model"]
    assert model.path == str(tmp_path) and model.warm == 100
    assert model.kwargs == {"device": "cuda", "dtype": "bf16", "attn_implementation": "sdpa", "max_seq_len": 1024}


def test_synth_icl_xvec_runaway_and_errors(worker: Any, tmp_path: Path) -> None:
    w, torch, fq = worker
    assert w.op_synth(req(tmp_path, "early"))["error"].startswith("model is not loaded")
    w.op_load({"model": "m", "device": "cuda"})
    out = w.op_synth(req(tmp_path, "icl", temperature=0.7, subtalker_top_k=3))
    assert out["ok"] and out["mode"] == "icl" and out["dur_s"] == pytest.approx(1.5) and "hit_token_limit" not in out
    assert sf.info(out["wav"]).samplerate == 24000 and out["vram_peak_gb"] == 3.0
    call = fq.CALLS[-1]
    assert call["language"] == "Russian" and call["ref_text"] == "Hello there." and call["xvec_only"] is False
    assert call["max_new_tokens"] == 100 and call["temperature"] == 0.7 and "subtalker_top_k" not in call
    assert torch.SEEDS[-1] == 1234
    w.op_synth(req(tmp_path, "xvec", x_vector_only=True))
    assert fq.CALLS[-1]["xvec_only"] is True and fq.CALLS[-1]["ref_text"] == ""
    loop = w.op_synth(req(tmp_path, "loop", text="Ю, ю, LOOP"))
    assert loop["hit_token_limit"] is True and loop["dur_s"] == pytest.approx(8.0)
    assert w.op_synth(req(tmp_path, "oom", text="OOM"))["error"] == "CUDA out of memory"
    assert w.op_synth({**req(tmp_path, "noref"), "ref_wav": str(tmp_path / "missing.wav")})["ok"] is False
    batch = w.op_synth_batch({"items": [req(tmp_path, "b1"), req(tmp_path, "b2", text="OOM"), req(tmp_path, "b3")]})
    assert batch["ok"] and [r["ok"] for r in batch["results"]] == [True, False, True]


def test_protocol_over_stdin_stdout(tmp_path: Path) -> None:
    fakes = tmp_path / "fakes"
    (fakes / "torch").mkdir(parents=True)
    (fakes / "torch" / "__init__.py").write_text(FAKE_TORCH, encoding="utf-8")
    (fakes / "faster_qwen3_tts.py").write_text(FAKE_FASTER_QWEN, encoding="utf-8")
    client = WorkerClient(Path(sys.executable), "tts_qwen3_fast.py", log_path=tmp_path / "w.log",
                          extra_env={"PYTHONPATH": str(fakes)})
    with client:
        loaded = client.load({"op": "load", "model": "m", "device": "cuda"})
        assert loaded["ok"] and loaded["device"] == "cuda:0"
        out = client.request(req(tmp_path, "p"))
        assert out["ok"] and Path(out["wav"]).is_file()
        assert client.request({"op": "ping"})["ok"]
        assert client.request({"op": "nope"})["ok"] is False
        assert client.request({"op": "stats"})["vram_peak_gb"] == 3.0
