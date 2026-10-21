from __future__ import annotations

from pathlib import Path

import numpy as np

from babeldub.cast.characters import (
    UNKNOWN_ID,
    apply_policy,
    assign_tiers,
    carry_labels,
    char_id_for,
    hint_name,
    new_character,
    render_casting_sheet,
    validate_label,
    voiced_lines,
)
from babeldub.cast.refs import candidate_pool, rank_candidates


def _chars(speech: list[float]) -> list[dict]:
    return [new_character(f"c{i}", f"Char {i}", "metadata", 10, s, 1.0) for i, s in enumerate(speech)]


def test_tiers_main_covers_share_then_secondary_and_minor() -> None:
    chars = _chars([500, 250, 150, 60, 30, 10])  # shares 50/25/15/6/3/1 %
    assign_tiers(chars, main_share=0.7)
    tiers = [c["tier"] for c in chars]
    assert tiers == ["main", "main", "secondary", "secondary", "secondary", "minor"]
    assert abs(sum(c["share"] for c in chars) - 1.0) < 1e-3


def test_tiers_single_dominant_character_and_unknown_never_ranks() -> None:
    chars = _chars([900, 50, 50])
    chars.append(new_character(UNKNOWN_ID, "unknown", "unknown", 40, 5000, None))
    assign_tiers(chars, main_share=0.7)
    assert [c["tier"] for c in chars] == ["main", "secondary", "secondary", "minor"]
    assert chars[3]["share"] == 0.0


def test_tiers_fall_back_to_line_counts_without_durations() -> None:
    chars = _chars([0, 0])
    chars[0]["n_lines"], chars[1]["n_lines"] = 3, 30
    assign_tiers(chars, main_share=0.7)
    assert chars[1]["tier"] == "main" and chars[0]["tier"] == "secondary"


def test_policy_all_main_list() -> None:
    chars = _chars([500, 250, 150])
    chars[2]["name_tgt"] = "Тадеуш"
    assign_tiers(chars, 0.7)
    assert apply_policy(chars, "all", []) == []
    assert all(c["selected"] for c in chars)
    apply_policy(chars, "main", [])
    assert [c["selected"] for c in chars] == [True, True, False]
    unmatched = apply_policy(chars, "list", ["c0", "тадеуш", "Nobody"])
    assert unmatched == ["Nobody"]
    assert [c["selected"] for c in chars] == [True, False, True]


def test_hints_and_ids() -> None:
    assert hint_name({"speaker_hint": "  Old   Man "}) == "Old Man"
    assert hint_name({"speaker_hint": "Unknown"}) is None
    assert hint_name({"speaker_hint": None}) is None
    assert char_id_for("Old Man") == "old-man"
    assert char_id_for("Zbyszek") == "zbyszek"
    lines = [
        {"id": "a", "kind": "dialogue", "flags": []},
        {"id": "b", "kind": "sfx", "flags": []},
        {"id": "c", "kind": "bark", "flags": ["no_audio"]},
        {"id": "d", "kind": "cutscene"},
    ]
    assert [x["id"] for x in voiced_lines(lines)] == ["a", "d"]


def test_label_validation_and_carry() -> None:
    known = {"cluster_01", "zbyszek"}
    assert validate_label({"char_id": "cluster_01", "name": "Maciek", "gender": "m", "age": "adult"}, known) == []
    problems = validate_label({"char_id": "cluster_09", "gender": "x", "sex": "m"}, known)
    assert any("unknown char_id" in p for p in problems)
    assert any("gender" in p for p in problems)
    assert any("unknown fields" in p for p in problems)
    chars = [new_character("cluster_01", "cluster_01", "cluster", 3, 9.0, 0.8)]
    carry_labels(chars, {"cluster_01": {"labeled": True, "name": "Maciek", "gender": "m", "name_tgt": "Мачек"}})
    assert chars[0]["name"] == "Maciek" and chars[0]["gender"] == "m" and chars[0]["labeled"]


def test_casting_sheet_is_russian_table() -> None:
    chars = _chars([300, 100])
    assign_tiers(chars, 0.7)
    apply_policy(chars, "main", [])
    sheet = render_casting_sheet(
        game_name="Demo",
        target_lang="ru",
        policy="main",
        policy_list=[],
        main_share=0.7,
        chars=chars,
        refs_by_char={"c0": [{"wav": "cast/refs/c0/x.wav", "dur_s": 6.0, "chosen": True}]},
        samples={"c0": [{"id": "l1", "text": "Hello | there", "dur_s": 2.0}]},
        uncertain=[],
        warnings=["something"],
    )
    assert sheet.startswith("# Лист кастинга — Demo")
    assert "| Персонаж |" in sheet and "Минут речи" in sheet and "Референс" in sheet
    assert "cast/refs/c0/x.wav" in sheet
    assert "Hello \\| there" in sheet
    assert "labels.agent.jsonl" in sheet


def test_reference_candidates_rules(tmp_path: Path) -> None:
    import soundfile as sf

    sr = 16000

    def mk(name: str, dur: float, amp: float = 0.2, flags: tuple[str, ...] = (), text: str | None = "Text.") -> dict:
        t = np.arange(int(dur * sr)) / sr
        sig = amp * np.sin(2 * np.pi * 200 * t)
        if amp >= 1.0:
            sig = np.clip(sig, -1, 1)
        sf.write(str(tmp_path / f"{name}.wav"), sig.astype(np.float32), sr)
        return {
            "id": name,
            "uid": name,
            "audio": {"dur_s": dur, "wav": f"{name}.wav"},
            "text": {"src": text} if text else None,
            "flags": list(flags),
        }

    lines = [
        mk("ideal", 7.0),
        mk("short", 1.0),
        mk("long", 30.0),
        mk("bed", 6.0, flags=("has_bed",)),
        mk("alt", 6.0, flags=("alt_take",)),
        mk("notext", 6.0, text=None),
        mk("clipped", 6.0, amp=1.5),
        mk("silent", 6.0, amp=0.0001),
        mk("ok5", 5.0),
    ]
    pool, relax = candidate_pool(tmp_path, lines, 3.0, 12.0)
    assert relax is None
    assert {c.line_id for c in pool} == {"ideal", "clipped", "silent", "ok5"}
    ranked = rank_candidates(pool)
    assert [c.line_id for c in ranked] == ["ideal", "ok5"]  # clipped and silent are rejected

    # with embeddings the most typical timbre wins over the "ideal" duration
    centroid = np.ones(8, np.float32)
    off = np.array([1, -1, 1, -1, 1, -1, 1, -1], np.float32)
    vecs = {"ideal": off, "ok5": centroid, "x1": centroid}
    extra = mk("x1", 4.0)
    pool, _ = candidate_pool(tmp_path, lines + [extra], 3.0, 12.0)
    ranked = rank_candidates(pool, lambda c: vecs.get(c.line_id))
    assert ranked[0].line_id in ("ok5", "x1")
    assert ranked[-1].line_id == "ideal"
    assert ranked[0].centroid_cos is not None


def test_reference_relaxation(tmp_path: Path) -> None:
    import soundfile as sf

    sr = 16000
    sig = (0.2 * np.sin(2 * np.pi * 200 * np.arange(2 * sr) / sr)).astype(np.float32)
    sf.write(str(tmp_path / "a.wav"), sig, sr)
    line = {"id": "a", "uid": "a", "audio": {"dur_s": 2.0, "wav": "a.wav"}, "text": {"src": "Hi there."}, "flags": []}
    pool, relax = candidate_pool(tmp_path, [line], 3.0, 12.0)
    assert relax == "duration" and [c.line_id for c in pool] == ["a"]
    line["text"] = None
    pool, relax = candidate_pool(tmp_path, [line], 3.0, 12.0)
    assert relax == "duration+no_text"
