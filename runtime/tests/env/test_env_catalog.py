import os

import pytest
import yaml

from babeldub.core.output import BabelDubError
from babeldub.envsetup import catalog as cat_mod
from babeldub.envsetup.catalog import (
    MODEL_MARKER,
    TIERS,
    auto_models,
    default_model,
    engine_for_language,
    is_model_downloaded,
    load_catalog,
    model_dir,
    model_for_role,
    parse_catalog,
    tier_for_vram,
)


def test_shipped_catalog_engines():
    cat = load_catalog()
    assert set(cat.engines) >= {"qwen3tts", "chatterbox", "qc", "dummy"}
    qwen = cat.engine("qwen3tts")
    assert qwen.torch["version"] == "2.8.0" and qwen.torch["index"].endswith("/cu128")
    assert "qwen-tts" in qwen.packages and qwen.worker == "tts_qwen3.py"
    assert qwen.supports("ru") and not qwen.supports("pl")
    chatter = cat.engine("chatterbox")
    assert chatter.torch["version"] == "2.6.0" and chatter.torch["index"].endswith("/cu124")
    assert any(p.startswith("chatterbox-tts @ git+https://github.com/resemble-ai/chatterbox") for p in chatter.packages)
    assert chatter.needs_git() and chatter.supports("pl") and chatter.supports("tr")
    qc = cat.engine("qc")
    assert {"faster-whisper", "speechbrain", "soundfile", "numpy"} <= set(qc.packages)
    assert {"nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"} <= set(qc.gpu_packages)
    dummy = cat.engine("dummy")
    assert dummy.env is None and dummy.python_path() is None and dummy.supports("xx")
    with pytest.raises(BabelDubError) as err:
        cat.engine("nope")
    assert err.value.code == 2


def test_shipped_catalog_models_are_complete_and_commercial_by_default():
    cat = load_catalog()
    names = {m.name for m in cat.models}
    assert {
        "Qwen3-TTS-12Hz-1.7B-Base",
        "Qwen3-TTS-12Hz-0.6B-Base",
        "qwen3-tts-12hz-1.7b-ru-stress-cf",
        "faster-whisper-large-v3-turbo",
        "spkrec-ecapa-voxceleb",
        "chatterbox-multilingual",
    } <= names
    for m in cat.models:
        assert m.engine in cat.engines
        assert m.license and m.size_gb > 0 and m.tiers
        assert set(m.tiers) <= set(TIERS) and set(m.auto_tiers) <= set(m.tiers)
        if not m.commercial:
            assert m.opt_in and not m.auto_tiers
    stress = cat.model("qwen3-tts-12hz-1.7b-ru-stress-cf")
    assert stress.repo == "siriusfreak/qwen3-tts-12hz-1.7b-ru-stress-cf" and stress.warning
    assert stress.language_specific and stress.languages == ["ru"]
    chatter = cat.model("chatterbox-multilingual")
    assert "t3_mtl23ls_v3.safetensors" in chatter.include and "watermark" in chatter.warning.lower()
    assert cat.model("dropbox-dash/faster-whisper-large-v3-turbo").name == "faster-whisper-large-v3-turbo"
    assert cat.model("Qwen3-TTS-1.7B-ru-stress-cf").name == stress.name  # alias of the lab folder


def test_auto_models_by_tier_and_language():
    cat = load_catalog()
    ru = [m.name for m in auto_models(cat, ["qwen3tts", "qc"], "8gb", "ru")]
    # the ru stress fine-tune keeps the original actor's voice worse: never installed by default
    assert ru == [
        "Qwen3-TTS-12Hz-1.7B-Base",
        "faster-whisper-large-v3-turbo",
        "spkrec-ecapa-voxceleb",
    ]
    en = [m.name for m in auto_models(cat, ["qwen3tts", "qc"], "8gb", "en")]
    assert "qwen3-tts-12hz-1.7b-ru-stress-cf" not in en
    nolang = [m.name for m in auto_models(cat, ["qwen3tts"], "8gb", None)]
    assert nolang == ["Qwen3-TTS-12Hz-1.7B-Base"]
    cpu = [m.name for m in auto_models(cat, ["qwen3tts", "qc"], "cpu", "ru")]
    assert cpu == ["Qwen3-TTS-12Hz-0.6B-Base", "faster-whisper-large-v3-turbo", "spkrec-ecapa-voxceleb"]
    pl = [m.name for m in auto_models(cat, ["chatterbox", "qc"], "12gb", "pl")]
    assert set(pl) == {"chatterbox-multilingual", "faster-whisper-large-v3-turbo", "spkrec-ecapa-voxceleb"}


def test_default_model_prefers_general_base():
    cat = load_catalog()
    assert default_model("qwen3tts", "8gb", "ru", cat).name == "Qwen3-TTS-12Hz-1.7B-Base"
    assert default_model("qwen3tts", "8gb", "de", cat).name == "Qwen3-TTS-12Hz-1.7B-Base"
    assert default_model("qwen3tts", "cpu", "ru", cat).name == "Qwen3-TTS-12Hz-0.6B-Base"
    assert default_model("qwen3tts", "8gb", "pl", cat) is None
    assert model_for_role("asr", "8gb", "ru", cat).name == "faster-whisper-large-v3-turbo"
    assert model_for_role("sv", "cpu", None, cat).name == "spkrec-ecapa-voxceleb"


def test_engine_for_language_and_tiers():
    cat = load_catalog()
    assert engine_for_language("ru", cat) == "qwen3tts"
    assert engine_for_language("pl", cat) == "chatterbox"
    assert engine_for_language("uk", cat) is None
    assert tier_for_vram(None) == "cpu"
    assert tier_for_vram(4.0) == "cpu"
    assert tier_for_vram(8.0) == "8gb"
    assert tier_for_vram(12.0) == "12gb"
    assert tier_for_vram(24.0) == "24gb"


def _write(tmp_path, data):
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_non_commercial_models_are_forced_opt_in(tmp_path):
    data = {
        "engines": {"e": {"env": "e", "languages": ["*"]}},
        "models": [{"name": "nc", "repo": "x/nc", "engine": "e", "commercial": False, "tiers": ["8gb"],
                    "auto_tiers": ["8gb"]}],
    }
    cat = load_catalog(_write(tmp_path, data))
    nc = cat.model("nc")
    assert nc.opt_in
    assert auto_models(cat, ["e"], "8gb", "ru") == []
    assert [m.name for m in auto_models(cat, ["e"], "8gb", "ru", accepted={"nc"})] == ["nc"]


@pytest.mark.parametrize(
    "models",
    [
        [{"name": "a", "repo": "x/a", "engine": "missing"}],
        [{"name": "a", "repo": "x/a", "engine": "e", "tiers": ["4gb"]}],
        [{"name": "a", "engine": "e"}],
        [{"name": "a", "repo": "x/a", "engine": "e"}, {"name": "a", "repo": "x/b", "engine": "e"}],
    ],
)
def test_invalid_catalogs_are_rejected(models):
    with pytest.raises(BabelDubError):
        parse_catalog({"engines": {"e": {"env": "e"}}, "models": models})


def test_model_dir_alias_and_download_detection(home):
    cat = load_catalog()
    stress = cat.model("qwen3-tts-12hz-1.7b-ru-stress-cf")
    assert model_dir(stress) == home / "models" / stress.name
    alias = home / "models" / "Qwen3-TTS-1.7B-ru-stress-cf"
    alias.mkdir(parents=True)
    assert model_dir(stress) == alias
    assert not is_model_downloaded(stress)
    (alias / "model.safetensors").write_bytes(b"\0" * 16)
    assert is_model_downloaded(stress)
    incomplete = alias / ".cache" / "huggingface" / "download"
    incomplete.mkdir(parents=True)
    (incomplete / "x.incomplete").write_bytes(b"")
    assert not is_model_downloaded(stress)
    (alias / MODEL_MARKER).write_text("{}", encoding="utf-8")
    assert is_model_downloaded(stress)
    assert model_dir("qwen3-tts-12hz-1.7b-ru-stress-cf", cat) == alias


def test_catalog_cache_reloads_after_edit(tmp_path):
    data = {"engines": {"e": {"env": "e"}}, "models": []}
    path = _write(tmp_path, data)
    first = cat_mod.load_catalog(path)
    data["engines"]["f"] = {"env": "f"}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    later = path.stat().st_mtime + 5
    os.utime(path, (later, later))
    assert "f" not in first.engines
    assert "f" in cat_mod.load_catalog(path).engines
