"""Unity Localization adapter: table joining and adapter flow with synthetic table objects."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import pytest

from babeldub.adapters.unity import adapter as ul
from babeldub.adapters.unity.adapter import (
    SHARED_TABLE,
    STRING_TABLE,
    BundleProbe,
    TableObject,
    UnityLocalizationAdapter,
    rows_from_tables,
)
from babeldub.core.output import EXIT_ENV, EXIT_USAGE, BabelDubError


def shared(path_id: int, name: str, entries: dict[int, str]) -> TableObject:
    return TableObject(
        kind=SHARED_TABLE,
        path_id=path_id,
        data={
            "m_TableCollectionName": name,
            "m_Entries": [{"m_Id": i, "m_Key": k} for i, k in entries.items()],
        },
    )


def strings(
    path_id: int, locale: str, shared_id: int, values: dict[int, str], name: str = "T"
) -> TableObject:
    return TableObject(
        kind=STRING_TABLE,
        path_id=path_id,
        data={
            "m_Name": f"{name}_{locale}",
            "m_LocaleId": {"m_Code": locale},
            "m_SharedData": {"m_FileID": 1, "m_PathID": shared_id},
            "m_TableData": [{"m_Id": i, "m_Localized": v} for i, v in values.items()],
        },
    )


def test_rows_join_by_id_and_skip_orphans() -> None:
    sh = shared(-5, "Dialogues_Act_1", {1: "dlg_a", 2: "dlg_b"})
    ru = strings(10, "ru-RU", -5, {1: "Привет", 2: "", 3: "orphan of a deleted key"}, name="Dialogues_Act_1")
    rows = list(rows_from_tables([ru], {-5: sh}))
    assert rows == [
        {
            "key": "dlg_a",
            "table": "Dialogues_Act_1",
            "locale": "ru-RU",
            "lang": "ru",
            "text": "Привет",
            "source": "unity-localization",
            "entry_id": 1,
        }
    ]


def test_rows_without_shared_table_fall_back_to_ids() -> None:
    es = strings(11, "es_419", 99, {7: "Hola"}, name="UI")
    rows = list(rows_from_tables([es], {}))
    assert rows[0]["key"] == "7" and rows[0]["key_missing"] is True
    assert rows[0]["table"] == "UI" and rows[0]["locale"] == "es-419" and rows[0]["lang"] == "es"


def _game(tmp_path: Path) -> Path:
    data = tmp_path / "G_Data"
    data.mkdir(parents=True)
    (data / "globalgamemanagers").write_bytes(b"\0" * 0x30 + b"2022.3.1f1\0")
    aa = data / "StreamingAssets" / "aa" / "StandaloneWindows64"
    aa.mkdir(parents=True)
    for name in (
        "localization-assets-shared_assets_all.bundle",
        "localization-string-tables-english_assets_all.bundle",
        "localization-string-tables-русский_assets_all.bundle",
        "localization-asset-tables-english_assets_all.bundle",
        "localization-locales_assets_all.bundle",
    ):
        (aa / name).write_bytes(b"UnityFS\0" + b"\0" * 32)
    (aa / "other_assets_all_abcd.bundle").write_bytes(b"UnityFS\0" + b"\0" * 32)
    (aa / "not_unity.bundle").write_bytes(b"garbage")
    big = aa / "huge_assets_all.bundle"
    with big.open("wb") as fh:
        fh.write(b"UnityFS\0")
        fh.truncate(ul.PROBE_MAX_BYTES + 10)
    return tmp_path


def test_candidate_bundles(tmp_path: Path) -> None:
    game = _game(tmp_path)
    names = [p.name for p in ul._candidate_bundles(game)]
    assert names[:3] == [
        "localization-assets-shared_assets_all.bundle",
        "localization-string-tables-english_assets_all.bundle",
        "localization-string-tables-русский_assets_all.bundle",
    ]
    assert "other_assets_all_abcd.bundle" in names  # small UnityFS bundle: content probe
    assert "not_unity.bundle" not in names and "huge_assets_all.bundle" not in names
    assert not any("asset-tables" in n or "locales" in n for n in names)


@pytest.fixture()
def fake_probes(monkeypatch: pytest.MonkeyPatch) -> dict[str, BundleProbe]:
    sh = shared(-5, "Dialogues", {1: "dlg_hello", 2: "dlg_bye"})
    probes = {
        "localization-assets-shared_assets_all.bundle": BundleProbe(
            path=Path("s"), unity_version="2022.3.1f1", tables=[sh]
        ),
        "localization-string-tables-english_assets_all.bundle": BundleProbe(
            path=Path("e"),
            unity_version="2022.3.1f1",
            tables=[strings(10, "en-US", -5, {1: "Hello", 2: "Bye"})],
        ),
        "localization-string-tables-русский_assets_all.bundle": BundleProbe(
            path=Path("r"), unity_version="2022.3.1f1", tables=[strings(11, "ru-RU", -5, {1: "Привет"})]
        ),
        "other_assets_all_abcd.bundle": BundleProbe(path=Path("o")),
    }

    def fake_probe(path: Path, game_dir: Path | None = None, generator: Any = None) -> BundleProbe:
        return probes[path.name]

    monkeypatch.setattr(ul, "probe_bundle", fake_probe)
    return probes


def test_discover_and_read_texts(tmp_path: Path, fake_probes: dict[str, BundleProbe]) -> None:
    game = _game(tmp_path)
    adapter = UnityLocalizationAdapter()
    assets = adapter.discover(game)
    assert [a.locale for a in assets] == [None, "en-US", "ru-RU"]
    assert all(a.role == "text" and a.level == "A" for a in assets)
    assert assets[0].info["kind"] == "shared-table-data" and assets[1].info["kind"] == "string-tables"
    assert adapter.read_texts(game, assets[0]) == []  # keys only, merged into the locale tables
    en = adapter.read_texts(game, assets[1])
    ru = adapter.read_texts(game, assets[2])
    assert [(r["key"], r["text"]) for r in en] == [("dlg_hello", "Hello"), ("dlg_bye", "Bye")]
    assert [(r["key"], r["lang"], r["table"]) for r in ru] == [("dlg_hello", "ru", "Dialogues")]


def test_missing_typetree_is_reported_honestly(
    tmp_path: Path, fake_probes: dict[str, BundleProbe], monkeypatch: pytest.MonkeyPatch
) -> None:
    game = _game(tmp_path)
    probe = fake_probes["localization-string-tables-русский_assets_all.bundle"]
    probe.typetree = False
    for table in probe.tables:
        table.data = {}
    monkeypatch.setattr(ul, "_make_generator", lambda game_dir, version: None)
    adapter = UnityLocalizationAdapter()
    ru = next(a for a in adapter.discover(game) if "русский" in a.relpath)
    assert ru.level == "C" and ru.info["typetree"] is False and "TypeTreeGeneratorAPI" in ru.info["warning"]
    assert ru.locale == "ru"  # from the bundle name
    with pytest.raises(BabelDubError) as err:
        adapter.read_texts(game, ru)
    assert err.value.code == EXIT_ENV


def test_build_is_refused_with_a_plan(tmp_path: Path) -> None:
    adapter = UnityLocalizationAdapter()
    from babeldub.adapters.base import Asset

    asset = Asset(asset_id="unity-localization:x", adapter="unity-localization", relpath="x", role="text")
    with pytest.raises(BabelDubError) as err:
        adapter.build(tmp_path, asset, {}, tmp_path / "out", {})
    assert err.value.code == EXIT_USAGE
    assert "v0.2" in (err.value.hint or "") and "patchcrc" in (err.value.hint or "")


def test_probe_bundle_rejects_garbage(tmp_path: Path) -> None:
    path = tmp_path / "bad.bundle"
    path.write_bytes(b"UnityFS\0" + struct.pack(">I", 7) + b"\xff" * 64)
    probe = ul.probe_bundle(path)
    assert probe.tables == []
