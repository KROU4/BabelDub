"""Arousal buckets: reference banks per character, the target-language prompt bank and the choice per line."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from babeldub.analysis.store import ANALYSIS_SCHEMA
from babeldub.audio.io import write_wav
from babeldub.cast import bootstrap
from babeldub.cast.refs import (
    Arousal,
    arousal_vector,
    emotion_banks,
    line_arousal,
    prosody_stats,
    ranked_buckets,
)
from babeldub.commands import voice
from babeldub.core import layout
from babeldub.core.ids import uid as make_uid
from babeldub.core.jsonl import latest_by, read_json, read_jsonl, write_json, write_jsonl
from babeldub.core.output import EXIT_OK
from babeldub.core.project import Project

SR = 24000
LOUD = {2, 5, 6, 9}  # line numbers spoken loud; the rest calm


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--project")
        sp.add_argument("--pretty", action="store_true")

    voice.register(sub, common)
    return p


def run(capsys: pytest.CaptureFixture[str], project: Path, *argv: str) -> tuple[int, dict[str, Any]]:
    args = parser().parse_args([argv[0], "--project", str(project), *argv[1:]])
    code = args.func(args)
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1, out
    return code, json.loads(out[0])


def tone(seconds: float, f0: float) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    sig = 0.3 * np.sin(2 * np.pi * f0 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
    return np.concatenate([np.zeros(int(0.1 * SR)), sig, np.zeros(int(0.1 * SR))]).astype(np.float32)


def lid(n: int) -> str:
    return f"fmod:Audio/Dialogue_en.bank#fsb0:{n:04d}"


def prosody(n: int) -> dict[str, Any]:
    return {"rms_db": -10.0, "f0_range_st": 7.0, "syl_rate": 5.0} if n in LOUD else \
        {"rms_db": -20.0, "f0_range_st": 4.0, "syl_rate": 5.0}


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    home = tmp_path / "home"
    monkeypatch.setenv("BABELDUB_HOME", str(home))
    root = home / "projects" / "bk"
    proj = Project.create(root, {"slug": "bk", "game_name": "Bk", "game_dir": str(tmp_path / "game"),
                                 "target_lang": "ru", "source_lang": "en",
                                 "tts": {"engine": "dummy", "ref_strategy": "bootstrap", "best_of": 1},
                                 "post": {"tolerance": 100.0}})
    lines, translations, analysis = [], [], []
    for n in range(1, 11):
        u = make_uid(lid(n))
        wav = write_wav(root / layout.ORIG_AUDIO_DIR / f"{u}.wav", tone(5.0, 110.0 + 5 * n), SR)
        lines.append({"id": lid(n), "uid": u, "kind": "dialogue", "locale": "en-US",
                      "audio": {"sr": SR, "ch": 1, "dur_s": 5.2, "wav": proj.rel(wav)},
                      "speaker_hint": "Zbyszek", "scene": "s1", "order": n, "fixed_length": False, "flags": [],
                      "text": {"src": f"This is the original line number {n}.", "src_lang": "en"}})
        translations.append({"id": lid(n), "dub_text": f"Это реплика номер {n}, малой.", "lang": "ru", "by": "agent"})
        analysis.append({"id": lid(n), "uid": u, "class": "speech", "speech_s": 5.0, "prosody": prosody(n)})
    write_jsonl(root / layout.LINES, lines, schema="babeldub.lines/1")
    write_jsonl(root / layout.TRANSLATIONS, translations, schema="babeldub.translations/1")
    write_jsonl(root / layout.ANALYSIS, analysis, schema=ANALYSIS_SCHEMA)
    return proj


def test_arousal_math() -> None:
    rows = [{"prosody": prosody(n)} for n in range(1, 11)]
    stats = prosody_stats(rows)
    assert stats is not None and stats.rms_db == -20.0 and stats.f0_range_st == 4.0
    assert prosody_stats(rows[:2]) is None  # too few lines: everything is calm
    assert arousal_vector(prosody(2), stats) == pytest.approx((10 / 6, 0.75))
    assert ranked_buckets(arousal_vector(prosody(2), stats))[0] == "loud"
    assert ranked_buckets(arousal_vector(prosody(1), stats))[0] == "calm"
    soft = arousal_vector({"rms_db": -27.0, "f0_range_st": 3.0}, stats)
    assert ranked_buckets(soft)[0] == "soft"
    lively = arousal_vector({"rms_db": -19.0, "f0_range_st": 8.0, "syl_rate": 6.0}, stats)
    assert ranked_buckets(lively)[0] == "animated"
    assert ranked_buckets(None, ["loud", "calm"]) == ["calm", "loud"]
    assert ranked_buckets((0.0, 0.0), emotion="sad")[0] == "soft"  # emotion moves a calm line
    assert ranked_buckets((2.0, 1.0), emotion="sad")[0] == "loud"  # ... not a loud one
    assert Arousal(None, None).bucket == "calm"


def test_emotion_banks_use_clean_speech_only() -> None:
    lines = [{"id": lid(n), "flags": ["has_bed"] if n == 5 else []} for n in range(1, 11)]
    analysis = {lid(n): {"class": "mixed" if n == 6 else "speech", "prosody": prosody(n)} for n in range(1, 11)}
    banks = emotion_banks(lines, analysis)
    assert banks == {"calm": [lid(n) for n in (1, 3, 4, 7, 8, 10)], "loud": [lid(2), lid(9)]}
    assert emotion_banks(lines, {}) == {"calm": [ln["id"] for ln in lines if not ln["flags"]]}


def test_bucket_references_and_line_choice(project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, project.root, "synth")
    assert code == EXIT_OK and out["by_ref"] == {"bootstrap-src": 4}, out
    rows = latest_by(read_jsonl(project.path(layout.SYNTH_RESULTS)))
    assert rows[lid(2)]["bucket"] == "loud" and rows[lid(2)]["ref_id"].startswith("long:Zbyszek:loud:")
    assert rows[lid(1)]["bucket"] == "calm" and rows[lid(1)]["ref_id"].count(":") == 2
    assert rows[lid(2)]["ref_wav"] != rows[lid(1)]["ref_wav"]


def _accepted(project: Project, n: int) -> tuple[str, Path]:
    wav = write_wav(project.path(layout.POST_DIR, f"{make_uid(lid(n))}.wav"), tone(4.0 + 0.5 * (n % 3), 150.0), SR)
    return lid(n), wav


def test_target_bank_per_bucket(project: Project) -> None:
    post, qa, dec, texts, buckets = {}, {}, {}, {}, {}
    spec = {1: (0.05, 4.5, 0.9), 3: (0.01, 2.0, 0.2), 4: (0.10, 4.0, 0.8), 2: (0.02, 3.5, 0.7), 7: (0.5, 5.0, 1.0)}
    for n, (cer, mos, pros) in spec.items():
        line_id, wav = _accepted(project, n)
        rel = project.rel(wav)
        post[line_id] = {"id": line_id, "attempt": 1, "wav": rel, "target_s": 4.0 if n != 4 else 5.5}
        qa[line_id] = {"id": line_id, "attempt": 1, "wav": rel, "cer": cer, "mos": mos, "prosody_sim": pros}
        dec[line_id] = {"decision": "accept", "attempt": 1}
        texts[line_id] = f"текст {n}"
        buckets[line_id] = "loud" if n in LOUD else "calm"
    kw: dict[str, Any] = dict(post=post, qa=qa, decisions=dec, texts=texts, max_cer=0.15, nonverbal=set(),
                              buckets=buckets)
    assert bootstrap.target_bank(project.root, "Zbyszek", list(post), min_accepted=6, **kw) == {}
    bank = bootstrap.target_bank(project.root, "Zbyszek", list(post), min_accepted=3, **kw)
    assert set(bank) == {"calm", "loud"}
    # calm: line 1 ranks first (MOS + prosody), CER 0.5 of line 7 is gated out; 4 s < 5 s adds line 4 (5.5 s)
    assert bank["calm"].lines == [lid(1), lid(4)] and bank["calm"].text == "текст 1 текст 4"
    assert bank["loud"].lines == [lid(2)]
    meta = read_json(project.path(layout.SYNTH_PROMPTS_DIR, "Zbyszek", "tgt.json"))
    assert set(meta["bank"]) == {"calm", "loud"}
    # frozen: a better dub later does not replace a built prompt
    qa[lid(3)]["mos"] = 5.0
    again = bootstrap.target_bank(project.root, "Zbyszek", list(post), min_accepted=3, **kw)
    assert again["calm"].ref_id == bank["calm"].ref_id


def test_old_single_prompt_is_the_calm_bucket(project: Project) -> None:
    folder = project.path(layout.SYNTH_PROMPTS_DIR, "Zbyszek")
    write_wav(folder / "tgt-abc.wav", tone(5.0, 140.0), SR)
    write_json(folder / "tgt.json", {"wav": "tgt-abc.wav", "text": "старый промпт", "ref_id": "tgt:Zbyszek:abc",
                                     "dur_s": 5.2, "lines": [lid(1)]})
    bank = bootstrap.target_bank(project.root, "Zbyszek", [], post={}, qa={}, decisions={}, texts={},
                                 min_accepted=3, max_cer=0.15, nonverbal=set())
    assert list(bank) == ["calm"] and bank["calm"].text == "старый промпт"


def test_pick_and_rebucket_after_a_flat_take() -> None:
    prompt = bootstrap.Prompt(Path("x.wav"), "", "long:c:k", 15.0, [])
    loud = bootstrap.Prompt(Path("y.wav"), "", "long:c:loud:k", 15.0, [])
    tgt = bootstrap.Prompt(Path("t.wav"), "т", "tgt:c:loud:k", 6.0, [])
    lines = [{"id": lid(n)} for n in range(1, 11)]
    analysis = {lid(n): {"prosody": prosody(n)} for n in range(1, 11)}
    state = voice._Bootstrap({"calm": prompt, "loud": loud}, {"loud": tgt}, [], True, line_arousal(lines, analysis))
    assert state.pick(lid(2)) == ("loud", loud, tgt)
    assert state.pick(lid(1)) == ("loud", prompt, tgt)  # only a loud prompt so far; calm timbre reference
    assert state.pick(lid(2), avoid="loud") == ("loud", prompt, tgt)  # the bank has nothing else
    flat = {"attempt": 1, "reasons": ["flat"]}
    assert voice._avoid_bucket("regen", {"attempt": 1, "bucket": "loud"}, flat) == "loud"
    assert voice._avoid_bucket("regen", {"attempt": 1, "bucket": "loud"}, {"attempt": 1, "reasons": ["cer"]}) is None
    assert voice._avoid_bucket("new", {"attempt": 1, "bucket": "loud"}, flat) is None


def test_ref_rows_carry_the_bucket(tmp_path: Path) -> None:
    from babeldub.cast.refs import RefCandidate, ref_rows

    cand = RefCandidate(line={"id": lid(2), "uid": "u2", "text": {"src_lang": "en"}}, wav=tmp_path / "a.wav",
                        dur_s=5.0, text="hi")
    assert ref_rows("Zbyszek", [cand], "en", {lid(2): "loud"})[0]["bucket"] == "loud"
    assert "bucket" not in ref_rows("Zbyszek", [cand], "en")[0]
