import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from babeldub.core.jsonl import read_json
from babeldub.core.output import BabelDubError
from babeldub.envsetup import installer
from babeldub.envsetup.catalog import ENV_MARKER, MODEL_MARKER, load_catalog, model_dir
from babeldub.envsetup.probe import parse_nvidia_smi, parse_version

EXE = ".exe" if sys.platform == "win32" else ""


@pytest.fixture
def fake_tools(monkeypatch, tmp_path):
    """uv/uvx/git resolve to fake paths; imports inside venvs succeed."""
    tools = {name: tmp_path / "bin" / (name + EXE) for name in ("uv", "uvx", "git")}

    def find(name):
        return tools.get(name)

    monkeypatch.setattr(installer, "find_tool", find)
    monkeypatch.setattr(installer, "check_imports", lambda python, modules, timeout=180: (True, None))
    return tools


def _fake_python(engine):
    python = engine.python_path()
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"")
    return python


def _no_gpu(monkeypatch):
    monkeypatch.setattr(installer, "detect_gpu", lambda: {"available": False})


def test_gpu_present_always_gets_cuda_wheels(home, fake_tools, monkeypatch):
    # GPU-first: even a small GPU (cpu model tier) runs ASR/embeddings/TTS on CUDA
    monkeypatch.setattr(installer, "detect_gpu", lambda: {"available": True, "vram_gb": 4.0})
    qc = load_catalog().engine("qc")
    cmds = installer.plan_engine(qc, "cpu").commands
    assert cmds[1][-1] == "https://download.pytorch.org/whl/cu128"
    assert installer.accel_for_tier("cpu") == "cuda"


def test_engine_commands_cuda_and_cpu(home, fake_tools, monkeypatch):
    _no_gpu(monkeypatch)
    cat = load_catalog()
    qc = cat.engine("qc")
    item = installer.plan_engine(qc, "8gb")
    assert item.action == "install" and item.size_gb
    cmds = item.commands
    assert cmds[0][1:3] == ["venv", str(home / "envs" / "qc")] and "--allow-existing" in cmds[0]
    assert "torch==2.8.0" in cmds[1] and cmds[1][-1] == "https://download.pytorch.org/whl/cu128"
    assert "faster-whisper" in cmds[2]
    assert cmds[3][-2:] == ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]
    cpu = installer.plan_engine(qc, "cpu").commands
    assert cpu[1][-1] == "https://download.pytorch.org/whl/cpu" and len(cpu) == 3


def test_engine_skip_adopt_and_spec_change(home, fake_tools, monkeypatch):
    _no_gpu(monkeypatch)
    cat = load_catalog()
    qwen = cat.engine("qwen3tts")
    _fake_python(qwen)
    adopt = installer.plan_engine(qwen, "8gb")
    assert adopt.action == "adopt"
    result = installer.run_engine(adopt, qwen, "8gb")
    assert result["status"] == "adopted"
    marker = read_json(qwen.env_dir() / ENV_MARKER)
    assert marker["adopted"] and marker["spec"]["accel"] == "cuda"
    assert installer.plan_engine(qwen, "8gb").action == "skip"
    changed = installer.plan_engine(qwen, "cpu")  # other torch index -> reinstall
    assert changed.action == "install" and "spec changed" in changed.reason
    assert installer.plan_engine(qwen, "8gb", force=True).action == "install"


def test_engine_blocked_without_uv_or_git(home, monkeypatch):
    cat = load_catalog()
    monkeypatch.setattr(installer, "check_imports", lambda *a, **k: (False, "missing"))
    monkeypatch.setattr(installer, "find_tool", lambda name: None)
    item = installer.plan_engine(cat.engine("qwen3tts"), "8gb")
    assert item.action == "blocked" and "uv" in item.reason
    monkeypatch.setattr(installer, "find_tool", lambda name: Path("uv") if name == "uv" else None)
    item = installer.plan_engine(cat.engine("chatterbox"), "8gb")
    assert item.action == "blocked" and "git" in item.reason
    assert installer.plan_engine(cat.engine("dummy"), "8gb").action == "skip"


def test_run_engine_failure_reports_log(home, fake_tools, monkeypatch):
    cat = load_catalog()
    qc = cat.engine("qc")
    item = installer.plan_engine(qc, "8gb")
    calls = []

    def fake_run(cmd, log_path, timeout):
        calls.append(cmd)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("resolving...\nerror: no matching torch\n", encoding="utf-8")
        return 1 if "torch==2.8.0" in cmd else 0

    monkeypatch.setattr(installer, "run_logged", fake_run)
    result = installer.run_engine(item, qc, "8gb")
    assert result["status"] == "failed" and result["tail"][-1] == "error: no matching torch"
    assert len(calls) == 2 and not (qc.env_dir() / ENV_MARKER).exists()


def test_run_engine_success_writes_marker(home, fake_tools, monkeypatch):
    cat = load_catalog()
    qwen = cat.engine("qwen3tts")
    item = installer.plan_engine(qwen, "8gb")

    def fake_run(cmd, log_path, timeout):
        if cmd[1] == "venv":
            _fake_python(qwen)
        return 0

    monkeypatch.setattr(installer, "run_logged", fake_run)
    assert installer.run_engine(item, qwen, "8gb")["status"] == "installed"
    assert read_json(qwen.env_dir() / ENV_MARKER)["spec"]["torch"]["version"] == "2.8.0"


def test_plan_model_licenses_and_commands(home, fake_tools):
    cat = load_catalog()
    chatter = cat.model("chatterbox-multilingual")
    item = installer.plan_model(chatter)
    cmd = item.commands[0]
    assert cmd[1:6] == ["--from", "huggingface_hub", "hf", "download", "ResembleAI/chatterbox"]
    assert "t3_mtl23ls_v3.safetensors" in cmd and cmd[-2] == "--local-dir"
    assert item.warning and item.license == "MIT"
    whisper = cat.model("faster-whisper-large-v3-turbo")
    folder = model_dir(whisper)
    folder.mkdir(parents=True)
    (folder / "model.bin").write_bytes(b"x")
    assert installer.plan_model(whisper).action == "skip"


def test_opt_in_model_needs_consent(home, fake_tools, tmp_path):
    from babeldub.envsetup.catalog import parse_catalog

    cat = parse_catalog(
        {"engines": {"e": {"env": "e"}},
         "models": [{"name": "NC", "repo": "x/nc", "engine": "e", "commercial": False, "license": "CC-BY-NC-4.0",
                     "tiers": ["8gb"]}]}
    )
    blocked = installer.plan_model(cat.model("NC"))
    assert blocked.action == "blocked" and "--accept-license NC" in blocked.reason
    assert installer.plan_model(cat.model("NC"), accepted={"NC"}).action == "install"


def test_run_model_writes_marker(home, fake_tools, monkeypatch):
    cat = load_catalog()
    spec = cat.model("spkrec-ecapa-voxceleb")
    item = installer.plan_model(spec)

    def fake_run(cmd, log_path, timeout):
        dest = Path(cmd[cmd.index("--local-dir") + 1])
        (dest / "embedding_model.ckpt").write_bytes(b"\1" * 32)
        return 0

    monkeypatch.setattr(installer, "run_logged", fake_run)
    result = installer.run_model(item, spec)
    assert result["status"] == "installed"
    marker = read_json(model_dir(spec) / MODEL_MARKER)
    assert marker["repo"] == "speechbrain/spkrec-ecapa-voxceleb" and marker["size_bytes"] == 32


def test_run_model_without_weights_fails(home, fake_tools, monkeypatch):
    cat = load_catalog()
    spec = cat.model("spkrec-ecapa-voxceleb")
    item = installer.plan_model(spec)
    monkeypatch.setattr(installer, "run_logged", lambda cmd, log_path, timeout: 0)
    assert installer.run_model(item, spec)["status"] == "failed"


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _release(asset_name):
    return {
        "tag_name": "r9999",
        "published_at": "2026-05-19T00:00:00Z",
        "assets": [
            {"name": "vgmstream-mac.zip", "browser_download_url": "https://example.invalid/mac.zip"},
            {"name": asset_name, "browser_download_url": f"https://example.invalid/{asset_name}"},
        ],
    }


def test_pick_asset_per_platform():
    assert installer.pick_asset(_release("vgmstream-win64.zip"), "win32")["name"] == "vgmstream-win64.zip"
    assert installer.pick_asset(_release("vgmstream-linux.zip"), "linux")["name"] == "vgmstream-linux.zip"
    with pytest.raises(BabelDubError):
        installer.pick_asset(_release("vgmstream-win64.zip"), "linux")
    with pytest.raises(BabelDubError):
        installer.pick_asset(_release("vgmstream-win64.zip"), "darwin")


def test_install_vgmstream_records_version_and_sha(home, monkeypatch):
    key = "win32" if sys.platform == "win32" else "linux"
    asset = installer.VGMSTREAM_ASSETS[key][0 if key == "win32" else 1]
    payload = _zip_bytes({f"vgmstream-cli{EXE}": b"MZ fake", "libvorbis.dll": b"dll"})
    monkeypatch.setattr(installer, "_http_get_json", lambda url, timeout=30: _release(asset))

    def fake_download(url, dest, timeout=300):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(installer, "_download", fake_download)
    result = installer.install_vgmstream()
    assert result["status"] == "installed" and result["version"] == "r9999"
    info = json.loads((home / "tools" / "vgmstream" / "INSTALLED.json").read_text(encoding="utf-8"))
    assert info["sha256"] == hashlib.sha256(payload).hexdigest() and info["asset"] == asset
    assert (home / "tools" / "vgmstream" / info["exe"]).is_file()
    assert installer.plan_vgmstream().action == "skip"
    assert installer.install_vgmstream()["status"] == "skipped"


def test_safe_extract_rejects_traversal(tmp_path):
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(_zip_bytes({"../evil.txt": b"x"}))
    with pytest.raises(BabelDubError):
        installer.safe_extract(bad_zip, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()
    bad_tar = tmp_path / "bad.tar.gz"
    with tarfile.open(bad_tar, "w:gz") as tf:
        info = tarfile.TarInfo("../evil2.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(BabelDubError):
        installer.safe_extract(bad_tar, tmp_path / "out2")
    good_tar = tmp_path / "good.tar.gz"
    with tarfile.open(good_tar, "w:gz") as tf:
        info = tarfile.TarInfo("vgmstream-cli")
        info.size = 2
        tf.addfile(info, io.BytesIO(b"ok"))
    installer.safe_extract(good_tar, tmp_path / "out3")
    assert (tmp_path / "out3" / "vgmstream-cli").read_bytes() == b"ok"


def test_run_logged_captures_output(tmp_path):
    log = tmp_path / "logs" / "x.log"
    code = installer.run_logged([sys.executable, "-c", "print('hello from child')"], log, timeout=60)
    assert code == 0 and "hello from child" in log.read_text(encoding="utf-8")
    assert installer.run_logged([str(tmp_path / "missing-binary")], log, timeout=5) == -1


def test_plan_file(home):
    items = [installer.PlanItem(kind="tool", name="vgmstream-cli", action="install", commands=[["a", "b c"]])]
    path = installer.write_plan_file(items, {"tier": "8gb"})
    data = read_json(path)
    assert data["tier"] == "8gb" and data["items"][0]["commands"] == ['a "b c"']


def test_probe_parsers():
    gpus = parse_nvidia_smi("NVIDIA GeForce RTX 3060 Ti, 8192, 1519, 617.14\n")
    assert gpus == [{"name": "NVIDIA GeForce RTX 3060 Ti", "vram_gb": 8.0, "used_gb": 1.5, "driver": "617.14"}]
    assert parse_nvidia_smi("RTX 4090, 24564 MiB, 100 MiB, 600.1")[0]["vram_gb"] == 24.0
    assert parse_nvidia_smi("garbage") == []
    assert parse_version("vgmstream-cli", '{"version":"r2117","extensions":{}}') == "r2117"
    assert parse_version("ffmpeg", "ffmpeg version 9.0-full_build Copyright") == "9.0-full_build"
    assert parse_version("uv", "uv 0.12.5 (210d1f678 2026-08-14)") == "0.12.5"
    assert parse_version("git", "git version 2.55.0.windows.3") == "2.55.0.windows.3"
    assert parse_version("rubberband", "") is None


def test_direct_url_model_checks_sha256(home, fake_tools, monkeypatch):
    cat = load_catalog()
    spec = cat.model("utmos22-strong")
    item = installer.plan_model(spec)
    assert item.action == "install" and item.reason == "direct download" and not item.commands

    def fake_download(url, dest, timeout=300):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\1" * 16)
        return spec.sha256

    monkeypatch.setattr(installer, "_download", fake_download)
    result = installer.run_model(item, spec)
    assert result["status"] == "installed"
    assert (model_dir(spec) / "utmos22_strong_step7459_v1.pt").exists()
    assert installer.plan_model(spec).action == "skip"
    monkeypatch.setattr(installer, "_download", lambda url, dest, timeout=300: (dest.write_bytes(b"x"), "bad")[1])
    failed = installer.run_model(installer.plan_model(spec, force=True), spec)
    assert failed["status"] == "failed" and "sha256" in failed["error"]
