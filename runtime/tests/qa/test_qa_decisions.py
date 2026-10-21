from __future__ import annotations

from typing import Any

from babeldub.qa.decisions import Thresholds, auto_decide, evaluate, needs_attention, validate_agent_row

TH = Thresholds(max_cer=0.15, min_sim_ratio=0.75, max_duration_ratio=1.35, fixed_length_tolerance=0.03,
                peak_ceiling_db=-1.0, max_attempts=3)


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
