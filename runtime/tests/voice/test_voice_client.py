"""WorkerClient against the dummy TTS worker (runs under the test interpreter) and the GPU lock."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from babeldub.audio.io import probe
from babeldub.core.output import EXIT_ENV, EXIT_USAGE, BabelDubError
from babeldub.workers.client import (
    GpuLock,
    WorkerClient,
    WorkerCrashed,
    WorkerTimeout,
    pid_alive,
    resolve_python,
)


def make_client(tmp_path: Path, **kw: object) -> WorkerClient:
    return WorkerClient(Path(sys.executable), "tts_dummy.py", log_path=tmp_path / "worker.log", **kw)  # type: ignore[arg-type]


def synth_req(tmp_path: Path, name: str, **params: object) -> dict[str, object]:
    return {"op": "synth", "id": name, "text": "Так, посмотрим, что тут у нас.", "lang": "ru",
            "ref_wav": "", "ref_text": "", "out": str(tmp_path / f"{name}.wav"), "seed": 7, "params": params}


def test_load_and_synth_roundtrip(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        reply = client.load({"model": "dummy", "device": "cpu"})
        assert reply["ok"] and reply["sr"] == 24000 and reply["info"]["test_only"] is True
        out = client.request(synth_req(tmp_path, "a"))
        assert out["ok"] and out["id"] == "a"
        info = probe(tmp_path / "a.wav")
        assert info["sr"] == 24000 and info["dur_s"] == pytest.approx(out["dur_s"], abs=1e-3)
        assert 1.0 < info["dur_s"] < 4.0  # 9 syllables at ~5 syll/s plus edge silence
        err = client.request(synth_req(tmp_path, "b", dummy_fail="error"))
        assert err["ok"] is False and "dummy failure" in err["error"]
        unknown = client.request({"op": "nope"})
        assert unknown["ok"] is False
    assert (tmp_path / "worker.log").exists()


def test_dummy_control_text_keeps_the_length_and_drops_a_prompt(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.wav"
    with make_client(tmp_path) as client:
        client.load({"model": "dummy"})
        plain = client.request(synth_req(tmp_path, "plain"))
        tagged = client.request({**synth_req(tmp_path, "tagged"), "control": "shouting, fast pace"})
        assert tagged["ok"] and tagged["control"] == "shouting, fast pace" and "prompt_dropped" not in tagged
        assert tagged["dur_s"] == plain["dur_s"]  # the control phrase is not spoken: same seed, same length
        prompt.write_bytes((tmp_path / "plain.wav").read_bytes())
        both = client.request({**synth_req(tmp_path, "both"), "control": "calm", "prompt_wav": str(prompt),
                               "prompt_text": "Ну что."})
        assert both["ok"] and both["prompt_dropped"] is True and both["mode"] != "ref+prompt"


def test_same_seed_is_deterministic(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.load({"model": "dummy"})
        client.request(synth_req(tmp_path, "x"))
        req = synth_req(tmp_path, "y")
        client.request(req)
    assert (tmp_path / "x.wav").read_bytes() == (tmp_path / "y.wav").read_bytes()


def test_dummy_simulates_runaway_up_to_the_token_cap(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.load({"model": "dummy"})
        loop = client.request({**synth_req(tmp_path, "loop", max_new_tokens=100), "text": "Ю, ю, RUNAWAY."})
        assert loop["ok"] and loop["hit_token_limit"] is True and loop["dur_s"] == pytest.approx(8.0, abs=1e-3)
        # a marker in the transcript loops only in ICL mode; timbre-only requests send no transcript
        icl = client.request({**synth_req(tmp_path, "icl", max_new_tokens=100), "ref_text": "RUNAWAY here"})
        assert icl["hit_token_limit"] is True and icl["mode"] == "icl"
        xvec = client.request({**synth_req(tmp_path, "xvec", max_new_tokens=100, x_vector_only=True), "ref_text": "RUNAWAY"})
        assert "hit_token_limit" not in xvec and xvec["mode"] == "xvec" and xvec["dur_s"] < 4.0
        stuck = client.request(synth_req(tmp_path, "stuck", max_new_tokens=100, dummy_runaway_xvec=True))
        assert stuck["hit_token_limit"] is True
        # normal speech longer than the cap is cut there, like a real engine
        cut = client.request(synth_req(tmp_path, "cut", max_new_tokens=20, syllables_per_s=1.0))
        assert cut["hit_token_limit"] is True and cut["dur_s"] == pytest.approx(1.6, abs=1e-3)


def test_crash_restarts_and_replays_load(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.load({"model": "dummy"})
        with pytest.raises(WorkerCrashed):
            client.request(synth_req(tmp_path, "c", dummy_fail="exit"), retry=False)
        assert not client.alive
        out = client.request(synth_req(tmp_path, "d"))  # restarted transparently, load replayed
        assert out["ok"] and client.restarts == 1


def test_crash_retry_gives_up_after_max_restarts(tmp_path: Path) -> None:
    with make_client(tmp_path, max_restarts=1) as client:
        client.load({"model": "dummy"})
        with pytest.raises(WorkerCrashed):
            client.request(synth_req(tmp_path, "e", dummy_fail="exit"))  # crash, restart, crash again


def test_timeout_kills_and_next_request_recovers(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.load({"model": "dummy"})
        with pytest.raises(WorkerTimeout):
            client.request(synth_req(tmp_path, "slow", dummy_sleep=5), timeout=1.0)
        assert not client.alive
        assert client.request(synth_req(tmp_path, "fast"))["ok"]


def test_missing_env_is_exit_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BABELDUB_HOME", str(tmp_path / "home"))
    with pytest.raises(BabelDubError) as err:
        resolve_python("qwen3tts")
    assert err.value.code == EXIT_ENV and "babeldub setup --engines qwen3tts" in (err.value.hint or "")


def test_gpu_lock_exclusive_and_stale(tmp_path: Path) -> None:
    path = tmp_path / "locks" / "gpu.lock"
    with GpuLock(path, owner="synth") as lock:
        assert lock.held and json.loads(path.read_text())["pid"] == os.getpid()
        with pytest.raises(BabelDubError) as err:
            GpuLock(path, owner="qa").acquire()
        assert err.value.code == EXIT_USAGE
    assert not path.exists()
    # a lock left by a process that no longer exists is taken over
    dead = 4_000_000
    assert not pid_alive(dead)
    path.write_text(json.dumps({"pid": dead, "owner": "crashed"}), encoding="utf-8")
    with GpuLock(path, owner="synth"):
        assert json.loads(path.read_text())["owner"] == "synth"
    assert not path.exists()
