"""tts_voxcpm.py worker with fake `torch` and `voxcpm` modules (no GPU, no weights)."""

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
        return int(6.5 * 2**30)

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

FAKE_VOXCPM = '''
import numpy as np

CALLS = []


class _Tts:
    sample_rate = 48000

    def __init__(self, device):
        self.device = device

    def text_tokenizer(self, text):
        return list(text)  # one token per character


class VoxCPM:
    def __init__(self, path, kwargs):
        self.path, self.kwargs = path, kwargs
        self.tts_model = _Tts(kwargs.get("device") or "cuda")

    @classmethod
    def from_pretrained(cls, hf_model_id, **kwargs):
        return cls(hf_model_id, kwargs)

    def _generate(self, text, prompt_wav_path=None, prompt_text=None, reference_wav_path=None, cfg_value=2.0,
                  inference_timesteps=10, min_len=2, max_len=4096, normalize=False, denoise=False,
                  retry_badcase=True, retry_badcase_max_times=3, retry_badcase_ratio_threshold=6.0,
                  streaming=False, seed=None):
        raise AssertionError("not called directly")

    def generate(self, text, **kwargs):
        CALLS.append(dict(text=text, **kwargs))
        if "OOM" in text:
            import torch
            raise torch.cuda.OutOfMemoryError("fake")
        t = np.arange(int(1.2 * 48000)) / 48000
        return (0.4 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
'''


def _module(name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(name)
    exec(compile(source, f"<fake {name}>", "exec"), module.__dict__)
    return module


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, types.ModuleType, types.ModuleType]:
    from babeldub.workers import tts_voxcpm as w

    torch = _module("torch", FAKE_TORCH)
    vox = _module("voxcpm", FAKE_VOXCPM)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "voxcpm", vox)
    monkeypatch.setattr(w, "STATE", {"model": None, "sr": 48000, "device": None, "seed_kw": False})
    return w, torch, vox


def wav(tmp_path: Path, name: str) -> str:
    path = tmp_path / f"{name}.wav"
    sf.write(str(path), [0.1] * 4800, 48000)
    return str(path)


def req(tmp_path: Path, name: str, text: str = "Привет.", **extra: Any) -> dict[str, Any]:
    return {"op": "synth", "id": name, "text": text, "lang": "ru", "ref_wav": wav(tmp_path, "long_ref"),
            "ref_text": "ignored", "out": str(tmp_path / f"{name}.wav"), "seed": 77, "params": {}, **extra}


def test_load_reports_device_and_needs_cuda(worker: Any, tmp_path: Path) -> None:
    w, torch, _vox = worker
    torch.cuda.available = False
    assert "not available" in w.op_load({"model": "m", "device": "cuda"})["error"]
    torch.cuda.available = True
    out = w.op_load({"model": str(tmp_path), "device": "cuda", "options": {}})
    assert out["ok"] and out["device"] == "cuda:0" and out["sr"] == 48000
    assert out["info"]["engine"] == "voxcpm" and out["info"]["batch"] is False
    model = w.STATE["model"]
    assert model.kwargs == {"load_denoiser": False, "optimize": False, "device": "cuda"}
    assert w.STATE["seed_kw"] is True  # this voxcpm takes generate(seed=...)


def test_synth_reference_prompt_and_errors(worker: Any, tmp_path: Path) -> None:
    w, torch, vox = worker
    assert w.op_synth(req(tmp_path, "early"))["error"].startswith("model is not loaded")
    w.op_load({"model": "m", "device": "cuda"})
    out = w.op_synth(req(tmp_path, "src", params={"cfg_value": 2.5, "temperature": 9}))
    assert out["ok"] and out["mode"] == "ref" and out["sr"] == 48000 and out["dur_s"] == pytest.approx(1.2)
    assert sf.info(out["wav"]).samplerate == 48000 and out["vram_peak_gb"] == 6.5
    call = vox.CALLS[-1]
    assert call["reference_wav_path"].endswith("long_ref.wav") and "prompt_wav_path" not in call
    assert call["cfg_value"] == 2.5 and call["inference_timesteps"] == 10 and "temperature" not in call
    assert call["seed"] == 77 and torch.SEEDS[-1] == 77
    prompt = wav(tmp_path, "ru_prompt")
    out = w.op_synth(req(tmp_path, "tgt", prompt_wav=prompt, prompt_text="Ну что, малой."))
    assert out["mode"] == "ref+prompt"
    assert vox.CALLS[-1]["prompt_wav_path"].endswith("ru_prompt.wav")
    assert vox.CALLS[-1]["prompt_text"] == "Ну что, малой."
    assert "go together" in w.op_synth(req(tmp_path, "half", prompt_wav=prompt))["error"]
    assert "prompt not found" in w.op_synth(req(tmp_path, "gone", prompt_wav=str(tmp_path / "x.wav"),
                                                  prompt_text="x"))["error"]
    assert w.op_synth({**req(tmp_path, "noref"), "ref_wav": str(tmp_path / "missing.wav")})["ok"] is False
    assert w.op_synth(req(tmp_path, "oom", text="OOM"))["error"] == "CUDA out of memory"
    batch = w.op_synth_batch({"items": [req(tmp_path, "b1"), req(tmp_path, "b2", text="OOM"), req(tmp_path, "b3")]})
    assert batch["ok"] and [r["ok"] for r in batch["results"]] == [True, False, True]


def test_control_text_building_is_a_pure_function() -> None:
    from babeldub.workers.tts_voxcpm import build_text, plain_ratio

    assert build_text("Стой!", "shouting, fast pace") == "(shouting, fast pace)Стой!"
    assert build_text("Стой!", None) == build_text("Стой!", "  ") == "Стой!"
    assert build_text("Стой!", "(calm)") == "(calm)Стой!"  # never nested parentheses
    assert plain_ratio(6.0, 10, 30) == pytest.approx(2.0)  # cap: 30 x 2.0 + 10 = 10 x 6.0 + 10
    assert plain_ratio(6.0, 10, 10) == 6.0 and plain_ratio(6.0, 0, 10) == 6.0


def test_synth_with_control_drops_the_prompt_and_caps_on_the_dub_text(worker: Any, tmp_path: Path) -> None:
    w, _torch, vox = worker
    w.op_load({"model": "m", "device": "cuda"})
    out = w.op_synth(req(tmp_path, "tag", text="Стой!", control="shouting"))
    assert out["ok"] and out["mode"] == "ref" and out["control"] == "shouting" and "prompt_dropped" not in out
    call = vox.CALLS[-1]
    assert call["text"] == "(shouting)Стой!" and "prompt_wav_path" not in call
    # 5 dub tokens of 15: the cap and the bad-case check count the dub text alone
    assert call["retry_badcase_ratio_threshold"] == pytest.approx(6.0 * 5 / 15)
    out = w.op_synth(req(tmp_path, "both", text="Стой!", control="calm", params={"retry_badcase_ratio_threshold": 3.0},
                         prompt_wav=wav(tmp_path, "ru_prompt"), prompt_text="Ну что."))
    assert out["ok"] and out["prompt_dropped"] is True and out["mode"] == "ref"
    call = vox.CALLS[-1]
    assert call["text"] == "(calm)Стой!" and "prompt_wav_path" not in call and "prompt_text" not in call
    assert call["retry_badcase_ratio_threshold"] == pytest.approx(3.0 * 5 / 11)
    w.op_synth(req(tmp_path, "plain", text="Стой!"))
    assert vox.CALLS[-1]["text"] == "Стой!" and "retry_badcase_ratio_threshold" not in vox.CALLS[-1]


def test_protocol_over_stdin_stdout(tmp_path: Path) -> None:
    fakes = tmp_path / "fakes"
    (fakes / "torch").mkdir(parents=True)
    (fakes / "torch" / "__init__.py").write_text(FAKE_TORCH, encoding="utf-8")
    (fakes / "voxcpm.py").write_text(FAKE_VOXCPM, encoding="utf-8")
    client = WorkerClient(Path(sys.executable), "tts_voxcpm.py", log_path=tmp_path / "w.log",
                          extra_env={"PYTHONPATH": str(fakes)})
    with client:
        loaded = client.load({"op": "load", "model": "m", "device": "cuda"})
        assert loaded["ok"] and loaded["device"] == "cuda:0"
        out = client.request(req(tmp_path, "p"))
        assert out["ok"] and Path(out["wav"]).is_file()
        assert client.request({"op": "ping"})["ok"]
        assert client.request({"op": "nope"})["ok"] is False
        assert client.request({"op": "stats"})["vram_peak_gb"] == 6.5
