"""A QA row counts only for the audio it checked: a `synth score` swap or a re-post makes it stale."""

from __future__ import annotations

import json
from pathlib import Path

from babeldub.core import layout
from babeldub.core.jsonl import latest_by, read_jsonl, write_json, write_jsonl
from babeldub.core.output import EXIT_OK
from babeldub.qa.decisions import qa_is_current

T0, T1, T2 = "2026-10-01T10:00:00+00:00", "2026-10-01T11:00:00+00:00", "2026-10-01T12:00:00+00:00"


def test_qa_is_current_rules() -> None:
    post = {"id": "x", "attempt": 1, "wav": "post/x.wav", "src_key": "k1", "ts": T1}
    row = {"id": "x", "attempt": 1, "wav": "post/x.wav", "src_key": "k1", "post_ts": T1, "ts": T2}
    assert qa_is_current(row, post)
    assert not qa_is_current(None, post) and not qa_is_current(row, None)
    assert not qa_is_current({**row, "attempt": 2}, post)
    assert not qa_is_current(row, {**post, "src_key": "k2"})  # `synth score` swapped the winner
    assert not qa_is_current(row, {**post, "ts": T2})  # post/vc redid the line
    # rows written before src_key/post_ts: stale only when the post row is newer than the check
    legacy = {"id": "x", "attempt": 1, "wav": "post/x.wav", "ts": T1}
    assert qa_is_current(legacy, {**post, "ts": T0})
    assert qa_is_current(legacy, {**post, "ts": T1})
    assert not qa_is_current(legacy, {**post, "src_key": "k2", "ts": T2})
    assert qa_is_current({k: v for k, v in legacy.items() if k != "ts"}, post)  # no time to compare


def test_score_swap_within_an_attempt_is_rechecked(dub, fake_qc, run) -> None:
    dub.line("hero", 4.0)
    lid = dub.line("hero", 4.0)
    dub.translate(lid, "Привет.")
    dub.dub(lid, voice="hero", asr="привет")
    dub.post[-1].update(src_key="k1", ts=T0)
    dub.save()
    root = str(dub.project.root)
    code, out = run(["qa", "--project", root])
    assert code == EXIT_OK and out["checked"] == 1
    row = latest_by(read_jsonl(dub.project.path(layout.QA)))[lid]
    assert row["src_key"] == "k1" and row["post_ts"] == T0
    assert run(["qa", "--project", root])[1]["checked"] == 0
    # `synth score` kept another candidate in the same attempt and file; post redid the line
    dub.post[-1].update(src_key="k2", ts=T1)
    dub.save()
    code, out = run(["qa", "--project", root])
    assert out["checked"] == 1 and latest_by(read_jsonl(dub.project.path(layout.QA)))[lid]["src_key"] == "k2"


def test_legacy_qa_rows_stay_current_until_post_changes(dub, fake_qc, run) -> None:
    dub.line("hero", 4.0)
    lid = dub.line("hero", 4.0)
    dub.translate(lid, "Привет.")
    dub.dub(lid, voice="hero", asr="привет")
    dub.post[-1].update(src_key="k1", ts=T0)
    dub.save()
    root = str(dub.project.root)
    assert run(["qa", "--project", root])[1]["checked"] == 1
    qa_path = dub.project.path(layout.QA)
    rows = [{k: v for k, v in r.items() if k not in ("src_key", "post_ts")} for r in read_jsonl(qa_path)]
    write_jsonl(qa_path, [{**r, "ts": T1} for r in rows], schema="babeldub.qa/1")  # written by an older version
    assert run(["qa", "--project", root])[1]["checked"] == 0  # post (T0) is older than the check
    dub.post[-1].update(src_key="k2", ts=T2)
    dub.save()
    assert run(["qa", "--project", root])[1]["checked"] == 1


def test_failed_mos_baseline_is_not_cached(dub, fake_qc, run, home) -> None:
    def key_of(path) -> str:
        return str(Path(path).resolve()).lower()

    (home / "models" / "utmos22-strong").mkdir(parents=True)
    (home / "models" / "utmos22-strong" / "utmos22_strong_step7459_v1.pt").write_bytes(b"x")
    base = [dub.line("hero", 4.0) for _ in range(3)]
    lid = dub.line("hero", 3.0)
    dub.translate(lid, "Привет, как дела?")
    fake_qc.mos[key_of(dub.dub(lid, voice="hero", asr="привет как дела"))] = 3.4
    dub.save()
    mos_path = dub.project.path("qa/mos_baseline.json")
    write_json(mos_path, {"hero": {"median": None, "n": 0}})  # cached by an older version after a failure
    root = str(dub.project.root)
    run(["qa", "--project", root])  # the originals' MOS fails again
    assert "hero" not in json.loads(mos_path.read_text(encoding="utf-8"))
    assert latest_by(read_jsonl(dub.project.path(layout.QA)))[lid]["orig_mos"] is None
    for other in [*base, lid]:
        line = next(x for x in dub.lines if x["id"] == other)
        fake_qc.mos[key_of(dub.project.path(line["audio"]["wav"]))] = 3.5
    run(["qa", "--force", "--project", root])  # measured on the next run
    assert json.loads(mos_path.read_text(encoding="utf-8"))["hero"]["median"] == 3.5
    assert latest_by(read_jsonl(dub.project.path(layout.QA)))[lid]["orig_mos"] == 3.5
