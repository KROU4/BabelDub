"""Whole pipeline through the real CLI on a synthetic game: no GPU, no game assets.

The game is a Unity folder with an FMOD Studio dialogue bank (built from synthetic WAVs by the
FMOD adapter's own FSB5 writer) and Unity Localization string tables (en + official ru for most
lines). Every stage runs through `babeldub.cli.main` exactly as an agent calls it:

  init -> set -> scan -> extract -> link -> cast (+ apply-labels) -> translate plan (auto -> adapt)
  -> glossary apply -> batch make/next/submit (the agent's .out is written here) -> glossary check
  -> synth (dummy TTS engine, ref_strategy=line, best of 2) -> post -> vc (no RVC model: pass through)
  -> qa (EXIT_ENV without the qc env, then a fake qc worker) -> decide apply -> report -> build -> verify
  -> install (into a COPY of the game) -> reapply -> uninstall (the copy is byte-identical again).

Only two things are faked: the content probe of the Unity bundles (real UnityFS bundles would need
Unity to author) and the qc worker process (faster-whisper + ECAPA live in a torch venv).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from babeldub import cli
from babeldub.adapters.fmod import fsb5 as F
from babeldub.adapters.unity import adapter as ul
from babeldub.adapters.unity.adapter import SHARED_TABLE, STRING_TABLE, BundleProbe, TableObject
from babeldub.commands import voice
from babeldub.core import layout
from babeldub.core.jsonl import latest_by, read_json, read_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_ENV, EXIT_OK, EXIT_USAGE

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fmod"))
from fmod_synth import SubSpec, make_bank, make_fsb, make_strings_bank  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
SA = "Synth_Data/StreamingAssets"
BANK = f"{SA}/Dialogue_en-US.bank"
AA = f"{SA}/aa/StandaloneWindows64"
BUNDLES = {
    "shared": "localization-assets-shared_assets_all.bundle",
    "en": "localization-string-tables-english(en)_assets_all.bundle",
    "ru": "localization-string-tables-russian(ru)_assets_all.bundle",
}

# key, FMOD event path, seconds, sample rate, channels, f0, English subtitle, official Russian (None = missing)
LINES: list[tuple[str, str, float, int, int, float, str, str | None]] = [
    (
        "dlg_a1_m01_d00_Hero_01",
        "event:/Dialogue/Act1/Hero/dlg_a1_m01_d00_Hero_01",
        3.6,
        24000,
        1,
        120.0,
        "{ColorStart}{CharacterName}:{ColorEnd} Hello Mia, the old engine is broken again.",
        "{ColorStart}{CharacterName}:{ColorEnd} Привет, Миа, старый мотор опять сломался.",
    ),
    (
        "dlg_a1_m01_d00_Hero_02",
        "event:/Dialogue/Act1/Hero/dlg_a1_m01_d00_Hero_02",
        3.2,
        48000,
        2,
        125.0,
        "Bring me the wrench from the garage, please.",
        "Принеси мне ключ из гаража, пожалуйста.",
    ),
    (
        "dlg_a1_m01_d01_Mia_01",
        "event:/Dialogue/Act1/Mia/dlg_a1_m01_d01_Mia_01",
        3.4,
        24000,
        1,
        210.0,
        "I will fix it before the sun goes down.",
        None,
    ),
    (
        "dlg_a1_m02_cs00_i00_Intro",
        "event:/Cutscenes/a1_m02_cs00_Intro/dlg_a1_m02_cs00_i00_Intro",
        4.0,
        24000,
        1,
        100.0,
        "Long ago, in a small town, lived a mechanic.",
        "Давным-давно в маленьком городке жил механик.",
    ),
]
MIA_DUB = "Я починю его до заката."


# ---- synthetic game ------------------------------------------------------------------------
def speechlike(seconds: float, sr: int, ch: int, f0: float, seed: int) -> np.ndarray:
    """Voiced-speech stand-in: harmonics with a syllable-rate envelope, int16 [frames, ch]."""
    t = np.arange(int(seconds * sr)) / sr
    sig = sum(np.sin(2 * np.pi * k * f0 * t) / k for k in range(1, 6)) * (
        0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)
    )
    sig = 0.3 * sig / np.max(np.abs(sig)) + 0.002 * np.random.default_rng(seed).standard_normal(t.size)
    ramp = min(len(t) // 4, int(0.02 * sr))
    sig[:ramp] *= np.linspace(0.0, 1.0, ramp)
    sig[-ramp:] *= np.linspace(1.0, 0.0, ramp)
    out = np.stack([sig * (1.0 - 0.15 * c) for c in range(ch)], axis=1)
    return np.round(out * 32767).astype(np.int16)


def gg_header(version: str) -> bytes:
    head = bytearray(0x30)
    struct.pack_into(">I", head, 0x08, 22)
    return bytes(head) + version.encode("ascii") + b"\0" + b"\0" * 64


def make_game(root: Path, studio: Path) -> Path:
    """Unity game folder with an FMOD dialogue bank, a strings bank and Localization bundles."""
    sa = root / SA
    sa.mkdir(parents=True)
    (root / "Synth_Data" / "globalgamemanagers").write_bytes(gg_header("2022.3.62f3"))
    (root / "UnityPlayer.dll").write_bytes(b"MZ" + b"\0" * 64)
    # the "studio" renders the voice lines to WAV; the FMOD adapter's writer packs them into FSB5
    studio.mkdir(parents=True)
    voice: list[SubSpec] = []
    for n, (key, _event, secs, sr, ch, f0, _en, _ru) in enumerate(LINES):
        wav = studio / f"{key}.wav"
        sf.write(str(wav), speechlike(secs, sr, ch, f0, n), sr, subtype="PCM_16")
        pcm, rate = sf.read(str(wav), dtype="int16", always_2d=True)
        voice.append(SubSpec(key, rate, pcm))
    sfx = make_fsb(F.CODEC_PCM16, [SubSpec("boom", 44100, speechlike(0.5, 44100, 2, 60.0, 9))])
    table_keys = [event.split(":/", 1)[1] for _k, event, *_ in LINES]
    bank = make_bank([sfx, make_fsb(F.CODEC_PCM16, voice)], prib_keys=table_keys, stbl_indices=[2, 0, 3, 1])
    (sa / "Dialogue_en-US.bank").write_bytes(bank)
    events = [
        "bank:/Master",
        "bank:/Dialogue_en-US",
        *(event for _k, event, *_ in LINES),
        "event:/Sound Effects/boom",
    ]
    strings, _ = make_strings_bank(events)
    (sa / "Master.strings.bank").write_bytes(strings)
    aa = root / AA
    aa.mkdir(parents=True)
    for name in BUNDLES.values():
        (aa / name).write_bytes(b"UnityFS\0" + b"\0" * 64)
    return root


def fake_unity_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Content of the Localization bundles: one shared key table, en and ru string tables."""
    ids = {key: n + 1 for n, (key, *_rest) in enumerate(LINES)}
    shared = TableObject(
        kind=SHARED_TABLE,
        path_id=-7,
        data={
            "m_TableCollectionName": "Dialogues_Act_1",
            "m_Entries": [{"m_Id": i, "m_Key": k} for k, i in ids.items()],
        },
    )

    def strings(path_id: int, locale: str, values: dict[str, str]) -> TableObject:
        return TableObject(
            kind=STRING_TABLE,
            path_id=path_id,
            data={
                "m_Name": f"Dialogues_Act_1_{locale}",
                "m_LocaleId": {"m_Code": locale},
                "m_SharedData": {"m_FileID": 1, "m_PathID": -7},
                "m_TableData": [{"m_Id": ids[k], "m_Localized": v} for k, v in values.items()],
            },
        )

    en = strings(10, "en", {key: en for key, _e, _s, _r, _c, _f, en, _ru in LINES})
    ru = strings(11, "ru", {key: ru for key, _e, _s, _r, _c, _f, _en, ru in LINES if ru})
    probes = {
        BUNDLES["shared"]: BundleProbe(path=Path("s"), unity_version="2022.3.62f3", tables=[shared]),
        BUNDLES["en"]: BundleProbe(path=Path("e"), unity_version="2022.3.62f3", tables=[en]),
        BUNDLES["ru"]: BundleProbe(path=Path("r"), unity_version="2022.3.62f3", tables=[ru]),
    }

    def probe(path: Path, game_dir: Path | None = None, generator: Any = None) -> BundleProbe:
        return probes[path.name]

    monkeypatch.setattr(ul, "probe_bundle", probe)


# ---- fake qc worker (stands in for faster-whisper + ECAPA in the qc venv) ----------------------
class FakeQc:
    """Speaker embeddings by character (from the file's uid), ASR = the text the dub was made from."""

    restarts = 0
    max_restarts = 3

    def __init__(self, project_root: Path) -> None:
        self.root = project_root
        self.requests: list[dict[str, Any]] = []

    def _char_of(self, uid: str) -> str:
        for line in latest_by(read_jsonl(self.root / layout.LINES)).values():
            if line["uid"] == uid:
                return str(line.get("speaker_hint") or "narrator")
        return "stranger"

    def load(self, msg: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "op": "load", "device": msg.get("device", "cpu"), "info": {}}

    def request(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(msg)
        path = Path(msg["wav"]).resolve()
        if msg["op"] == "embed":
            centre = np.random.default_rng(zlib.crc32(self._char_of(path.stem).encode())).standard_normal(192)
            noise = np.random.default_rng(zlib.crc32(str(path).lower().encode())).standard_normal(192)
            vec = centre / np.linalg.norm(centre) + 0.15 * noise / np.linalg.norm(noise)
            return {"ok": True, "op": "embed", "id": msg.get("id"), "emb": [float(x) for x in vec]}
        if msg["op"] == "asr":
            spoken = ""
            if path.parent.name == "audio" and path.parent.parent.name == "post":  # post/audio/<uid>.wav
                rows = latest_by(read_jsonl(self.root / layout.SYNTH_RESULTS)).values()
                spoken = next((r["text"] for r in rows if f"/{path.stem}/" in str(r.get("wav"))), "")
            return {"ok": True, "op": "asr", "id": msg.get("id"), "text": spoken}
        info = sf.info(str(path))
        if msg["op"] == "vad":  # analyze: one speech segment with 0.1 s margins
            return {"ok": True, "op": "vad", "segments": [[0.1, round(info.duration - 0.1, 3)]],
                    "speech_s": round(info.duration - 0.2, 3), "dur_s": info.duration}
        if msg["op"] == "asr_words":  # analyze: the original says its subtitle
            line = next(ln for ln in latest_by(read_jsonl(self.root / layout.LINES)).values() if ln["uid"] == path.stem)
            text = (line.get("text") or {}).get("src") or ""
            words = [{"w": w, "start": 0.1, "end": 0.5, "prob": 0.9} for w in text.split()]
            return {"ok": True, "op": "asr_words", "text": text, "lang": "en", "words": words,
                    "avg_logprob": -0.2, "no_speech_prob": 0.01}
        if msg["op"] == "prosody":
            return {"ok": True, "op": "prosody", "f0_median_hz": 140.0, "f0_range_st": 5.0, "rms_db": -20.0,
                    "voiced_ratio": 0.6, "dur_s": info.duration}
        return {"ok": False, "op": msg["op"], "error": "unsupported"}

    def close(self) -> None:
        return None


# ---- helpers -------------------------------------------------------------------------------
def sha1_tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha1(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture()
def bd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setenv("BABELDUB_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("BABELDUB_PROJECT", raising=False)
    monkeypatch.chdir(tmp_path)

    def run(*argv: Any) -> tuple[int, dict[str, Any]]:
        capsys.readouterr()
        code = cli.main([str(a) for a in argv])
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1, (argv, out)  # contract: exactly one JSON object on stdout
        return code, json.loads(out[0])

    return run


# ---- the run -------------------------------------------------------------------------------
def test_pipeline_end_to_end(tmp_path: Path, bd, monkeypatch: pytest.MonkeyPatch) -> None:
    pristine = make_game(tmp_path / "pristine", tmp_path / "studio")
    game = tmp_path / "Synth Game"  # the project works on a copy; the pristine folder is never touched
    shutil.copytree(pristine, game)
    before = sha1_tree(pristine)
    fake_unity_probes(monkeypatch)

    # init / settings
    code, out = bd(
        "init", game, "--lang", "ru", "--cast", "all", "--slug", "synth-game", "--name", "Synth Game"
    )
    assert code == EXIT_OK, out
    root = Path(out["project"])
    assert out["next"] == "babeldub scan"
    # post.tolerance: CI runners have no ffmpeg, so the dubs keep their natural tempo here (the bounded
    # tempo fit is covered by tests/voice); best of 2 candidates per line
    code, out = bd("set", "tts.engine=dummy", "compute.device=cpu", "post.tolerance=100", "tts.best_of=2")
    assert code == EXIT_OK, out
    assert bd("set", "compute.device=gpu")[0] == EXIT_USAGE

    # recon
    code, out = bd("scan")
    assert code == EXIT_OK, out
    assert out["engine"]["id"] == "unity" and out["level"] == "A"
    assert {m["id"] for m in out["middleware"]} >= {"fmod"} and "unity-localization" in out["text_systems"]
    assert any(BANK in v for v in out["voice_assets"])
    recon = read_json(root / layout.RECON)
    refs = recon["refs"]["recon"] + recon["refs"]["pack"]
    assert "skills/babeldub-recon/references/audio/fmod.md" in refs
    assert all((REPO / ref).is_file() for ref in refs), refs  # every pointer of the report is a real file

    code, out = bd("extract")
    assert code == EXIT_OK, out
    assert out["lines"] == 4 and out["by_kind"] == {"dialogue": 3, "cutscene": 1} and out["failed"] == 0
    lines = latest_by(read_jsonl(root / layout.LINES))
    for line in lines.values():
        data, sr = sf.read(str(root / line["audio"]["wav"]), dtype="int16", always_2d=True)
        spec = next(s for s in LINES if s[0] == line["entry"]["key"])
        assert sr == spec[3] and data.shape[1] == spec[4]  # decoded natively, bit-exact format
    assert {ln["speaker_hint"] for ln in lines.values()} == {"Hero", "Mia", None}

    code, out = bd("link")
    assert code == EXIT_OK, out
    assert out["linked"] == 4 and out["with_official_target"] == 3 and out["src_lang"] == "en"
    hero1 = next(
        ln
        for ln in latest_by(read_jsonl(root / layout.LINES)).values()
        if ln["entry"]["key"].endswith("Hero_01")
    )
    assert hero1["text"]["src"] == "Hello Mia, the old engine is broken again."  # speaker label stripped

    # analyze: VAD, ASR and prosody of the originals (fake qc worker), line classes
    code, out = bd("status")
    assert out["next_stage"] == "analyze", out
    import babeldub.qa.qc as qc

    with monkeypatch.context() as m:
        fake = FakeQc(root)
        m.setattr(qc, "qc_missing", lambda *a, **k: [])
        m.setattr(qc, "_make_client", lambda log_path: fake)
        code, out = bd("analyze")
        assert code == EXIT_OK and out["analyzed"] == 4 and out["subtitle_mismatch"] == 0, out
        assert sum(out["classes"].values()) == 4 and {r["op"] for r in fake.requests} == {"vad", "asr_words", "prosody"}
        code, out = bd("analyze")
        assert code == EXIT_OK and out["analyzed"] == 0 and out["up_to_date"] == 4, out
    code, out = bd("query", "--where", "voiced=true", "--fields", "id,class")
    assert all(r["class"] for r in out["rows"]), out

    # cast: speakers from FMOD event folders, the cutscene narrator is unknown until labelled
    code, out = bd("cast")
    assert code == EXIT_ATTENTION and out["uncertain"] == ["unknown"], out
    assert out["refs"] == 3 and out["no_ref"] == []
    (root / layout.CAST_LABELS_AGENT).write_text(
        json.dumps(
            {"char_id": "unknown", "name": "Narrator", "name_tgt": "Рассказчик", "gender": "m", "age": "old"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    code, out = bd("cast", "apply-labels")
    assert code == EXIT_OK and out["uncertain"] == [], out

    # text: the official Russian exists for most lines, so `auto` adapts it (spoken style, length budget);
    # the line without one is translated in the same batch
    code, out = bd("translate", "plan")
    assert code == EXIT_OK and out["mode"] == "adapt" and out["need_agent"] == 4, out
    (root / layout.GLOSSARY_AGENT).parent.mkdir(parents=True, exist_ok=True)
    (root / layout.GLOSSARY_AGENT).write_text(
        json.dumps({"term": "Mia", "translation": "Миа"}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    assert bd("glossary", "apply")[0] == EXIT_OK
    code, out = bd("batch", "make")
    assert code == EXIT_OK and out["batches"] == ["b0001"] and out["lines"] == 4, out
    code, out = bd("batch", "next")
    assert code == EXIT_OK and out["state"] == "new", out
    header, *rows = read_jsonl_raw(Path(out["in"]))
    assert header["_batch"] == "b0001" and header["target_lang"] == "ru" and header["mode"] == "adapt"
    row = next(r for r in rows if r["speaker"] == "Mia")
    assert row["src"] == "I will fix it before the sun goes down." and row["tgt_official"] is None
    assert "task" not in row  # no official text: translated
    adapted = [r for r in rows if r.get("task") == "adapt"]
    assert len(adapted) == 3 and all(r["tgt_official"] for r in adapted)
    out_path = Path(out["out"])
    out_path.write_text(json.dumps({"id": row["id"], "dub_text": ""}) + "\n", encoding="utf-8")
    code, out = bd("batch", "submit", "b0001")
    assert code == EXIT_USAGE and out["merged"] == 0, out  # invalid agent file: nothing merged
    answers = [{"id": row["id"], "dub_text": MIA_DUB}]
    answers += [{"id": r["id"], "dub_text": r["tgt_official"].replace("Давным-давно", "Давным-давным-давно")}
                for r in adapted]
    out_path.write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in answers), encoding="utf-8")
    code, out = bd("batch", "submit", "b0001")
    assert code == EXIT_OK and out["merged"] == 4, out
    assert bd("batch", "next")[1]["done"] is True
    merged = latest_by(read_jsonl(root / layout.TRANSLATIONS))
    intro = next(r for r in adapted if r["tgt_official"].startswith("Давным"))
    assert merged[intro["id"]]["sub_text"] == intro["tgt_official"]  # the subtitle stays official
    assert merged[intro["id"]]["dub_text"].startswith("Давным-давным-давно")
    code, out = bd("glossary", "check")
    assert code == EXIT_OK and out["total"] == 0, out

    # voice: every line is dubbed with its own original line as the voice prompt
    code, out = bd("synth", "--dry-run")
    assert code == EXIT_OK and out["would_run"] == 4 and out["by_ref"] == {"line": 4}, out
    code, out = bd("synth")
    assert code == EXIT_OK and out["done"] == 4 and out["failed"] == 0, out
    code, out = bd("synth")
    assert code == EXIT_OK and out["done"] == 0 and out["skipped"]["done"] == 4, out  # idempotent
    results = latest_by(read_jsonl(root / layout.SYNTH_RESULTS))
    for lid, res in results.items():
        assert res["ref_mode"] == "line" and res["ref_wav"] == lines[lid]["audio"]["wav"]
    code, out = bd("post")
    assert code == EXIT_OK and out["done"] == 4 and out["over_length"] == [], out
    for lid, post in latest_by(read_jsonl(root / layout.POST)).items():
        info = sf.info(str(root / post["wav"]))
        assert (info.samplerate, info.channels) == (lines[lid]["audio"]["sr"], lines[lid]["audio"]["ch"])
    for lid, res in latest_by(read_jsonl(root / layout.SYNTH_RESULTS)).items():
        n = voice.FIXED_LENGTH_BEST_OF if lines[lid]["fixed_length"] else 2  # a cutscene line gets more takes
        assert res["best_of"]["n"] == n and res["best_of"]["usable"] == n

    # vc: no character has an RVC model here, every line passes through unchanged
    code, out = bd("vc")
    assert code == EXIT_OK and out["converted"] == 0 and sum(out["passthrough"].values()) == 4, out
    assert "vc train" in out["notes"][0] and out["next"] == "babeldub qa"

    # qa: honest EXIT_ENV without the qc venv, then a fake qc worker
    code, out = bd("qa")
    assert code == EXIT_ENV and "setup --engines qc" in out["hint"], out
    code, out = bd("status")
    assert out["stages"]["qa"] == "pending" and out["next"] == "babeldub qa", out
    fake = FakeQc(root)
    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: [])
    monkeypatch.setattr(qc, "_make_client", lambda log_path: fake)
    code, out = bd("qa")
    assert code == EXIT_OK, out
    assert out["checked"] == 4 and out["pass"] == 4 and out["accept"] == 4 and out["cer_median"] == 0.0, out
    assert {r["op"] for r in fake.requests} == {"asr", "embed", "prosody"}  # no MOS model downloaded
    (root / layout.DECISIONS_AGENT).write_text(
        json.dumps({"id": row["id"], "decision": "accept", "note": "listened: fine"}) + "\n", encoding="utf-8"
    )
    code, out = bd("decide", "apply")
    assert code == EXIT_OK and out["applied"] == 1, out
    assert bd("decide", "apply")[1]["applied"] == 0  # idempotent
    code, out = bd("report")
    assert code == EXIT_OK and (root / layout.QA_REPORT).is_file(), out
    code, out = bd(
        "query", "--where", "decision=accept", "--fields", "id,translation.dub_text", "--limit", "10"
    )
    assert code == EXIT_OK and out["total"] == 4, out

    # pack: native rebuild, verification, install into the copy, rollback
    code, out = bd("build")
    assert code == EXIT_OK, out
    assert out["replaced"] == 4 and out["containers"][0]["codec"] == "pcm16", out
    code, out = bd("verify")
    assert code == EXIT_OK and out["ok"] is True and out["checks"] > 10, out
    code, out = bd("install", "--dry-run")
    assert code == EXIT_OK and sha1_tree(game) == before, out
    code, out = bd("install")
    assert code == EXIT_OK and out["files"] == 1, out
    built = root / layout.BUILD_DIR / BANK
    assert (game / BANK).read_bytes() == built.read_bytes() != (pristine / BANK).read_bytes()
    code, out = bd("install")
    assert code == EXIT_OK and out["unchanged"] == 1, out
    code, out = bd("reapply")
    assert code == EXIT_OK and out["current"] == 1, out
    code, out = bd("status")
    assert out["counts"]["installed"] == 1 and out["counts"]["replaced"] == 4, out
    code, out = bd("uninstall")
    assert code == EXIT_OK, out
    assert sha1_tree(game) == before == sha1_tree(pristine)  # the game is exactly the original again
    code, out = bd("status")
    assert code == EXIT_ATTENTION and out["attention"]["install"]["state"] == "uninstalled", out


def read_jsonl_raw(path: Path) -> list[dict[str, Any]]:
    """All rows including the batch header (core.jsonl readers skip only _schema headers)."""
    return [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines() if raw.strip()]
