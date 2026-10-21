from __future__ import annotations

import pytest

from babeldub.adapters.fmod import heuristics as H


@pytest.mark.parametrize(
    ("stem", "has_audio", "role"),
    [
        ("Dialogue_en-US", True, "voice"),
        ("VO_French", True, "voice"),
        ("Narration", True, "voice"),
        ("Volcano", True, "audio"),
        ("Music", True, "audio"),
        ("Master.strings", False, "other"),
    ],
)
def test_bank_role(stem: str, has_audio: bool, role: str) -> None:
    assert H.bank_role(stem, has_audio) == role


@pytest.mark.parametrize(
    ("stem", "locale"),
    [
        ("Dialogue_en-US", "en-US"),
        ("VO_fr", "fr"),
        ("Dialogue_pl_PL", "pl-PL"),
        ("Dialogue_zh-Hans", "zh-Hans"),
        ("Master", None),
        ("Weapons_big", None),
        ("SFX_UI", None),
    ],
)
def test_locale_from_name(stem: str, locale: str | None) -> None:
    assert H.locale_from_name(stem) == locale


def test_locale_from_siblings() -> None:
    sibs = ["Dialogue_PRC", "Dialogue_FRE", "Dialogue_GER"]
    assert H.locale_from_siblings("Dialogue_FRE", sibs) == "FRE"
    assert H.locale_from_siblings("Dialogue_FRE", ["Dialogue_FRE"]) is None


@pytest.mark.parametrize(
    ("name", "event", "kind", "fixed"),
    [
        (
            "dlg_a1_m01_d03_Zbyszek_01",
            "event:/Dialogue/Act1/Zbyszek/dlg_a1_m01_d03_Zbyszek_01",
            "dialogue",
            False,
        ),
        (
            "dlg_a2_m21_cs00_i00_Intro",
            "event:/Cutscenes/a2_m21_cs00_Intro/dlg_a2_m21_cs00_i00_Intro",
            "cutscene",
            True,
        ),
        ("sfx_car_door", "event:/Sound Effects/Cutscenes/Ending/sfx_car_door", "sfx", False),
        ("119300-Sticks_drop_on_dirt_2", None, "sfx", False),
        ("video_Cutscene_Intro", None, "cutscene", True),
        ("msc_Stopwatch", None, "music", False),
        ("guard_bark_03", "event:/NPC/Barks/guard_bark_03", "bark", False),
        ("hero_line_042", "event:/VO/Hero/hero_line_042", "dialogue", False),
        ("mystery_take", None, "unknown", False),
    ],
)
def test_classify_in_voice_bank(name: str, event: str | None, kind: str, fixed: bool) -> None:
    assert H.classify(name, event, "Dialogue_en-US", "voice") == (kind, fixed)


def test_classify_audio_bank_falls_back_to_bank_name() -> None:
    assert H.classify("track_01", None, "Music", "audio") == ("music", False)
    assert H.classify("wind_loop", None, "Ambience", "audio") == ("ambience", False)
    assert H.classify("thing", None, "Stuff", "audio") == ("sfx", False)


def test_speaker_and_scene_hints() -> None:
    assert H.speaker_hint("event:/Dialogue/Act1/Zbyszek/dlg_x") == "Zbyszek"
    assert H.speaker_hint("event:/Dialogue/Bonus/dlg_x") is None
    assert H.speaker_hint("event:/Cutscenes/a2_m21_cs00_Intro/dlg_x") is None
    assert H.speaker_hint("event:/Dialogue/Act1/dlg_x") is None
    assert H.speaker_hint(None) is None
    assert H.scene_hint("dlg_a1_m01_d03_Zbyszek_01", None, "dialogue") == "a1_m01"
    assert H.scene_hint("line", "event:/Cutscenes/Intro_Scene/line", "cutscene") == "Intro_Scene"
    assert H.scene_hint("line", "event:/VO/Chapter3/Bob/line", "dialogue") == "Chapter3"
    assert H.scene_hint("dlg_a1_m01", None, "sfx") is None


def test_line_flags() -> None:
    assert "alt_take" in H.line_flags("dlg_a1_m01_d00_v2", "dialogue")
    assert "alt_take" in H.line_flags("dlg_x_BEST", "dialogue")
    assert "nonverbal" in H.line_flags("Hobo_01_Hmmm", "dialogue")
    assert "nonverbal" in H.line_flags("dlg_a2_m18_d10.i00_ChiefGroan_SFX", "dialogue")
    assert "has_bed" in H.line_flags("video_Intro", "cutscene")
    assert H.line_flags("dlg_a1_m01_d00", "dialogue") == []
