from __future__ import annotations

from typing import Any

from babeldub.qa.decisions import (
    Thresholds,
    auto_decide,
    evaluate,
    is_cross_language,
    needs_attention,
    validate_agent_row,
)

# a same-language dub: the classic ratio rule sim >= 0.75 x sim_ref
TH = Thresholds(max_cer=0.15, min_sim_ratio=0.75, max_duration_ratio=1.35, fixed_length_tolerance=0.03,
                peak_ceiling_db=-1.0, max_attempts=3, cross_lang=False)
XLANG = Thresholds()  # defaults: Russian dub of English originals


def _eval(**over: Any) -> tuple[str, list[str]]:
    kw: dict[str, Any] = dict(th=TH, exists=True, dur_s=2.0, active_rms_db=-20.0, peak_db=-3.0, clip_frac=0.0,
                              has_text=True, cer=0.05, sim=0.6, sim_ref=0.7, ratio=1.1, fixed_length=False)
    kw.update(over)
    return evaluate(**kw)


def test_thresholds_from_project() -> None:
    th = Thresholds.from_project({"qa": {"max_cer": 0.1, "min_sim_ratio": 0.8, "max_duration_ratio": 1.2},
                                  "post": {"fixed_length_tolerance": 0.05, "true_peak_db": -2.0},
                                  "tts": {"max_attempts": 5}})
    assert (th.max_cer, th.min_sim_ratio, th.max_duration_ratio) == (0.1, 0.8, 1.2)
    assert (th.fixed_length_tolerance, th.peak_ceiling_db, th.max_attempts) == (0.05, -2.0, 5)
    assert Thresholds.from_project({}).max_cer == 0.15
    th = Thresholds.from_project({"source_lang": "en", "target_lang": "ru",
                                  "qa": {"min_sim_ratio_xlang": 0.5, "min_sim_abs": 0.2, "min_identity_margin": -0.1}})
    assert th.cross_lang and th.sim_ratio == 0.5
    assert (th.min_sim_abs, th.min_identity_margin) == (0.2, -0.1)
    same = Thresholds.from_project({"source_lang": "pt", "target_lang": "pt-BR"})
    assert not same.cross_lang and same.sim_ratio == 0.75


def test_cross_language() -> None:
    assert is_cross_language("en", "ru")
    assert is_cross_language(None, "ru")  # unknown source: the normal case is a dub into another language
    assert not is_cross_language("en", "en")
    assert not is_cross_language("pt_BR", "PT")


def test_sim_threshold_ratio_and_floor() -> None:
    # Low-Budget Repairs: sim_ref 0.76; good cross-language dubs 0.38-0.66, failures 0.08-0.11
    assert XLANG.sim_threshold(0.76) == 0.45 * 0.76
    assert TH.sim_threshold(0.76) == 0.75 * 0.76
    assert XLANG.sim_threshold(0.4) == 0.25  # the absolute floor
    assert XLANG.sim_threshold(None) == 0.25
    for good in (0.38, 0.5, 0.66):
        assert _eval(th=XLANG, sim=good, sim_ref=0.76) == ("pass", [])
    for good in (0.38, 0.5):
        assert _eval(th=TH, sim=good, sim_ref=0.76) == ("fail", ["sim"])  # the same-language bar is 0.57
    for bad in (0.08, 0.11, 0.24):
        assert _eval(th=XLANG, sim=bad, sim_ref=0.76) == ("fail", ["sim"])
    assert _eval(th=XLANG, sim=0.2, sim_ref=0.3) == ("fail", ["sim"])  # 0.45 x 0.3 = 0.135, but the floor is 0.25


def test_identity_margin_and_nonverbal() -> None:
    assert _eval(margin=0.1) == ("pass", [])
    assert _eval(margin=-0.04) == ("pass", [])  # within the default -0.05 tolerance
    assert _eval(margin=-0.2) == ("fail", ["identity"])
    assert _eval(margin=None) == ("pass", [])
    assert _eval(sim=0.1, margin=-0.3) == ("fail", ["sim", "identity"])
    # a scream: neither the voice nor the transcript is judged
    assert _eval(nonverbal=True, sim=0.09, margin=-0.3, cer=0.9) == ("pass", [])
    assert _eval(nonverbal=True, sim=0.09, ratio=1.6) == ("fail", ["duration"])
    assert _eval(nonverbal=True, has_text=False, cer=None) == ("fail", ["no_dub_text"])


def test_evaluate_rules() -> None:
    assert _eval() == ("pass", [])
    assert _eval(cer=0.3) == ("fail", ["cer"])
    # sim is judged relative to the character's own baseline: 0.75 * 0.7 = 0.525
    assert _eval(sim=0.53) == ("pass", [])
    assert _eval(sim=0.50) == ("fail", ["sim"])
    assert _eval(sim=None) == ("pass", [])
    assert _eval(ratio=1.36) == ("fail", ["duration"])
    assert _eval(ratio=1.05, fixed_length=True) == ("fail", ["duration"])
    assert _eval(ratio=1.02, fixed_length=True) == ("pass", [])
    assert _eval(ratio=0.5) == ("pass", [])  # shorter is fine: post pads the timing
    assert _eval(peak_db=-0.2) == ("fail", ["peak"])
    assert _eval(clip_frac=0.01) == ("fail", ["peak"])
    assert _eval(has_text=False, cer=None) == ("fail", ["no_dub_text"])
    assert _eval(exists=False) == ("fail", ["missing_file"])
    assert _eval(dur_s=0.0) == ("fail", ["empty"])
    assert _eval(active_rms_db=-70.0) == ("fail", ["silent"])
    assert _eval(qc_error=True, cer=None, sim=None) == ("fail", ["qc_error"])
    assert _eval(cer=0.5, sim=0.1, ratio=2.0) == ("fail", ["cer", "sim", "duration"])


def test_auto_decisions() -> None:
    assert auto_decide("pass", [], 1, TH).decision == "accept"
    d = auto_decide("fail", ["cer"], 1, TH)
    assert d.decision == "regen" and not d.attention
    d = auto_decide("fail", ["sim"], 2, TH)
    assert d.decision == "regen"
    d = auto_decide("fail", ["sim"], 3, TH)
    assert d.decision == "keep_original" and d.attention
    assert auto_decide("fail", ["identity"], 1, TH).decision == "regen"
    d = auto_decide("fail", ["identity"], 3, TH)
    assert d.decision == "keep_original" and d.attention
    d = auto_decide("fail", ["duration"], 1, TH)
    assert d.decision == "shorten" and d.attention
    # quality first while attempts remain: a hallucination also makes the line too long
    assert auto_decide("fail", ["cer", "duration"], 1, TH).decision == "regen"
    assert auto_decide("fail", ["cer", "duration"], 3, TH).decision == "shorten"
    d = auto_decide("fail", ["duration"], 2, TH, shortened=2)
    assert d.decision == "keep_original" and d.attention
    d = auto_decide("fail", ["no_dub_text"], 1, TH)
    assert d.decision is None and d.attention
    assert auto_decide("fail", ["qc_error", "cer"], 1, TH).decision is None
    assert auto_decide("fail", ["peak"], 1, TH).decision == "regen"
    assert auto_decide("fail", ["silent"], 3, TH).decision == "keep_original"


def test_needs_attention() -> None:
    assert needs_attention(None, "fail")
    assert not needs_attention(None, "pass")
    assert needs_attention({"by": "auto", "attention": True}, "fail")
    assert not needs_attention({"by": "auto", "attention": False}, "fail")
    assert not needs_attention({"by": "agent", "attention": True}, "fail")


def test_validate_agent_row() -> None:
    known = {"a", "b"}
    assert validate_agent_row({"id": "a", "decision": "keep_original", "note": "ok"}, known) == []
    assert validate_agent_row({"id": "a", "decision": "accept", "attempt": 2}, known) == []
    probs = validate_agent_row({"id": "zzz", "decision": "approve", "attempt": 0, "note": 5}, known)
    assert len(probs) == 4
    assert validate_agent_row({"decision": "skip"}, known) == ["missing 'id'"]
    assert validate_agent_row({"id": "a", "decision": "skip", "attempt": True}, known) == ["attempt must be a positive integer"]


def test_auto_decide_line_classes() -> None:
    mixed = auto_decide("pass", [], 1, TH, line_class="mixed")
    assert mixed.decision == "accept" and mixed.attention and "mixed" in mixed.note
    assert auto_decide("fail", ["cer"], 1, TH, line_class="short_word", max_attempts=2).decision == "regen"
    last = auto_decide("fail", ["cer"], 2, TH, line_class="short_word", max_attempts=2)
    assert last.decision == "keep_original" and last.attention


def test_mos_flat_and_timing_reasons() -> None:
    th = Thresholds.from_project({"qa": {"mos_drop": 0.8, "min_f0_range_ratio": 0.6}})
    assert th.mos_drop == 0.8 and th.min_f0_range_ratio == 0.6 and th.max_onset_s == 0.08
    assert XLANG.low_mos(2.4, 3.5) and not XLANG.low_mos(2.6, 3.5) and not XLANG.low_mos(None, 3.5)
    assert XLANG.flat(2.0, 6.0) and not XLANG.flat(4.0, 6.0) and not XLANG.flat(0.5, 2.0)  # a flat original
    assert XLANG.off_time([0.0, 0.1], [0.0]) and XLANG.off_time([0.0], [0.09])
    assert not XLANG.off_time([-0.05], [-0.4])  # ending early is fine
    assert _eval(low_mos=True, flat=True)[1] == ["mos", "flat"]
    assert _eval(low_mos=True, nonverbal=True)[1] == []
    assert _eval(off_time=True)[1] == [] and _eval(off_time=True, fixed_length=True, ratio=1.0)[1] == ["timing"]
    for reason in ("mos", "flat", "timing"):
        assert auto_decide("fail", [reason], 1, TH).decision == "regen"
        assert auto_decide("fail", [reason], 3, TH).decision == "keep_original"
