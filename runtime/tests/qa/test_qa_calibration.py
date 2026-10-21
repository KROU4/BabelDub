"""Cross-language similarity ratio, absolute floor, nonverbal skips and the speaker identity margin."""

from __future__ import annotations

from babeldub.core import layout
from babeldub.core.jsonl import latest_by, read_jsonl
from babeldub.core.output import EXIT_OK


def _hero(dub, n: int = 4) -> list[str]:
    base = [dub.line("hero", 4.0) for _ in range(n)]  # clean originals: baseline, centroid, reference
    dub.ref("hero", base[0])
    return base


def _qa(dub) -> dict:
    return latest_by(read_jsonl(dub.project.path(layout.QA)))


def _set(dub, section: str, key: str, value) -> None:
    dub.project.data.setdefault(section, {})[key] = value
    dub.project.save()


def test_cross_language_ratio_and_absolute_floor(dub, run) -> None:
    _hero(dub)
    mid, low = dub.line("hero", 4.0), dub.line("hero", 4.0)
    for lid in (mid, low):
        dub.translate(lid, "Привет.")
    dub.dub(mid, asr="привет", vec=dub.voices.mix({"hero": 0.58, "other": 0.81}))
    dub.dub(low, asr="привет", vec=dub.voices.mix({"hero": 0.17, "other": 0.98}))
    dub.save()
    root = str(dub.project.root)

    # English originals, Russian dub: the cross-language ratio applies
    code, out = run(["qa", "--project", root])
    assert code == EXIT_OK, out
    qa = _qa(dub)
    m = qa[mid]
    assert m["sim_ratio"] == 0.45 and m["sim_threshold"] == round(0.45 * m["sim_ref"], 4)
    assert 0.45 * m["sim_ref"] < m["sim"] < 0.75 * m["sim_ref"]  # the same-language rule would fail it
    assert m["verdict"] == "pass" and m["sim_skipped"] is None and m["cer_skipped"] is None
    assert qa[low]["reasons"] == ["sim"]

    # a same-language dub keeps the stricter ratio
    dub.project.data["source_lang"] = "ru"
    dub.project.save()
    run(["qa", "--force", "--project", root])
    m = _qa(dub)[mid]
    assert m["sim_ratio"] == 0.75 and m["reasons"] == ["sim"]

    # the absolute floor holds even when the ratio is set very low
    dub.project.data["source_lang"] = "en"
    _set(dub, "qa", "min_sim_ratio_xlang", 0.05)
    run(["qa", "--force", "--project", root])
    lo = _qa(dub)[low]
    assert lo["sim_threshold"] == 0.25 and 0.05 * lo["sim_ref"] < lo["sim"] < 0.25
    assert lo["reasons"] == ["sim"]
    report_code, _ = run(["report", "--project", root])
    assert report_code == EXIT_OK
    report = dub.project.path(layout.QA_REPORT).read_text(encoding="utf-8")
    assert "max(0.05 × sim_ref, 0.25)" in report and "nonverbal" in report


def test_nonverbal_lines_skip_voice_and_cer(dub, run) -> None:
    _hero(dub)
    scream = dub.line("hero", 2.0, flags=("nonverbal",))
    dub.translate(scream, "А-а-а!")
    dub.dub(scream, voice="stranger", asr="")
    dub.save()
    code, out = run(["qa", "--project", str(dub.project.root)])
    assert code == EXIT_OK and out["accept"] == 1, out
    q = _qa(dub)[scream]
    assert q["verdict"] == "pass" and q["reasons"] == []
    assert q["sim_skipped"] == "nonverbal" and q["cer_skipped"] == "nonverbal"
    assert q["cer"] == 1.0 and q["sim"] < q["sim_threshold"]  # measured and recorded, not judged
    assert q["sim_line"] is None  # a scream's original is never a voice sample


def test_identity_margin_catches_another_characters_voice(dub, run) -> None:
    _hero(dub)
    for _ in range(4):
        dub.line("villain", 4.0)
    good, swapped = dub.line("hero", 4.0), dub.line("hero", 4.0)
    for lid in (good, swapped):
        dub.translate(lid, "Привет.")
    dub.dub(good, voice="hero", asr="привет")
    dub.dub(swapped, asr="привет", vec=dub.voices.mix({"hero": 0.5, "villain": 0.85}))
    dub.save()
    code, out = run(["qa", "--project", str(dub.project.root)])
    assert code == EXIT_OK and out["regen"] == 1, out
    qa = _qa(dub)
    g, s = qa[good], qa[swapped]
    assert g["verdict"] == "pass" and g["closest_char"] == "hero" and g["margin"] > 0.3
    assert s["reasons"] == ["identity"] and s["closest_char"] == "villain" and s["margin"] < -0.05
    assert s["sim"] >= s["sim_threshold"]  # similarity alone would have let it through
    dec = latest_by(read_jsonl(dub.project.path(layout.DECISIONS)))
    assert dec[swapped]["decision"] == "regen" and "identity" in dec[swapped]["note"]

    # a lower tolerance lets it pass
    _set(dub, "qa", "min_identity_margin", -1.0)
    run(["qa", "--force", "--project", str(dub.project.root)])
    assert _qa(dub)[swapped]["verdict"] == "pass"


def test_same_voice_cluster_is_not_another_character(dub, run) -> None:
    _hero(dub)
    for _ in range(4):
        dub.line("villain", 4.0)
    # a voice cluster of the hero left unmerged (cast before merging, or one actor in two roles)
    hero_like = dub.voices.mix({"hero": 1.0, "echo": 0.5})
    for _ in range(3):
        dub.line("cluster_04", 4.0, vec=hero_like)
    lid = dub.line("hero", 4.0)
    dub.translate(lid, "Привет.")
    dub.dub(lid, asr="привет", vec=hero_like)  # sits right on the cluster's centroid
    dub.save()
    code, out = run(["qa", "--project", str(dub.project.root)])
    assert code == EXIT_OK, out
    q = _qa(dub)[lid]
    assert q["closest_char"] == "cluster_04"
    assert q["verdict"] == "pass" and q["margin"] > 0.5  # measured against the villain, not the hero's own cluster

    # with the same-voice bar above any cosine the cluster counts as another character and fails the line
    _set(dub, "cast", "merge_threshold", 1.0)
    run(["qa", "--force", "--project", str(dub.project.root)])
    q = _qa(dub)[lid]
    assert q["reasons"] == ["identity"] and q["margin"] < -0.05
