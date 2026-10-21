from pathlib import Path

import pytest

from babeldub.commands import env as env_cmd
from babeldub.envsetup import installer
from babeldub.envsetup.catalog import load_catalog, model_dir

GPU = {"name": "NVIDIA GeForce RTX 3060 Ti", "vram_gb": 8.0, "used_gb": 1.5, "driver": "617.14"}


def _tools(missing=()):
    names = ["uv", "ffmpeg", "ffprobe", "uvx", "vgmstream-cli", "rubberband", "git"]
    return {n: ({"found": False} if n in missing else {"found": True, "path": f"/bin/{n}", "version": "1.0"}) for n in names}


@pytest.fixture
def machine(monkeypatch):
    """A fake machine: tool/GPU/venv probes return what the test sets."""
    state = {"tools": _tools(), "gpus": [GPU], "envs": {}}
    monkeypatch.setattr(env_cmd, "tools_report", lambda: state["tools"])
    monkeypatch.setattr(env_cmd, "gpu_report", lambda: state["gpus"])

    def envs(engines, import_check=True):
        return {e.name: state["envs"].get(e.name, {"status": "missing"}) for e in engines}

    monkeypatch.setattr(env_cmd, "envs_report", envs)
    monkeypatch.setattr(installer, "check_imports", lambda python, modules, timeout=180: (True, None))
    monkeypatch.setattr(installer, "find_tool", lambda name: Path(f"/bin/{name}") if name in ("uv", "uvx", "git") else None)
    return state


def test_doctor_missing_required_tool_is_exit_4(bd, machine, home):
    machine["tools"] = _tools(missing=("ffmpeg", "ffprobe"))
    code, out = bd("doctor")
    assert code == 4 and not out["ok"]
    assert out["missing"] == ["ffmpeg", "ffprobe"]
    assert "ffmpeg" in out["install"] and out["tier"] == "8gb"
    assert Path(out["report"]).is_file()


def test_doctor_without_envs_asks_for_setup(bd, machine):
    code, out = bd("doctor")
    assert code == 3 and out["ok"]
    assert out["gpu"]["name"] == GPU["name"] and out["tier"] == "8gb"
    # with a GPU the default TTS engine is the CUDA-graph runtime of the same Qwen3-TTS models
    assert {"env:qwen3tts-fast", "env:qc", "model:Qwen3-TTS-12Hz-1.7B-Base"} <= set(out["to_install"])
    assert "env:qwen3tts" not in out["to_install"]
    assert out["next"].startswith("babeldub setup")


def test_doctor_all_ready(bd, machine, home):
    machine["envs"] = {"qwen3tts-fast": {"status": "ok"}, "qc": {"status": "ok"}}
    cat = load_catalog()
    for name in ("Qwen3-TTS-12Hz-1.7B-Base", "faster-whisper-large-v3-turbo", "spkrec-ecapa-voxceleb"):
        folder = model_dir(cat.model(name))
        folder.mkdir(parents=True)
        (folder / "model.safetensors").write_bytes(b"x")
    code, out = bd("doctor")
    assert code == 0, out
    assert out["to_install"] == [] and out["next"].startswith("babeldub init")


def test_doctor_cpu_without_gpu(bd, machine):
    machine["gpus"] = []
    code, out = bd("doctor")
    assert out["tier"] == "cpu" and out["gpu"] is None
    assert "model:Qwen3-TTS-12Hz-0.6B-Base" in out["to_install"]
    assert "env:qwen3tts" in out["to_install"] and "env:qwen3tts-fast" not in out["to_install"]  # CUDA only
    assert any("no NVIDIA GPU" in w for w in out["warnings"])


def test_setup_dry_run_plan(bd, machine, home):
    code, out = bd("setup", "--dry-run")
    assert code == 0 and out["dry_run"]
    planned = {(i["kind"], i["name"]): i for i in out["planned"]}
    assert ("engine", "qwen3tts-fast") in planned and ("engine", "qc") in planned
    assert ("engine", "qwen3tts") not in planned
    assert ("model", "Qwen3-TTS-12Hz-1.7B-Base") in planned
    assert ("model", "qwen3-tts-12hz-1.7b-ru-stress-cf") not in planned  # language unknown without a project
    assert planned[("model", "Qwen3-TTS-12Hz-1.7B-Base")]["license"] == "Apache-2.0"
    assert ("tool", "vgmstream-cli") in planned
    assert out["download_gb"] > 5
    assert not (home / "envs" / "qwen3tts").exists()
    assert Path(out["plan"]).is_file()


def test_setup_dry_run_uses_project_language(bd, machine, tmp_path):
    game = tmp_path / "Game"
    game.mkdir()
    assert bd("init", game, "--lang", "ru")[0] == 0
    code, out = bd("setup", "--dry-run")
    names = [i["name"] for i in out["planned"]]
    assert out["lang"] == "ru" and "Qwen3-TTS-12Hz-1.7B-Base" in names
    assert "qwen3-tts-12hz-1.7b-ru-stress-cf" not in names  # opt in explicitly (keeps the voice worse)
    code, out = bd("setup", "--dry-run", "--models", "qwen3-tts-12hz-1.7b-ru-stress-cf")
    warn = next(i for i in out["planned"] if i["name"] == "qwen3-tts-12hz-1.7b-ru-stress-cf")
    assert "license" in warn["warning"]


def test_setup_polish_project_picks_chatterbox(bd, machine, tmp_path):
    game = tmp_path / "Game"
    game.mkdir()
    bd("init", game, "--lang", "pl")
    code, out = bd("setup", "--dry-run", "--tier", "12gb")
    names = [i["name"] for i in out["planned"]]
    assert "chatterbox" in names and "chatterbox-multilingual" in names and "qwen3tts" not in names


def test_setup_never_installs_the_cuda_only_engine_on_the_cpu_tier(bd, machine):
    code, out = bd("setup", "--dry-run", "--tier", "cpu")
    engines = {i["name"]: i for i in out["planned"] if i["kind"] == "engine"}
    assert set(engines) == {"qwen3tts", "qc"} and "Qwen3-TTS-12Hz-0.6B-Base" in [i["name"] for i in out["planned"]]
    code, out = bd("setup", "--dry-run", "--tier", "cpu", "--engines", "qwen3tts-fast", "--models", "none")
    (item,) = out["planned"]
    assert code == 3 and item["action"] == "blocked" and "CUDA only" in item["reason"]
    assert out["blocked"] == ["qwen3tts-fast"]


def test_setup_only_tools(bd, machine, monkeypatch):
    calls = []
    monkeypatch.setattr(installer, "install_vgmstream",
                        lambda force=False: calls.append(force) or {"name": "vgmstream-cli", "kind": "tool",
                                                                    "status": "installed", "version": "r1"})
    code, out = bd("setup", "--tools")
    assert code == 0 and out["done"] == ["vgmstream-cli"] and calls == [False]
    assert out["planned"] == ["vgmstream-cli"]


def test_setup_runs_engines_and_reports_failures(bd, machine, monkeypatch):
    def fake_run(cmd, log_path, timeout):
        return 1 if any("torch==" in c for c in cmd) else 0

    monkeypatch.setattr(installer, "run_logged", fake_run)
    code, out = bd("setup", "--engines", "qc", "--models", "none", "--tier", "8gb")
    assert code == 1 and out["failed"][0]["name"] == "qc"
    assert out["next"].startswith("read the log")


def test_setup_rejects_unknown_engine(bd, machine):
    assert bd("setup", "--engines", "nope", "--dry-run")[0] == 2


def test_setup_opt_in_blocked_in_plan(bd, machine, monkeypatch, tmp_path):
    from babeldub.envsetup import catalog as cat_mod

    yaml_text = (
        "engines:\n  e: {env: e, languages: ['*']}\n"
        "models:\n  - {name: NC, repo: x/nc, engine: e, license: CC-BY-NC-4.0, commercial: false, tiers: [8gb],"
        " size_gb: 1}\n"
    )
    path = tmp_path / "models.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(cat_mod, "catalog_path", lambda: path)
    code, out = bd("setup", "--models", "NC", "--dry-run")
    assert code == 3 and out["blocked"] == ["NC"]
    code, out = bd("setup", "--models", "NC", "--dry-run", "--accept-license", "NC")
    assert code == 0 and out["planned"][0]["action"] == "install"


def test_models_list_and_fetch(bd, machine, monkeypatch, home):
    code, out = bd("models", "list")
    assert code == 0
    rows = {m["name"]: m for m in out["models"]}
    assert rows["Qwen3-TTS-12Hz-1.7B-Base"]["downloaded"] is False
    assert rows["chatterbox-multilingual"]["commercial"] is True
    code, out = bd("models", "list", "--engine", "qc")
    assert {m["engine"] for m in out["models"]} == {"qc"}

    code, out = bd("models", "fetch", "spkrec-ecapa-voxceleb", "--dry-run")
    assert code == 0 and out["planned"][0]["action"] == "install"
    assert bd("models", "fetch", "no-such-model")[0] == 2

    def fake_run(cmd, log_path, timeout):
        dest = Path(cmd[cmd.index("--local-dir") + 1])
        (dest / "embedding_model.ckpt").write_bytes(b"1")
        return 0

    monkeypatch.setattr(installer, "run_logged", fake_run)
    code, out = bd("models", "fetch", "spkrec-ecapa-voxceleb")
    assert code == 0 and out["done"] == ["spkrec-ecapa-voxceleb"]
    code, out = bd("models", "fetch", "spkrec-ecapa-voxceleb")
    assert out["skipped"] == ["spkrec-ecapa-voxceleb"] and out["done"] == []
