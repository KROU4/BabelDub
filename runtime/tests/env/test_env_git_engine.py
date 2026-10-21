"""Git engines (Applio): pinned checkout, filtered requirements, post-install in the checkout; catalog defaults."""

import sys
from pathlib import Path

from babeldub.core.jsonl import read_json
from babeldub.envsetup import installer
from babeldub.envsetup.catalog import (
    ENV_MARKER,
    big_gpu,
    load_catalog,
    runnable_engine,
    setup_tts_engine,
)

EXE = ".exe" if sys.platform == "win32" else ""
COMMIT = "5171e1dcf31a33d1afeb14ef7c6aac6dadae5dc1"


def _fake_tools(monkeypatch, tmp_path, head=None):
    tools = {name: tmp_path / "bin" / (name + EXE) for name in ("uv", "uvx", "git")}
    monkeypatch.setattr(installer, "find_tool", lambda name: tools.get(name))
    monkeypatch.setattr(installer, "check_imports", lambda python, modules, timeout=180: (True, None))
    monkeypatch.setattr(installer, "git_head", lambda folder: head)
    monkeypatch.setattr(installer, "detect_gpu", lambda: {"available": True, "vram_gb": 8.0})
    return tools


def test_catalog_defaults_for_the_dubbing_chain():
    cat = load_catalog()
    vox, applio = cat.engine("voxcpm"), cat.engine("applio")
    assert vox.cuda_only and vox.fallback == "qwen3tts-fast" and vox.supports("pl") and not vox.supports("uk")
    assert applio.role == "vc" and applio.git["commit"] == COMMIT and applio.requirements == "requirements-engine.txt"
    assert cat.model("VoxCPM2").license == "Apache-2.0" and cat.model("VoxCPM2").vram_gb == 6.5
    assert runnable_engine("voxcpm", "cpu", cat) == "qwen3tts"  # through qwen3tts-fast, also CUDA only
    assert setup_tts_engine("auto", "ru", "8gb", cat, 8.0) == "voxcpm"
    assert setup_tts_engine("auto", "ru", "8gb", cat, 6.0) == "qwen3tts-fast"
    assert setup_tts_engine("auto", "pl", "cpu", cat) == "chatterbox"
    assert setup_tts_engine("qwen3tts", "ru", "12gb", cat) == "qwen3tts"  # an explicit engine stays
    assert setup_tts_engine("voxcpm", "pl", "cpu", cat) == "chatterbox"  # its CPU fallback cannot speak Polish
    assert big_gpu("8gb") and big_gpu("12gb", 12.0) and not big_gpu("8gb", 6.0) and not big_gpu("cpu")


def test_git_engine_commands(home, monkeypatch, tmp_path):
    tools = _fake_tools(monkeypatch, tmp_path)
    applio = load_catalog().engine("applio")
    item = installer.plan_engine(applio, "8gb")
    assert item.action == "install" and item.license == "MIT"
    cmds = item.commands
    source = home / "engines" / "applio"
    assert cmds[0] == [str(tools["git"]), "init", "--quiet", str(source)]
    assert cmds[1] == [str(tools["git"]), "-C", str(source), "fetch", "--depth", "1",
                       "https://github.com/IAHispano/Applio", COMMIT]
    assert cmds[2][-2:] == ["--detach", "FETCH_HEAD"]
    assert cmds[4][5:7] == ["torch==2.11.0", "torchaudio==2.11.0"] and cmds[4][-1].endswith("/cu128")
    assert cmds[5] == ["@filter-requirements", str(source / "requirements-engine.txt"), str(source / "req-babeldub.txt")]
    assert cmds[6][-2:] == ["-r", str(source / "req-babeldub.txt")]
    assert cmds[-1][:2] == ["@cd", str(source)] and cmds[-1][3:] == ["core.py", "prerequisites", "--pretraineds-hifigan",
                                                                    "--models", "--no-exe"]


def test_git_engine_adopted_only_at_the_pinned_commit(home, monkeypatch, tmp_path):
    applio = load_catalog().engine("applio")
    python = applio.python_path()
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    _fake_tools(monkeypatch, tmp_path, head="0" * 40)
    item = installer.plan_engine(applio, "8gb")
    assert item.action == "install" and "pinned commit" in item.reason
    _fake_tools(monkeypatch, tmp_path, head=COMMIT)
    item = installer.plan_engine(applio, "8gb")
    assert item.action == "adopt"
    installer.run_engine(item, applio, "8gb")
    marker = read_json(applio.env_dir() / ENV_MARKER)
    assert marker["adopted"] and marker["spec"]["git"]["commit"] == COMMIT
    assert installer.plan_engine(applio, "8gb").action == "skip"


def test_run_steps_filter_requirements_and_cd(home, monkeypatch, tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "req.txt").write_text("click==8.1.8\ntorch==2.11.0\ntorchaudio==2.11.0\ntorchcrepe==0.0.24\n"
                                    "torch>=2 ; sys_platform == 'win32'\nnumpy\n", encoding="utf-8")
    log = tmp_path / "setup.log"
    assert installer.run_step(["@filter-requirements", str(source / "req.txt"), str(source / "out.txt")], log, 10) == 0
    assert (source / "out.txt").read_text(encoding="utf-8").split() == ["click==8.1.8", "torchcrepe==0.0.24", "numpy"]
    assert installer.run_step(["@filter-requirements", str(source / "nope.txt"), str(source / "x.txt")], log, 10) == -1
    seen = {}

    def fake_run(cmd, log_path, timeout, cwd=None):
        seen.update(cmd=cmd, cwd=cwd)
        return 0

    monkeypatch.setattr(installer, "run_logged", fake_run)
    assert installer.run_step(["@cd", str(source), "python", "core.py", "prerequisites"], log, 10) == 0
    assert seen == {"cmd": ["python", "core.py", "prerequisites"], "cwd": Path(source)}
