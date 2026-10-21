"""Unity Localization string tables (Addressables / AssetBundles) read with UnityPy.

Unity Localization (com.unity.localization) stores, per table collection:
  - SharedTableData (one per collection): m_TableCollectionName, m_Entries [{m_Id, m_Key}]
  - StringTable (one per locale): m_LocaleId.m_Code, m_SharedData (PPtr), m_TableData [{m_Id, m_Localized}]
Rows are joined by m_Id. Bundles are usually named localization-string-tables-<language> and
localization-assets-shared, but any small bundle that contains these MonoBehaviours is accepted.

MonoBehaviour fields are decoded from the bundle TypeTree (Addressables bundles keep it even in
IL2CPP builds). Without a TypeTree the optional TypeTreeGeneratorAPI package is used to rebuild
it from GameAssembly.dll + global-metadata.dat (IL2CPP) or Managed/*.dll (Mono); if it is not
installed the asset is reported with an honest warning instead of guessing.

Writing tables back (and zeroing Addressables CRCs) is planned for v0.2; build() refuses.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from babeldub.adapters.base import Adapter, Asset
from babeldub.adapters.unity.unity_info import addressables_crc, data_dirs, unity_version
from babeldub.core.ids import asset_id
from babeldub.core.output import EXIT_ENV, EXIT_USAGE, BabelDubError
from babeldub.recon.langs import lang_of, locale_from_token

ADAPTER = "unity-localization"
NAME_HINTS = ("localization-string-tables", "localization-assets-shared")
PROBE_MAX_BYTES = 16 * 1024 * 1024  # content-probe only small bundles (tables are tiny)
PROBE_MAX_FILES = 300
_UNITYFS = b"UnityFS\x00"

STRING_TABLE = "StringTable"
ASSET_TABLE = "AssetTable"
SHARED_TABLE = "SharedTableData"


@dataclass
class TableObject:
    """A MonoBehaviour of interest inside a bundle."""

    kind: str  # StringTable | AssetTable | SharedTableData
    path_id: int
    data: dict[str, Any]


@dataclass
class BundleProbe:
    path: Path
    unity_version: str | None = None
    typetree: bool = True
    generator: str | None = None
    tables: list[TableObject] = field(default_factory=list)
    error: str | None = None

    @property
    def string_tables(self) -> list[TableObject]:
        return [t for t in self.tables if t.kind == STRING_TABLE]

    @property
    def shared_tables(self) -> list[TableObject]:
        return [t for t in self.tables if t.kind == SHARED_TABLE]


def _is_unityfs(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(8) == _UNITYFS
    except OSError:
        return False


def _candidate_bundles(game_dir: Path) -> list[Path]:
    """Localization bundles by name first, then small UnityFS bundles for a content probe."""
    named: list[Path] = []
    others: list[Path] = []
    for ddir in data_dirs(game_dir):
        streaming = ddir / "StreamingAssets"
        if not streaming.is_dir():
            continue
        for path in sorted(streaming.rglob("*")):
            if not path.is_file():
                continue
            lower = path.name.lower()
            if any(lower.startswith(h) for h in NAME_HINTS):
                named.append(path)
                continue
            if lower.startswith(("localization-asset-tables", "localization-locales")):
                continue  # asset tables and Locale objects carry no strings
            ext = path.suffix.lower()
            if (
                ext not in (".bundle", ".assetbundle", ".unity3d", ".ab", "")
                or len(others) >= PROBE_MAX_FILES
            ):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= PROBE_MAX_BYTES and _is_unityfs(path):
                others.append(path)
    return named + others


def _node_names(obj: Any) -> set[str] | None:
    stype = getattr(obj, "serialized_type", None)
    node = getattr(stype, "node", None) if stype is not None else None
    if node is None:
        return None
    return {child.m_Name for child in getattr(node, "m_Children", [])}


def _script_class(obj: Any) -> str | None:
    """MonoScript class name of a MonoBehaviour; works without the object's TypeTree."""
    try:
        head = obj.parse_monobehaviour_head()
        script = head.m_Script.deref_parse_as_object()
        return str(script.m_ClassName)
    except Exception:  # unresolvable script reference (external file) -> fall back to fields
        return None


def _kind_from_fields(names: set[str]) -> str | None:
    if {"m_TableCollectionName", "m_Entries"} <= names:
        return SHARED_TABLE
    if {"m_TableData", "m_LocaleId", "m_SharedData"} <= names:
        return STRING_TABLE  # AssetTable has the same layout; bundle names disambiguate below
    return None


def _make_generator(game_dir: Path, version: str | None) -> Any:
    """TypeTree generator for builds without TypeTree; None when the optional package is missing."""
    if not version:
        return None
    try:
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

        generator = TypeTreeGenerator(version)
        generator.load_local_game(str(game_dir))
        return generator
    except Exception:  # ImportError (TypeTreeGeneratorAPI not installed) or an unreadable build
        return None


def probe_bundle(path: Path, game_dir: Path | None = None, generator: Any = None) -> BundleProbe:
    """Classify and decode localization MonoBehaviours of one bundle."""
    import UnityPy

    probe = BundleProbe(path=path)
    try:
        env = UnityPy.load(str(path))
    except Exception as exc:
        probe.error = f"UnityPy cannot open bundle: {exc}"
        return probe
    for bundle in env.files.values():
        version = getattr(bundle, "version_engine", None)
        if version:
            probe.unity_version = str(version)
            break
    if generator is not None:
        env.typetree_generator = generator
        probe.generator = "TypeTreeGeneratorAPI"
    lower = path.name.lower()
    asset_tables_by_name = "asset-tables" in lower
    kind_by_name = (
        STRING_TABLE
        if lower.startswith(NAME_HINTS[0])
        else SHARED_TABLE
        if lower.startswith(NAME_HINTS[1])
        else None
    )
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        names = _node_names(obj)
        # TypeTree field names are free to read; the script is resolved only for table candidates
        # (StringTable and AssetTable share a layout) or when there is no TypeTree at all.
        kind = _kind_from_fields(names) if names else None
        if names is not None and kind is None:
            continue
        if kind != SHARED_TABLE:
            script = _script_class(obj)
            if script in (STRING_TABLE, ASSET_TABLE, SHARED_TABLE):
                kind = script
            elif kind == STRING_TABLE and asset_tables_by_name:
                kind = ASSET_TABLE
            elif kind is None and generator is None:
                kind = kind_by_name  # no TypeTree and no script: trust the bundle name
        if kind is None:
            continue
        if names is None and generator is None:
            probe.typetree = False
            probe.tables.append(TableObject(kind=kind, path_id=int(obj.path_id), data={}))
            continue
        try:
            data = obj.read_typetree()
        except Exception as exc:
            probe.typetree = False
            probe.error = f"TypeTree decode failed: {exc}"
            probe.tables.append(TableObject(kind=kind, path_id=int(obj.path_id), data={}))
            continue
        probe.tables.append(TableObject(kind=kind, path_id=int(obj.path_id), data=data))
    return probe


def rows_from_tables(
    string_tables: list[TableObject], shared: dict[int, TableObject], source: str = ADAPTER
) -> Iterator[dict[str, Any]]:
    """Join StringTables with their SharedTableData by m_Id -> texts.jsonl rows."""
    for table in string_tables:
        data = table.data
        if not data:
            continue
        locale_code = str((data.get("m_LocaleId") or {}).get("m_Code") or "") or None
        locale = locale_from_token(locale_code) or locale_code
        shared_ptr = data.get("m_SharedData") or {}
        sdata = shared.get(int(shared_ptr.get("m_PathID") or 0))
        keys: dict[int, str] = {}
        collection = None
        if sdata is not None and sdata.data:
            collection = sdata.data.get("m_TableCollectionName")
            keys = {int(e["m_Id"]): str(e["m_Key"]) for e in sdata.data.get("m_Entries", [])}
        if not collection:
            name = str(data.get("m_Name") or "")
            suffix = f"_{locale_code}" if locale_code else ""
            collection = name[: -len(suffix)] if suffix and name.endswith(suffix) else name
        for entry in data.get("m_TableData", []):
            text = entry.get("m_Localized")
            if text is None or text == "":
                continue
            entry_id = int(entry["m_Id"])
            key = keys.get(entry_id)
            if key is None and keys:
                continue  # orphan entry: its key was deleted from the collection, the game never shows it
            row: dict[str, Any] = {
                "key": key if key is not None else str(entry_id),
                "table": collection,
                "locale": locale,
                "lang": lang_of(locale),
                "text": text,
                "source": source,
                "entry_id": entry_id,
            }
            if key is None:
                row["key_missing"] = True
            yield row


class UnityLocalizationAdapter(Adapter):
    name = ADAPTER
    level = "A"
    description = "Unity Localization StringTables in Addressables bundles (read via UnityPy; write in v0.2)"

    def __init__(self) -> None:
        self._probes: dict[str, BundleProbe] = {}
        self._shared: dict[str, dict[int, TableObject]] = {}
        self._generators: dict[str, Any] = {}

    # ---- helpers -------------------------------------------------------
    def _generator(self, game_dir: Path) -> Any:
        key = str(game_dir.resolve())
        if key not in self._generators:
            version, _ = unity_version(game_dir)
            self._generators[key] = _make_generator(game_dir, version)
        return self._generators[key]

    def _probe(self, game_dir: Path, path: Path) -> BundleProbe:
        key = str(path.resolve())
        probe = self._probes.get(key)
        if probe is None:
            probe = probe_bundle(path, game_dir)
            if not probe.typetree:
                generator = self._generator(game_dir)
                if generator is not None:
                    probe = probe_bundle(path, game_dir, generator)
            self._probes[key] = probe
        return probe

    def _shared_tables(self, game_dir: Path) -> dict[int, TableObject]:
        key = str(game_dir.resolve())
        if key not in self._shared:
            shared: dict[int, TableObject] = {}
            for path in _candidate_bundles(game_dir):
                probe = self._probe(game_dir, path)
                for table in probe.shared_tables:
                    shared[table.path_id] = table
            self._shared[key] = shared
        return self._shared[key]

    # ---- Adapter API ---------------------------------------------------
    def discover(self, game_dir: Path) -> list[Asset]:
        assets: list[Asset] = []
        candidates = _candidate_bundles(game_dir)
        if not candidates:
            return assets
        crc_by_file = {r["file"]: r["crc"] for r in addressables_crc(game_dir) if r.get("file")}
        for path in candidates:
            probe = self._probe(game_dir, path)
            strings, shared = probe.string_tables, probe.shared_tables
            if not strings and not shared:
                continue
            rel = path.relative_to(game_dir).as_posix()
            locales = sorted(
                {str(t.data["m_LocaleId"].get("m_Code")) for t in strings if t.data.get("m_LocaleId")}
            )
            locale: str | None = None
            if len(locales) == 1:
                locale = locale_from_token(locales[0]) or locales[0]
            elif not locales and strings:
                tail = path.stem.split("localization-string-tables-")[-1].split("_assets")[0]
                locale = locale_from_token(tail)
            info: dict[str, Any] = {
                "kind": "string-tables"
                if strings and not shared
                else ("shared-table-data" if shared and not strings else "mixed"),
                "tables": len(strings),
                "collections": len(shared),
                "typetree": probe.typetree,
                "unity_version": probe.unity_version,
                "write": "v0.2",
            }
            if len(locales) > 1:
                info["locales"] = locales
            if probe.generator:
                info["typetree_source"] = probe.generator
            if rel in crc_by_file:
                info["addressables_crc"] = crc_by_file[rel]
            if not probe.typetree:
                info["warning"] = (
                    "bundle has no TypeTree for its MonoBehaviours (stripped IL2CPP build); install the optional "
                    "TypeTreeGeneratorAPI package into the babeldub environment to decode the tables"
                )
            elif probe.error:
                info["warning"] = probe.error
            assets.append(
                Asset(
                    asset_id=asset_id(self.name, rel),
                    adapter=self.name,
                    relpath=rel,
                    role="text",
                    size=path.stat().st_size,
                    locale=locale,
                    level="A" if probe.typetree else "C",
                    info=info,
                )
            )
        return assets

    def read_texts(self, game_dir: Path, asset: Asset) -> list[dict[str, Any]]:
        path = game_dir / asset.relpath
        if not path.is_file():
            raise BabelDubError(
                f"{asset.relpath} not found in the game folder",
                code=EXIT_USAGE,
                hint="re-run `babeldub scan`",
            )
        probe = self._probe(game_dir, path)
        if not probe.string_tables:
            return []
        if not probe.typetree:
            raise BabelDubError(
                f"{asset.relpath}: string tables have no TypeTree and no generator is available",
                code=EXIT_ENV,
                hint="install the optional package TypeTreeGeneratorAPI next to babeldub (pip/uv), then re-run extract",
            )
        shared = self._shared_tables(game_dir)
        return list(rows_from_tables(probe.string_tables, shared))

    def build(
        self,
        game_dir: Path,
        asset: Asset,
        replacements: dict[str, Path],
        out_path: Path,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        raise BabelDubError(
            "unity-localization: writing string tables is not part of v0.1 (read-only)",
            code=EXIT_USAGE,
            hint=(
                "v0.2 will patch StringTables with UnityPy and zero m_Crc in the Addressables catalog "
                "(same effect as `AddressablesTools patchcrc`); see skills/babeldub-pack/references/formats/unity-addressables.md"
            ),
        )
