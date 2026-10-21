"""Line <-> text linking on synthetic manifests."""

from __future__ import annotations

from typing import Any

from babeldub.recon.link import link_lines, mark_alt_takes, norm_key, spoken_text


def line(ident: str, key: str, locale: str | None = "en-US", **entry: Any) -> dict[str, Any]:
    return {
        "id": ident,
        "uid": ident,
        "asset_id": "a",
        "entry": {"key": key, **entry},
        "kind": "dialogue",
        "locale": locale,
        "order": int(ident.rsplit(":", 1)[-1]) if ":" in ident else 0,
        "flags": [],
    }


def text(key: str, lang: str, value: str, table: str = "Dialogues", **extra: Any) -> dict[str, Any]:
    return {
        "key": key,
        "table": table,
        "locale": lang,
        "lang": lang,
        "text": value,
        "source": "test",
        **extra,
    }


def test_norm_key() -> None:
    assert norm_key("Dialogue/Act2/Zbyszek/dlg_a2_m16_d00.i00_AboutRoom") == "dlg_a2_m16_d00_i00_aboutroom"
    assert norm_key("event:/VO/Bob/bob_01") == "bob_01"
    assert norm_key("Bob_01_v2.ogg") == "bob_01"
    assert norm_key("bob_01_v3_BEST") == "bob_01"
    assert norm_key("bob_01-alt") == "bob_01"
    assert norm_key("dlg_a0_m00_d00(2).i00_Aunt") == "dlg_a0_m00_d00_i00_aunt"
    assert norm_key("dlg_a0-m05_d01_i03_Couch") == "dlg_a0_m05_d01_i03_couch"
    # a dot inside a key is not an extension unless the extension is a known one
    assert norm_key("x.i00_Name") == "x_i00_name"


def test_spoken_text() -> None:
    raw = "{ColorStart}{CharacterName}:{ColorEnd} Kid, repeat after me…"
    assert spoken_text(raw) == "Kid, repeat after me…"
    assert spoken_text("<i>Hello</i> <color=#ff0000>there</color>\\nfriend") == "Hello there friend"
    assert spoken_text("{i}Ren'Py{/i} line{w} two") == "Ren'Py line two"
    assert spoken_text("Keep {0} and {name}") == "Keep {0} and {name}"
    assert spoken_text("   ") is None and spoken_text(None) is None


def test_link_passes_and_official_target() -> None:
    lines = [
        line("l:0000", "dlg_01"),  # exact
        line("l:0001", "dlg_02_v2"),  # normalized (take suffix)
        line("l:0002", "Dialogue/Act1/dlg_03"),  # normalized (path prefix)
        line("l:0003", "0001", file="game/voice/bob_hello.ogg"),  # filename via text 'audio' field
        line("l:0004", "nothing_like_it"),  # unlinked
    ]
    texts = [
        text("dlg_01", "en", "{ColorStart}{CharacterName}:{ColorEnd} One."),
        text("dlg_01", "ru", "{ColorStart}{CharacterName}:{ColorEnd} Один."),
        text("dlg_02", "en", "Two."),
        text("dlg_02", "ru", "Два."),
        text("dlg_03", "en", "Three."),
        text("start_a1b2c3", "en", "Hello!", audio="voice/bob_hello.ogg"),
        text("UI_Title", "en", "Title", table="UI"),
    ]
    out, summary = link_lines(lines, texts, target_lang="ru")
    by_id = {r["id"]: r for r in out}
    assert by_id["l:0000"]["text"]["match"] == "exact"
    assert by_id["l:0000"]["text"]["src"] == "One."
    assert by_id["l:0000"]["text"]["tgt_official"] == "Один."
    assert by_id["l:0000"]["text"]["src_raw"].startswith("{ColorStart}")
    assert by_id["l:0001"]["text"]["key"] == "dlg_02" and by_id["l:0001"]["text"]["match"] == "normalized"
    assert by_id["l:0002"]["text"]["key"] == "dlg_03" and by_id["l:0002"]["text"]["tgt_official"] is None
    assert by_id["l:0003"]["text"]["key"] == "start_a1b2c3" and by_id["l:0003"]["text"]["match"] == "filename"
    assert "no_text" in by_id["l:0004"]["flags"] and by_id["l:0004"]["text"]["src"] is None
    assert summary["linked"] == 4 and summary["unlinked"] == 1
    assert summary["with_official_target"] == 2
    assert summary["src_lang"] == "en" and summary["target_lang"] == "ru"
    assert summary["by_match"] == {"exact": 1, "normalized": 2, "filename": 1}
    # re-linking after texts appear clears the stale no_text flag
    texts.append(text("nothing_like_it", "en", "Found later."))
    again, summary2 = link_lines(out, texts, target_lang="ru")
    assert "no_text" not in {f for r in again for f in r["flags"]}
    assert summary2["unlinked"] == 0


def test_src_lang_from_texts_when_audio_has_no_locale() -> None:
    lines = [line("l:0000", "k1", locale=None)]
    texts = [
        text("k1", "pl", "Raz"),
        text("k2", "pl", "Dwa"),
        text("k1", "en", "One"),
        text("k1", "ru", "Раз"),
    ]
    out, summary = link_lines(lines, texts, target_lang="ru")
    assert out[0]["text"]["src_lang"] == "pl" and out[0]["text"]["src"] == "Raz"
    # the project's known source language wins over text statistics
    out, _ = link_lines(lines, texts, target_lang="ru", source_lang="en")
    assert out[0]["text"]["src"] == "One"


def test_dialogue_tables_win_key_collisions() -> None:
    lines = [line("l:0000", "intro")]
    texts = [
        text("intro", "en", "Menu intro", table="UI"),
        text("intro", "en", "Spoken intro", table="Dialogues_Act1"),
    ]
    out, _ = link_lines(lines, texts, target_lang="ru")
    assert out[0]["text"]["src"] == "Spoken intro"


def test_alt_takes_and_duplicates() -> None:
    lines = [
        line("l:0000", "dlg_x", table_key="Dialogue/dlg_x"),
        line("l:0001", "dlg_x_v1"),
        line("l:0002", "dlg_x_v2_BEST"),
        line("l:0003", "dlg_y"),
        line("l:0004", "dlg_y"),
        line("l:0005", "dlg_z_v1"),
        line("l:0006", "dlg_z_v2_best"),
    ]
    texts = [text("dlg_x", "en", "X"), text("dlg_y", "en", "Y"), text("dlg_z", "en", "Z")]
    out, summary = link_lines(lines, texts, target_lang="ru")
    flags = {r["entry"]["key"] + "@" + r["id"][-1]: r["flags"] for r in out}
    assert flags["dlg_x@0"] == []
    assert flags["dlg_x_v1@1"] == ["alt_take"] and flags["dlg_x_v2_BEST@2"] == ["alt_take"]
    assert flags["dlg_y@3"] == [] and flags["dlg_y@4"] == ["duplicate"]
    assert flags["dlg_z_v2_best@6"] == [] and flags["dlg_z_v1@5"] == ["alt_take"]
    assert summary["alt_takes"] == 4
    assert mark_alt_takes(out) == 0  # idempotent: flagged lines are not regrouped


def test_preserved_sources_are_not_overwritten() -> None:
    manual = line("l:0000", "zzz")
    manual["text"] = {
        "key": None,
        "src": "typed by a human",
        "src_lang": "en",
        "tgt_official": None,
        "source": "human",
    }
    out, summary = link_lines([manual], [text("other", "en", "x")], target_lang="ru")
    assert out[0]["text"]["src"] == "typed by a human"
    assert summary["by_match"] == {"kept": 1} and summary["unlinked"] == 0
