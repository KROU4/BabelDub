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
    assert mos_base["hero"] == {"median": 3.5, "n": 8}
