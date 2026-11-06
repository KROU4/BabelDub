from __future__ import annotations

import json

from babeldub.core import layout
from babeldub.core.jsonl import latest_by, read_jsonl
from babeldub.core.output import EXIT_ATTENTION, EXIT_ENV, EXIT_OK, EXIT_USAGE


def _scenario(dub) -> dict[str, str]:
    ids: dict[str, str] = {}
    base = [dub.line("hero", 4.0) for _ in range(4)]  # clean originals: baseline + reference
    dub.ref("hero", base[0])
    ids["good"] = base[1]
    dub.translate(base[1], "Привет, как дела?")
    dub.dub(base[1], voice="hero", asr="привет как дела")
    ids["mumble"] = base[2]
    dub.translate(base[2], "Мы немного выпили.")
    dub.dub(base[2], voice="hero", asr="совсем другое")
    ids["drift"] = base[3]
    dub.translate(base[3], "Садись в машину.")
    dub.dub(base[3], voice="stranger", asr="садись в машину")
    ids["long"] = dub.line("hero", 2.0)
    dub.translate(ids["long"], "Очень длинная фраза.")
    dub.dub(ids["long"], voice="hero", asr="очень длинная фраза", ratio=1.6)
    ids["fixed"] = dub.line("hero", 2.0, fixed_length=True)
    dub.translate(ids["fixed"], "Катсцена.")
    dub.dub(ids["fixed"], voice="hero", asr="катсцена", ratio=1.06)
    ids["last"] = dub.line("hero", 3.0)
    dub.translate(ids["last"], "Последняя попытка.")
    dub.dub(ids["last"], attempt=3, voice="hero", asr="что-то не то")
    ids["silent"] = dub.line("hero", 3.0)
    dub.translate(ids["silent"], "Тишина.")
    dub.dub(ids["silent"], voice="hero", asr="", amp=0.00001)
    ids["notext"] = dub.line("hero", 3.0)
    dub.dub(ids["notext"], voice="hero", asr="что-то")
    dub.save()
    return ids


def test_qa_verdicts_and_auto_decisions(dub, fake_qc, run) -> None:
    ids = _scenario(dub)
    root = str(dub.project.root)
    code, out = run(["qa", "--project", root])
    assert code == EXIT_ATTENTION, out
    assert out["checked"] == 8 and out["pass"] == 1 and out["fail"] == 7
    qa = latest_by(read_jsonl(dub.project.path(layout.QA)))
    assert qa[ids["good"]]["verdict"] == "pass"
    assert qa[ids["good"]]["cer"] == 0.0
    assert qa[ids["good"]]["sim_line"] is not None and qa[ids["good"]]["sim_char"] is not None
    assert qa[ids["mumble"]]["reasons"] == ["cer"]
    assert qa[ids["drift"]]["reasons"] == ["sim"]
    assert qa[ids["drift"]]["sim"] < 0.75 * qa[ids["drift"]]["sim_ref"]
    assert qa[ids["long"]]["reasons"] == ["duration"]
    assert qa[ids["fixed"]]["reasons"] == ["duration"]
    assert qa[ids["last"]]["reasons"] == ["cer"]
    assert qa[ids["silent"]]["reasons"] == ["silent"] and qa[ids["silent"]]["asr_text"] is None
    assert qa[ids["notext"]]["reasons"] == ["no_dub_text"]
    dec = latest_by(read_jsonl(dub.project.path(layout.DECISIONS)))
    assert dec[ids["good"]]["decision"] == "accept" and dec[ids["good"]]["by"] == "auto"
    assert dec[ids["mumble"]]["decision"] == "regen"
    assert dec[ids["drift"]]["decision"] == "regen"
    assert dec[ids["silent"]]["decision"] == "regen"
    assert dec[ids["long"]]["decision"] == "shorten" and dec[ids["fixed"]]["decision"] == "shorten"
    assert dec[ids["last"]]["decision"] == "keep_original" and dec[ids["last"]]["attention"] is True
    assert ids["notext"] not in dec
    assert set(out["attention"]) == {ids["long"], ids["fixed"], ids["last"], ids["notext"]}
    assert out["regen"] == 3 and out["shorten"] == 2 and out["keep_original"] == 1 and out["accept"] == 1
    baseline = json.loads(dub.project.path("qa/baseline.json").read_text(encoding="utf-8"))
    assert baseline["chars"]["hero"]["source"] == "pairwise" and baseline["chars"]["hero"]["n"] >= 2
    assert fake_qc.loads[0]["asr_model"].endswith("faster-whisper-large-v3-turbo")
    asr_langs = {r["lang"] for r in fake_qc.requests if r["op"] == "asr"}
    assert asr_langs == {"ru"}
    assert fake_qc.closed == 1
    assert dub.project.progress()["qa"]["status"] == "attention"

    # idempotent: nothing new to check, the worker is not started again
    fake_qc.loads.clear()
    code, out = run(["qa", "--project", root])
    assert code == EXIT_ATTENTION and out["checked"] == 0 and fake_qc.loads == []

    # the agent decides the open items; only the shortening remains
    agent = [
        {"id": ids["last"], "decision": "keep_original", "note": "original is fine"},
        {"id": ids["notext"], "decision": "skip", "note": "untranslatable"},
        {"id": ids["fixed"], "decision": "accept", "note": "6 % over is ok here"},
    ]
    dub.project.path(layout.DECISIONS_AGENT).write_text("\n".join(json.dumps(r) for r in agent) + "\n", encoding="utf-8")
    code, out = run(["decide", "apply", "--project", root])
    assert code == EXIT_OK and out["applied"] == 3
    code, out = run(["decide", "apply", "--project", root])
    assert out["applied"] == 0 and out["skipped"] == 3
    code, out = run(["qa", "--project", root])
    assert code == EXIT_ATTENTION and out["attention"] == [ids["long"]]
    dec = latest_by(read_jsonl(dub.project.path(layout.DECISIONS)))
    assert dec[ids["fixed"]]["by"] == "agent" and dec[ids["fixed"]]["attempt"] == 1

    code, out = run(["report", "--project", root])
    assert code == EXIT_OK and out["attention"] == 1
    report = dub.project.path(layout.QA_REPORT).read_text(encoding="utf-8")
    assert report.startswith("# QA report — Demo Game")
    assert "| Hero | `hero` | 8 |" in report
    # the relative SIM rule only relaxes the absolute bar; an orig_sim at about zero keeps the absolute rule
    assert "relaxes it (`sim_rule: relative`): the dub\npasses at the bar" in report
    assert "an `orig_sim` ≤ 0.05 keeps the absolute rule" in report
    assert ids["long"] in report


def test_regenerated_attempt_is_rechecked_and_agent_decision_kept(dub, fake_qc, run) -> None:
    a = dub.line("hero", 4.0)
    b = dub.line("hero", 4.0)
    dub.translate(b, "Привет.")
    dub.dub(b, voice="hero", asr="пока")
    dub.save()
    root = str(dub.project.root)
    assert run(["qa", "--project", root])[0] == EXIT_OK  # regen is not an attention item
    # attempt 2 arrives from synth/post
    dub.dub(b, attempt=2, voice="hero", asr="привет")
    dub.save()
    code, out = run(["qa", "--project", root])
    assert code == EXIT_OK and out["checked"] == 1 and out["accept"] == 1
    qa_rows = [r for r in read_jsonl(dub.project.path(layout.QA)) if r["id"] == b]
    assert [r["attempt"] for r in qa_rows] == [1, 2]
    # a human decision for the current attempt is not overwritten by --force
    dub.project.path(layout.DECISIONS_AGENT).write_text(json.dumps({"id": b, "decision": "reref"}) + "\n", encoding="utf-8")
    assert run(["decide", "apply", "--by", "human", "--project", root])[0] == EXIT_OK
    run(["qa", "--force", "--project", root])
    dec = latest_by(read_jsonl(dub.project.path(layout.DECISIONS)))
    assert dec[b]["decision"] == "reref" and dec[b]["by"] == "human"
    assert a not in dec  # line a was never dubbed


def test_decide_apply_rejects_invalid(dub, run) -> None:
    lid = dub.line("hero", 3.0)
    dub.translate(lid, "Да.")
    dub.dub(lid, voice="hero", asr="да")
    dub.save()
    p = dub.project.path(layout.DECISIONS_AGENT)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"id": "nope", "decision": "approve"}\nnot json\n', encoding="utf-8")
    code, out = run(["decide", "apply", "--project", str(dub.project.root)])
    assert code == EXIT_USAGE and "invalid JSON" in out["error"]
    p.write_text('{"id": "nope", "decision": "approve"}\n', encoding="utf-8")
    code, out = run(["decide", "apply", "--project", str(dub.project.root)])
    assert code == EXIT_USAGE and len(out["errors"]) == 2
    assert not dub.project.path(layout.DECISIONS).exists()


def test_qa_worker_errors_and_missing_env(dub, fake_qc, run, monkeypatch) -> None:
    lid = dub.line("hero", 3.0)
    dub.translate(lid, "Да.")
    wav = dub.dub(lid, voice="hero", asr="да")
    dub.save()
    fake_qc.fail.add(str(wav.resolve()).lower())
    code, out = run(["qa", "--project", str(dub.project.root)])
    assert code == EXIT_ATTENTION and out["errors"] and out["attention"] == [lid]
    qa = latest_by(read_jsonl(dub.project.path(layout.QA)))
    assert qa[lid]["reasons"] == ["qc_error"]

    import babeldub.qa.qc as qc

    monkeypatch.setattr(qc, "qc_missing", lambda *a, **k: ["env:qc"])
    code, out = run(["qa", "--force", "--project", str(dub.project.root)])
    assert code == EXIT_ENV and out["missing"] == ["env:qc"]


def test_interrupted_qa_keeps_finished_lines(dub, fake_qc, run, monkeypatch) -> None:
    import babeldub.commands.qa as qa_cmd
    from babeldub.core.output import EXIT_ERROR, BabelDubError

    ids = []
    for i in range(4):
        lid = dub.line("hero", 3.0)
        dub.translate(lid, f"Реплика {i}.")
        dub.dub(lid, voice="hero", asr=f"реплика {i}")
        ids.append(lid)
    dub.save()
    monkeypatch.setattr(qa_cmd, "FLUSH_EVERY", 1)
    original = fake_qc.request
    calls = {"asr": 0}

    def crashing(msg):
        if msg["op"] == "asr":
            calls["asr"] += 1
            if calls["asr"] == 3:
                raise BabelDubError("worker qc_worker.py crashed too often", code=EXIT_ERROR)
        return original(msg)

    fake_qc.request = crashing
    code, out = run(["qa", "--project", str(dub.project.root)])
    assert code == EXIT_ERROR and "crashed" in out["error"]
    assert [r["id"] for r in read_jsonl(dub.project.path(layout.QA))] == ids[:2]
    assert fake_qc.closed == 1
    fake_qc.request = original
    code, out = run(["qa", "--project", str(dub.project.root)])
    assert code == EXIT_OK and out["checked"] == 2 and out["accept"] == 2


def test_followers_are_not_checked(dub, fake_qc, run) -> None:
    lead = dub.line("hero", 4.0)
    alt = dub.line("hero", 3.8, flags=("alt_take",))
    for lid, key in ((lead, "dlg_Intro_i05"), (alt, "dlg_Intro_i05_v1")):
        line = next(x for x in dub.lines if x["id"] == lid)
        line.update(asset_id="fmod:Data/Dialogue.bank", entry={"key": key})
        line["text"]["key"] = "dlg_Intro_i05"
        dub.translate(lid, "Привет.")
        dub.dub(lid, voice="hero", asr="привет")  # the alt take was post-processed by an older run
    dub.save()
    root = str(dub.project.root)
    code, out = run(["qa", "--project", root])
    assert code == EXIT_OK and out["checked"] == 1 and out["follows"] == 1 and out["pending"] == 0, out
    assert set(latest_by(read_jsonl(dub.project.path(layout.QA)))) == {lead}
    code, out = run(["qa", "--ids", alt, "--project", root])
    assert code == EXIT_USAGE and out["follows"] == {alt: lead} and out["n_follows"] == 1
    more = []
    for i in range(11):  # many followers asked at once: one JSON error, not a traceback
        lid = dub.line("hero", 1.0, flags=("alt_take",))
        line = next(x for x in dub.lines if x["id"] == lid)
        line.update(asset_id="fmod:Data/Dialogue.bank", entry={"key": f"dlg_Intro_i05_v{i + 2}"})
        line["text"]["key"] = "dlg_Intro_i05"
        more.append(lid)
    dub.save()
    code, out = run(["qa", "--ids", ",".join(more), "--project", root])
    assert code == EXIT_USAGE and out["n_follows"] == 11 and len(out["follows"]) == 10
    code, out = run(["report", "--project", root])
    assert code == EXIT_OK and out["lines"] == 1


def test_qa_requires_post(project, run) -> None:
    code, out = run(["qa", "--project", str(project.root)])
    assert code == EXIT_USAGE and "post" in out["error"]


def test_gpu_lock_is_held_and_released(dub, fake_qc, run, monkeypatch) -> None:
    import babeldub.qa.qc as qc

    lid = dub.line("hero", 3.0)
    dub.translate(lid, "Да.")
    dub.dub(lid, voice="hero", asr="да")
    dub.save()
    monkeypatch.setattr(qc, "resolve_device", lambda setting: "cuda")
    lock_path = dub.project.path(layout.GPU_LOCK)
    seen = {}
    original = fake_qc.request

    def spy(msg):
        seen.setdefault("locked", lock_path.exists())
        return original(msg)

    fake_qc.request = spy
    code, out = run(["qa", "--project", str(dub.project.root)])
    assert code == EXIT_OK and out["device"] == "cuda"
    assert seen["locked"] is True
    assert not lock_path.exists()
    assert fake_qc.loads[-1]["device"] == "cuda"


def test_qa_mos_prosody_timing_and_line_classes(dub, fake_qc, run, home) -> None:
    from pathlib import Path

    from babeldub.core.jsonl import write_jsonl

    def key_of(path):
        return str(Path(path).resolve()).lower()

    (home / "models" / "utmos22-strong").mkdir(parents=True)
    (home / "models" / "utmos22-strong" / "utmos22_strong_step7459_v1.pt").write_bytes(b"x")
    base = [dub.line("hero", 4.0) for _ in range(3)]  # clean originals: their MOS median is 3.5
    ids = {name: dub.line("hero", 3.0) for name in ("fine", "unnatural", "flat", "mixed", "short")}
    ids["cutscene"] = dub.line("hero", 3.0, fixed_length=True)
    for name, lid in ids.items():
        dub.translate(lid, "Привет, как дела?")
        wav = dub.dub(lid, voice="hero", asr="привет как дела")
        fake_qc.mos[key_of(wav)] = 2.0 if name == "unnatural" else 3.4
        fake_qc.prosody[key_of(wav)] = {"f0_range_st": 2.0 if name == "flat" else 5.5}
    for lid in base + list(ids.values()):
        line = next(x for x in dub.lines if x["id"] == lid)
        orig = dub.project.path(line["audio"]["wav"])
        fake_qc.mos[key_of(orig)] = 3.5
        fake_qc.prosody[key_of(orig)] = {"f0_range_st": 6.0}
    dub.save()
    post = read_jsonl(dub.project.path(layout.POST))
    for row in post:
        if row["id"] == ids["cutscene"]:
            row.update(onset_deltas=[0.0, 0.15], offset_deltas=[0.0, 0.02])
    write_jsonl(dub.project.path(layout.POST), post, schema="babeldub.post/1")
    write_jsonl(dub.project.path(layout.ANALYSIS), [{"id": ids["mixed"], "class": "mixed"},
                                                     {"id": ids["short"], "class": "short_word"}],
                schema="babeldub.analysis/1")
    code, out = run(["qa", "--project", str(dub.project.root)])
    qa = latest_by(read_jsonl(dub.project.path(layout.QA)))
    dec = latest_by(read_jsonl(dub.project.path(layout.DECISIONS)))
    assert qa[ids["fine"]]["verdict"] == "pass" and qa[ids["fine"]]["mos"] == 3.4 and qa[ids["fine"]]["orig_mos"] == 3.5
    assert qa[ids["fine"]]["prosody_sim"] == round(5.5 / 6.0, 4) and qa[ids["fine"]]["orig_f0_range_st"] == 6.0
    assert qa[ids["unnatural"]]["reasons"] == ["mos"] and dec[ids["unnatural"]]["decision"] == "regen"
    assert qa[ids["flat"]]["reasons"] == ["flat"] and dec[ids["flat"]]["decision"] == "regen"
    assert qa[ids["cutscene"]]["reasons"] == ["timing"]
    assert dec[ids["mixed"]]["decision"] == "accept" and dec[ids["mixed"]]["attention"] is True
    assert qa[ids["mixed"]]["class"] == "mixed" and ids["mixed"] in out["attention"]
    assert out["mos_median"] == 3.4
    mos_base = json.loads(dub.project.path("qa/mos_baseline.json").read_text(encoding="utf-8"))
    assert mos_base["chars"]["hero"] == {"median": 3.5, "n": 8}
    assert mos_base["model"] == "utmos22"  # an older project without qa.mos_model keeps UTMOS22
    assert fake_qc.loads[0]["mos_backend"] == "utmos22" and "mos_fallback" not in fake_qc.loads[0]


def test_qa_mos_baseline_is_remeasured_when_the_mos_model_changes(dub, fake_qc, run, home) -> None:
    from pathlib import Path

    def key_of(path):
        return str(Path(path).resolve()).lower()

    for name in ("utmos22-strong", "distillmos"):
        (home / "models" / name).mkdir(parents=True)
        (home / "models" / name / "w.pt").write_bytes(b"x")
    base = [dub.line("hero", 4.0) for _ in range(3)]
    lid = dub.line("hero", 3.0)
    dub.translate(lid, "Привет.")
    wav = dub.dub(lid, voice="hero", asr="привет")
    fake_qc.mos[key_of(wav)] = 3.0
    for b in base:
        fake_qc.mos[key_of(dub.project.path(next(x for x in dub.lines if x["id"] == b)["audio"]["wav"]))] = 3.6
    dub.save()
    # a v0.3 baseline file (no model recorded: UTMOS22)
    path = dub.project.path("qa/mos_baseline.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hero": {"median": 4.4, "n": 8}}), encoding="utf-8")
    dub.project.data.setdefault("qa", {})["mos_model"] = "distillmos"
    dub.project.save()
    fake_qc.mos_model = "distillmos"
    run(["qa", "--project", str(dub.project.root)])
    load = fake_qc.loads[0]
    assert load["mos_backend"] == "distillmos" and load["mos_model"].endswith("distillmos")
    assert load["mos_fallback"].endswith("utmos22-strong")
    qa = latest_by(read_jsonl(dub.project.path(layout.QA)))
    assert qa[lid]["orig_mos"] == 3.6  # measured again with Distill-MOS, not the UTMOS22 4.4
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["model"] == "distillmos" and saved["chars"]["hero"]["median"] == 3.6


def test_qa_russian_vowel_reduction_is_not_a_cer_failure(dub, fake_qc, run) -> None:
    base = [dub.line("hero", 4.0) for _ in range(3)]
    dub.ref("hero", base[0])
    reduced, garbled = dub.line("hero", 3.0), dub.line("hero", 3.0)
    dub.translate(reduced, "Закрыто. Посмотрим!")
    dub.dub(reduced, voice="hero", asr="закрыта пасмотрим")
    dub.translate(garbled, "Кошмар какой!")
    dub.dub(garbled, voice="hero", asr="кук фасут")
    dub.save()
    code, out = run(["qa", "--project", str(dub.project.root)])
    qa = latest_by(read_jsonl(dub.project.path(layout.QA)))
    row = qa[reduced]
    assert row["verdict"] == "pass" and row["cer"] == 0.0 and row["cer_folded"] == 0.0 and row["cer_raw"] > 0.1
    assert row["asr_text_raw"] == "закрыта пасмотрим"
    assert qa[garbled]["reasons"] == ["cer"] and qa[garbled]["cer"] > 0.15
    asr = [r for r in fake_qc.requests if r["op"] == "asr"]
    assert {r["text"] for r in asr} == {"Закрыто. Посмотрим!", "Кошмар какой!"}  # the reference for tail stripping


def test_qa_second_asr_lowers_the_cer_and_flags_disagreement(dub, fake_qc, run, home) -> None:
    from pathlib import Path

    giga = home / "models" / "GigaAM-v3-ctc"
    giga.mkdir(parents=True)
    (giga / "pytorch_model.bin").write_bytes(b"x")
    base = [dub.line("hero", 4.0) for _ in range(3)]
    dub.ref("hero", base[0])
    misheard, both_bad, broken = dub.line("hero", 3.0), dub.line("hero", 3.0), dub.line("hero", 3.0)
    for lid, whisper, giga_hears in ((misheard, "совсем другое", "садись в машину"),
                                     (both_bad, "совсем другое", "тоже другое"),
                                     (broken, "садись в машину", None)):
        dub.translate(lid, "Садись в машину.")
        wav = dub.dub(lid, voice="hero", asr=whisper)
        if giga_hears is not None:
            fake_qc.texts2[str(Path(wav).resolve()).lower()] = giga_hears
    dub.save()
    code, out = run(["qa", "--project", str(dub.project.root)])
    assert fake_qc.loads[0]["asr2_model"] == str(giga)
    qa = latest_by(read_jsonl(dub.project.path(layout.QA)))
    row = qa[misheard]
    assert row["verdict"] == "pass" and row["cer"] == 0.0 and row["cer_asr2"] == 0.0 and row["cer_raw"] > 0.5
    assert row["asr_disagree"] is True and row["asr2_text"] == "садись в машину"
    assert qa[both_bad]["reasons"] == ["cer"] and qa[both_bad]["asr_disagree"] is False
    # GigaAM fails on this line: it is turned off for the rest of the run with a warning, Whisper alone decides
    assert qa[broken]["cer_asr2"] is None and qa[broken]["verdict"] == "pass"
    assert out["asr_disagree"] == 1 and out["asr2"] == 2 and any("GigaAM" in w for w in out["warnings"])


def test_qa_without_a_russian_target_never_loads_the_second_asr(dub, fake_qc, run, home) -> None:
    giga = home / "models" / "GigaAM-v3-ctc"
    giga.mkdir(parents=True)
    (giga / "pytorch_model.bin").write_bytes(b"x")
    dub.project.data["target_lang"] = "pl"
    dub.project.save()
    lid = dub.line("hero", 3.0)
    dub.translate(lid, "Wsiadaj.")
    dub.dub(lid, voice="hero", asr="wsiadaj")
    dub.save()
    run(["qa", "--project", str(dub.project.root)])
    assert "asr2_model" not in fake_qc.loads[0] and not any(r["op"] == "asr2" for r in fake_qc.requests)


def test_qa_short_lines_judge_sim_relative_to_the_original(dub, fake_qc, run) -> None:
    v = dub.voices
    base = [dub.line("hero", 4.0) for _ in range(3)]
    dub.ref("hero", base[0])  # a typical reference; only the short originals are atypical
    weak = v.mix({"hero": 0.35, "noise1": 1.0})  # ECAPA on < 1 s: far from the hero even for the original
    short_ok, short_bad = dub.line("hero", 0.7, vec=weak), dub.line("hero", 0.7, vec=weak)
    dub.translate(short_ok, "Тихо!")
    dub.dub(short_ok, voice=None, vec=v.mix({"hero": 0.33, "noise2": 1.0}), asr="тихо", dur_s=0.7)
    dub.translate(short_bad, "Тихо!")
    dub.dub(short_bad, voice="stranger", asr="тихо", dur_s=0.7)
    dub.save()
    code, out = run(["qa", "--project", str(dub.project.root)])
    qa = latest_by(read_jsonl(dub.project.path(layout.QA)))
    ok, bad = qa[short_ok], qa[short_bad]
    assert ok["sim_rule"] == "relative" and ok["sim"] < ok["sim_threshold"]  # the absolute bar would fail it
    assert ok["sim_rel"] >= 0.8 and ok["verdict"] == "pass"  # sim_rel: the dub's cosine / orig_sim
    assert bad["sim_rule"] == "relative" and bad["sim_rel"] < 0.8 and bad["reasons"] == ["sim"]
    assert out["sim_relative"] == 2
    baseline = json.loads(dub.project.path("qa/baseline.json").read_text(encoding="utf-8"))
    assert baseline["lines"][short_ok]["orig_sim"] == ok["orig_sim"]
    ref_id = dub.refs[0]["ref_id"]  # what orig_sim was measured against: the reference, its character, its embedding
    assert baseline["lines"][short_ok]["against"].startswith(f"ref:{ref_id}:hero:")
    assert baseline["definition"].startswith("line-to-line") and baseline["chars"]["hero"]["sim_ref_loo"] is not None


def test_qa_cached_orig_sim_follows_a_recast(dub, fake_qc, run) -> None:
    v = dub.voices
    for char in ("hero", "villain"):
        for _ in range(3):
            dub.line(char, 4.0)
    long = dub.line("hero", 3.0)
    dub.translate(long, "Длинная реплика.")
    dub.dub(long, voice="hero", asr="длинная реплика")
    short = dub.line("hero", 0.7, vec=v.mix({"hero": 1.0, "villain": 0.2}))
    dub.translate(short, "Эй!")
    dub.dub(short, voice="hero", asr="эй", dur_s=0.7)
    dub.save()
    run(["qa", "--project", str(dub.project.root)])
    qa = latest_by(read_jsonl(dub.project.path(layout.QA)))
    assert qa[long]["sim_rule"] == "absolute" and qa[long]["sim_rel"] is None  # sim_rel only under the relative rule
    path = dub.project.path("qa/baseline.json")
    first = json.loads(path.read_text(encoding="utf-8"))["lines"][short]
    assert first["against"].startswith("lines:hero:")
    # the line belonged to the villain: its own sim is measured again against the villain's clean originals
    dub.assign = [a if a["id"] != short else {**a, "char_id": "villain"} for a in dub.assign]
    dub.save()
    run(["qa", "--project", str(dub.project.root), "--force"])
    second = json.loads(path.read_text(encoding="utf-8"))["lines"][short]
    assert second["against"].startswith("lines:villain:") and second["orig_sim"] < first["orig_sim"]
    assert latest_by(read_jsonl(dub.project.path(layout.QA)))[short]["orig_sim"] == second["orig_sim"]


def test_qa_atypical_original_is_judged_line_to_line_without_itself(dub, fake_qc, run) -> None:
    from pathlib import Path

    from babeldub.qa.metrics import mean_cosine

    def key(path):
        return str(Path(path).resolve()).lower()

    v = dub.voices
    base = [dub.line("hero", 4.0) for _ in range(3)]  # typical clean originals, no chosen reference
    odd = dub.line("hero", 3.0, vec=v.mix({"hero": 0.2, "odd": 1.0}))  # clean and long, but atypical
    dub.translate(odd, "Садись в машину.")
    dub.dub(odd, vec=v.mix({"hero": 0.2, "odd_dub": 1.0}), asr="садись в машину")
    dub.save()
    root = str(dub.project.root)
    run(["qa", "--project", root])
    row = latest_by(read_jsonl(dub.project.path(layout.QA)))[odd]
    others = [fake_qc.vectors[key(dub.project.path(x["audio"]["wav"]))] for x in dub.lines if x["id"] in base]
    own = fake_qc.vectors[key(dub.project.path(next(x for x in dub.lines if x["id"] == odd)["audio"]["wav"]))]
    # line to line like sim_ref, the line itself left out: a centroid that contains the line read it as typical
    assert row["orig_sim"] == round(mean_cosine(own, others), 4) and row["orig_sim"] < row["sim_threshold"]
    assert row["sim_rule"] == "relative" and row["sim"] < row["sim_threshold"]
    assert row["sim_rel"] >= 0.8 and row["verdict"] == "pass"
    path = dub.project.path("qa/baseline.json")
    assert json.loads(path.read_text(encoding="utf-8"))["lines"][odd]["against"].startswith("lines:hero:")

    # a value cached against the old centroid target is not reused
    data = json.loads(path.read_text(encoding="utf-8"))
    data["lines"][odd] = {"orig_sim": 0.9, "against": "centroid:hero:000000000000"}
    path.write_text(json.dumps(data), encoding="utf-8")
    run(["qa", "--project", root, "--force"])
    assert latest_by(read_jsonl(dub.project.path(layout.QA)))[odd]["orig_sim"] == row["orig_sim"]


def test_qa_pitch_register_check(dub, fake_qc, run) -> None:
    import math
    from pathlib import Path

    from babeldub.core.jsonl import write_jsonl

    def key_of(path):
        return str(Path(path).resolve()).lower()

    base = [dub.line("hero", 4.0) for _ in range(3)]
    dub.ref("hero", base[0])
    cases = {"shifted": (200.0, 1.0, 140.0, 1.0), "close": (148.0, 1.0, 140.0, 1.0),
             "too_short": (200.0, 1.0, 140.0, 0.2)}
    ids = {}
    for name, (dub_hz, dub_voiced, orig_hz, orig_voiced) in cases.items():
        lid = ids[name] = dub.line("hero", 3.0)
        dub.translate(lid, "Садись в машину.")
        wav = dub.dub(lid, voice="hero", asr="садись в машину")
        fake_qc.prosody[key_of(wav)] = {"f0_median_hz": dub_hz, "voiced_s": dub_voiced, "f0_range_st": 5.0}
        orig = dub.project.path(next(x for x in dub.lines if x["id"] == lid)["audio"]["wav"])
        fake_qc.prosody[key_of(orig)] = {"f0_median_hz": orig_hz, "voiced_s": orig_voiced, "f0_range_st": 5.0}
    dub.save()
    write_jsonl(dub.project.path(layout.ANALYSIS),
                [{"id": lid, "prosody": {"f0_median_hz": hz}} for lid, hz in zip(base, (140.0, 150.0, 160.0), strict=True)],
                schema="babeldub.analysis/1")
    code, out = run(["qa", "--project", str(dub.project.root)])
    qa = latest_by(read_jsonl(dub.project.path(layout.QA)))
    assert qa[ids["shifted"]]["reasons"] == ["pitch"] and qa[ids["shifted"]]["pitch_shift_st"] == round(12 * math.log2(200 / 140), 2)
    assert qa[ids["close"]]["verdict"] == "pass" and abs(qa[ids["close"]]["pitch_shift_st"]) < 1.0
    assert qa[ids["too_short"]]["pitch_shift_st"] is None and qa[ids["too_short"]]["verdict"] == "pass"
    assert qa[ids["shifted"]]["f0_median_hz"] == 200.0 and qa[ids["shifted"]]["orig_f0_median_hz"] == 140.0
    dec = latest_by(read_jsonl(dub.project.path(layout.DECISIONS)))
    assert dec[ids["shifted"]]["decision"] == "regen"
    shifts = [abs(qa[ids[n]]["pitch_shift_st"]) for n in ("shifted", "close")]
    assert out["pitch"] == {"checked": 2, "over": 1, "median_abs_st": round(sum(shifts) / 2, 2)}
    hints = {r.get("f0_hint_hz") for r in fake_qc.requests if r["op"] == "prosody"}
    assert hints == {150.0}  # the character's median F0 from analysis.jsonl bounds every pyin pass
    # both passes reach 4 x the hint (an octave-up take or a shouted original is not read as its subharmonic)
    passes = [(Path(r["wav"]).as_posix(), r.get("f0_up")) for r in fake_qc.requests if r["op"] == "prosody"]
    assert {up for wav, up in passes if f"/{layout.POST_DIR}/" in wav} == {4.0}
    assert {up for wav, up in passes if f"/{layout.ORIG_AUDIO_DIR}/" in wav} == {4.0}


def test_qa_register_check_measures_flagged_originals_and_never_judges_the_analysis_fallback(dub, fake_qc, run) -> None:
    from pathlib import Path

    from babeldub.core.jsonl import write_jsonl

    def key_of(path):
        return str(Path(path).resolve()).lower()

    base = [dub.line("hero", 4.0) for _ in range(3)]
    dub.ref("hero", base[0])
    bed = dub.line("hero", 3.0, flags=("has_bed",))  # a music bed: no voice sample, but its F0 is measurable
    unmeasured = dub.line("hero", 3.0)  # the worker cannot measure this original
    for lid in (bed, unmeasured):
        dub.translate(lid, "Садись в машину.")
        wav = dub.dub(lid, voice="hero", asr="садись в машину")
        fake_qc.prosody[key_of(wav)] = {"f0_median_hz": 200.0, "voiced_s": 1.0, "f0_range_st": 5.0}
    bed_orig = dub.project.path(next(x for x in dub.lines if x["id"] == bed)["audio"]["wav"])
    fake_qc.prosody[key_of(bed_orig)] = {"f0_median_hz": 140.0, "voiced_s": 1.0, "f0_range_st": 5.0}
    dub.save()
    # unhinted analysis prosody: it agrees with the dub on the bed line and disagrees on the unmeasured one
    analysis = [{"id": lid, "prosody": {"f0_median_hz": hz}} for lid, hz in zip(base, (140.0, 150.0, 160.0), strict=True)]
    analysis += [{"id": bed, "prosody": {"f0_median_hz": 200.0, "voiced_s": 1.0, "f0_range_st": 5.0}},
                 {"id": unmeasured, "prosody": {"f0_median_hz": 120.0, "voiced_s": 1.0, "f0_range_st": 5.0}}]
    write_jsonl(dub.project.path(layout.ANALYSIS), analysis, schema="babeldub.analysis/1")
    run(["qa", "--project", str(dub.project.root)])
    qa = latest_by(read_jsonl(dub.project.path(layout.QA)))
    # the has_bed original is measured from its own wav with the character's hint, not taken from analysis.jsonl
    assert qa[bed]["orig_f0_median_hz"] == 140.0 and qa[bed]["reasons"] == ["pitch"]
    orig_pass = [r for r in fake_qc.requests if r["op"] == "prosody" and key_of(r["wav"]) == key_of(bed_orig)]
    assert orig_pass and all(r.get("f0_hint_hz") == 150.0 and r.get("f0_up") == 4.0 for r in orig_pass)
    # the fallback still feeds the prosody similarity, but an unhinted median gives no register verdict
    assert qa[unmeasured]["orig_f0_median_hz"] == 120.0 and qa[unmeasured]["pitch_shift_st"] is None
    assert qa[unmeasured]["verdict"] == "pass"


def test_voice_checks_use_the_take_before_the_channel_match(dub, fake_qc, run) -> None:
    from pathlib import Path

    import numpy as np
    import soundfile as sf

    def key_of(path):
        return str(Path(path).resolve()).lower()

    for _ in range(3):
        dub.line("hero", 4.0)
    radio = dub.line("hero", 3.0)
    plain = dub.line("hero", 3.0)
    for lid in (radio, plain):
        dub.translate(lid, "Привет, как дела?")
    final = dub.dub(radio, asr="привет как дела", vec=dub.voices.emb("villain"))  # the radio chain: another "voice"
    pre = dub.project.path("post/pre_match/radio.wav")
    pre.parent.mkdir(parents=True)
    sf.write(str(pre), (0.2 * np.sin(np.arange(3 * 16000) * 0.07)).astype(np.float32), 16000)
    fake_qc.vectors[key_of(pre)] = dub.voices.emb("hero")
    dub.post[-1]["pre_match_wav"] = "post/pre_match/radio.wav"
    dub.dub(plain, voice="hero", asr="привет как дела")
    dub.save()
    code, out = run(["qa", "--project", str(dub.project.root)])
    qa = latest_by(read_jsonl(dub.project.path(layout.QA)))
    assert qa[radio]["verdict"] == "pass" and qa[radio]["voice_wav"] == "post/pre_match/radio.wav", qa[radio]
    assert qa[plain]["voice_wav"] is None
    embedded = [key_of(r["wav"]) for r in fake_qc.requests if r["op"] == "embed" and "#a" in str(r.get("id"))]
    heard = [key_of(r["wav"]) for r in fake_qc.requests if r["op"] == "asr"]
    assert key_of(pre) in embedded and key_of(final) not in embedded  # SIM on the take before the match ...
    assert key_of(final) in heard and key_of(pre) not in heard  # ... the words on what the player hears
    assert isinstance(qa[radio]["ltas_db"], float) and qa[radio]["ltas_db"] >= 0.0  # informational, never a reason
    assert "ltas" not in " ".join(qa[radio]["reasons"])
