# Unity Localization (StringTable in Addressables)

Read this if `StreamingAssets/aa/<platform>/` has bundles `localization-string-tables-*`, `localization-assets-shared*`, `localization-locales*`.

## Level in BabelDub v0.1

- **Reading: yes (A).** The `unity-localization` adapter (via UnityPy) reads the strings of all languages into `inventory/texts.jsonl`, and `babeldub link` links them to voice lines and finds the official translation (`tgt_official`). MonoBehaviour fields are taken from the bundles' typetree (Addressables bundles usually have one, even with IL2CPP). If there is no typetree, the CLI tries the optional TypeTreeGeneratorAPI package; without it the asset is marked level C with a warning.
- **Writing into the game: no** (planned for v0.2): subtitles do not change in v0.1. A round trip of StringTable edits via UnityPy is verified on a test game; in-game behavior is not verified.

## How it works (per the `com.unity.localization` 1.5.13 sources)

- `LocalizationTable`: `m_LocaleId`, `m_SharedData` (reference to SharedTableData), `m_Metadata`, `m_TableData: List<TableEntryData>`.
- `TableEntryData`: `long m_Id`, `string m_Localized`, `m_Metadata`.
- `SharedTableData`: `m_TableCollectionName`, `m_TableCollectionNameGuidString`, `m_Entries: List<{long m_Id, string m_Key, m_Metadata}>`, `m_KeyGenerator`.
- Link: `SharedTableData.m_Entries` (`m_Id` → `m_Key`) + `StringTable.m_TableData` (`m_Id` → `m_Localized`) of the required locale (`m_LocaleId.m_Code`, for example `ru-RU`).
- String table bundles can be per language; the name may contain the **native language name in lowercase** rather than the locale code (in Low-Budget Repairs: `localization-string-tables-русский_assets_all.bundle`, `…-polski_…`, `…-english_…`). The keys are in the shared `localization-assets-shared_assets_all.bundle`.
- **Asset tables** (`localization-asset-tables-*`) localize assets (sprites, sometimes AudioClips or references).

## Link to the voice

A common case: the string key = the audio file or subsound name. In Low-Budget Repairs the Unity Localization string key = the FSB subsound name = the FMOD audio table key (1187 matches out of 1252 dialogue keys). Leftovers: duplicates `_vN`/`_BEST`, edit fragments, keys without audio.

## Smart Strings and markup

- Unity Smart Strings: `{0:plural:…}`, `{0:choose(1|2|3):…}`, nested scopes. For Russian, `plural` needs three forms.
- TMP/uGUI tags (`<color=…>`, `<b>`, `<i>`, `<size=…>`, `<sprite …>`, `<link=…>`) and the game's placeholders (in Low-Budget Repairs: `{ColorStart}{CharacterName}:{ColorEnd}`).
- All of this is preserved byte for byte in the subtitle and does not go into the spoken text (`dub_text`).

## If writing is needed (outside v0.1)

1. Find the StringTable of the required locale by `m_LocaleId.m_Code` and replace `m_Localized`.
2. UnityPy: `obj.patch(tree)` → `env.file.save(packer="lz4")`.
3. Zero the CRC of the changed bundle in the Addressables catalog (`AddressablesTools patchcrc`, see `engines/unity.md`).
4. Adding a new language is hard (a `Locale` asset, tables for every collection, catalog entries). Replacing the contents of an existing unneeded locale is more practical (not verified).
5. TextMeshPro fonts without Cyrillic will need a Font Asset replacement or a fallback.

## Links

- Unity Localization 1.5.13, Tables — https://github.com/needle-mirror/com.unity.localization/tree/1.5.13/Runtime/Tables
- Smart Strings — https://docs.unity3d.com/Packages/com.unity.localization@1.5/manual/Smart/SmartStrings.html
- UnityPy — https://github.com/K0lb3/UnityPy
- AddressablesTools — https://github.com/nesrak1/AddressablesTools
